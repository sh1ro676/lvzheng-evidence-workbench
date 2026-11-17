"""Real stream/schools HTTP acceptance. Default: one paid statute question.

--all runs the fixed set; --check-config makes no provider calls. Reports stay
in ignored .validation/live/. Mechanical checks never certify legal quality.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))


def revision(root: Path = ROOT) -> dict:
    def git(*args):
        return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()
    return {'commit': git('rev-parse', 'HEAD'), 'dirty': bool(git('status', '--porcelain'))}


def select_cases(manifest: dict, selected: str | None = None) -> list[dict]:
    cases = {case['id']: case for case in manifest['cases']}
    if len(cases) != len(manifest['cases']):
        raise ValueError('测试问题编号重复')
    output, visiting, included = [], set(), set()

    def include(case_id):
        if case_id not in cases or case_id in visiting:
            raise ValueError('未知测试问题或上下文依赖成环')
        if case_id in included:
            return
        visiting.add(case_id)
        case = cases[case_id]
        if case.get('history_from'):
            include(case['history_from'])
        visiting.remove(case_id)
        included.add(case_id)
        output.append(case)
    for case_id in ([selected] if selected else cases):
        include(case_id)
    return output


def redact(value, secrets: list[str]):
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, '[REDACTED]')
        return value
    if isinstance(value, list):
        return [redact(item, secrets) for item in value]
    if isinstance(value, dict):
        return {key: redact(item, secrets) for key, item in value.items()}
    return value


class UpstreamAudit:
    """Observe real HTTP requests without recording authorization or bodies."""
    def __init__(self, app, limit: int):
        self.app, self.limit = app, limit
        self.events, self.case_id, self.lock = [], '', threading.Lock()

    def __enter__(self):
        self.original = urllib.request.urlopen
        self.pku_original = self.app.PKULAW.client.opener
        urllib.request.urlopen = self.observe(self.original)
        self.app.PKULAW.client.opener = self.observe(self.pku_original)
        return self

    def observe(self, opener):
        def call(request, *args, **kwargs):
            url = urllib.parse.urlsplit(request.full_url if hasattr(request, 'full_url') else str(request))
            with self.lock:
                if sum(event['case_id'] == self.case_id for event in self.events) >= self.limit:
                    raise urllib.error.URLError('验收的上游请求预算已用尽')
                self.events.append({'case_id': self.case_id, 'host': url.hostname,
                                    'path': url.path, 'at': time.monotonic()})
            return opener(request, *args, **kwargs)
        return call

    def __exit__(self, *_args):
        urllib.request.urlopen = self.original
        self.app.PKULAW.client.opener = self.pku_original


def post(port: int, path: str, body: dict, timeout: float = 360) -> tuple[dict, list[dict]]:
    """Total deadline includes headers and every streamed line."""
    deadline = time.monotonic() + timeout

    def remaining():
        seconds = deadline - time.monotonic()
        if seconds <= 0:
            raise TimeoutError('验收接口超过总等待时间')
        return seconds
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=remaining())
    try:
        connection.connect()
        sock = connection.sock
        sock.settimeout(remaining())
        data = json.dumps(body, ensure_ascii=False).encode('utf-8')
        connection.request('POST', path, body=data, headers={'Content-Type': 'application/json'})
        sock.settimeout(remaining())
        response = connection.getresponse()
        if response.status != 200:
            raise RuntimeError(f'验收 HTTP {response.status}')
        events, total = [], 0
        if path.endswith('/stream'):
            while True:
                sock.settimeout(remaining())
                line = response.readline(1_000_001)
                total += len(line)
                if len(line) > 1_000_000 or total > 8_000_000:
                    raise ValueError('验收响应超过大小限制')
                if not line:
                    break
                if line.startswith(b'data:'):
                    event = json.loads(line[5:])
                    events.append(event)
                    if event.get('error'):
                        raise RuntimeError(f"流式接口错误：{event['error']}")
                    if event.get('done'):
                        return event, events
            raise ValueError('流式接口未返回 done，不能算作验收成功')
        chunks = []
        while not response.isclosed():
            sock.settimeout(remaining())
            chunk = response.read1(65536)
            if not chunk:
                break
            total += len(chunk)
            if total > 8_000_000:
                raise ValueError('验收响应超过大小限制')
            chunks.append(chunk)
        return json.loads(b''.join(chunks)), events
    finally:
        connection.close()


def run_case(port: int, case: dict, prior: dict, audit: UpstreamAudit) -> dict:
    started = time.monotonic()
    audit.case_id = case['id']
    history = []
    if case.get('history_from'):
        previous = prior[case['history_from']]
        history = [{'role': 'user', 'content': previous['question']},
                   {'role': 'assistant', 'content': previous['main']['answer']}]
    request = {'question': case['question'], 'history': history, 'web_search': case['web_search']}
    main, events = post(port, '/api/chat/stream', request)
    schools = None
    if main.get('followup') == 'schools':
        schools, _ = post(port, '/api/schools', request, timeout=180)
    infos = [main.get('retrieval', {}).get('web_search', {})]
    if schools:
        infos.append(schools.get('retrieval', {}).get('web_search', {}))
    school_results = (schools or {}).get('retrieval', {}).get('web_search', {}).get('institution_results', [])
    expected_schools = {'中国政法大学', '西北政法大学', '北京大学法学院', '清华大学法学院'}
    checks = {
        'stream_completed': main.get('done') is True,
        'model_answer_available': main.get('status') not in {'model_unavailable', 'source_unavailable', 'error'},
        'citation_checks_passed': main.get('verification', {}).get('passed') is True,
        'requested_school_followup': not case['web_search'] or schools is not None,
        'four_school_states_returned': not case['web_search'] or {item['institution'] for item in school_results} == expected_schools,
        'web_search_tool_available': not case['web_search'] or any(
            event.get('stage') == 'search' and event.get('ok') for info in infos for event in info.get('events', [])),
        'school_transport_available': not schools or schools.get('status') not in {'source_unavailable', 'model_unavailable', 'error'},
        'school_citation_checks': not schools or not schools.get('section') or (schools.get('verification') or {}).get('passed') is True,
        'required_main_sources': len(main.get('evidence', [])) >= case.get('minimum_main_evidence', 0),
        'real_upstream_calls_observed': any(event['case_id'] == case['id'] for event in audit.events),
    }
    return {
        'id': case['id'], 'question': case['question'], 'web_search': case['web_search'],
        'history_from': case.get('history_from'), 'execution_passed': all(checks.values()), 'checks': checks,
        'main': main, 'schools': schools, 'stream_events': events,
        'metrics': {
            'elapsed_seconds': round(time.monotonic() - started, 3),
            'main_evidence': len(main.get('evidence', [])),
            'school_evidence': len((schools or {}).get('evidence', [])),
            'schools_hit': [item['institution'] for item in school_results if item.get('found')],
            'web_search_requests': sum(info.get('search_count', 0) for info in infos),
            'web_fetch_attempts': sum(info.get('fetch_attempt_count', 0) for info in infos),
            'upstream_http_requests': sum(event['case_id'] == case['id'] for event in audit.events),
        },
        'quality_review': {'status': 'pending_manual_review', 'items': [
            '正文是否实际回答问题', '所引资料是否主题相关', '身份关联是否准确', '引语及法律适用是否可靠',
        ]},
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    choose = parser.add_mutually_exclusive_group()
    choose.add_argument('--case', default=None, help='默认 statute；followup 会先执行前置问题')
    choose.add_argument('--all', action='store_true', help='执行全部固定问题，消耗更多真实 API 额度')
    parser.add_argument('--check-config', action='store_true', help='只检查配置，不调用上游')
    parser.add_argument('--label', default='current')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--project-root', type=Path, default=ROOT, help='可指向旧版工作树；验收脚本无需复制进旧仓库')
    parser.add_argument('--env-file', type=Path, help='可复用本机 .env，不复制或上传密钥')
    parser.add_argument('--max-upstream-requests', type=int, default=100)
    args = parser.parse_args(argv)
    if not 1 <= args.max_upstream_requests <= 500:
        parser.error('上游 HTTP 请求预算必须在 1–500 之间')
    manifest = json.loads((ROOT / 'validation' / 'live_cases.json').read_text(encoding='utf-8'))
    cases = select_cases(manifest, None if args.all else args.case or 'statute')
    if args.env_file:
        for raw in args.env_file.read_text(encoding='utf-8').splitlines():
            if raw.strip().startswith('#') or '=' not in raw:
                continue
            key, value = raw.split('=', 1)
            if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key.strip()):
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    project_root = args.project_root.resolve()
    sys.path.insert(0, str(project_root / 'backend'))
    import server as app  # Loads .env inside this process, never into the report.
    if Path(app.__file__).resolve().parent != project_root / 'backend':
        raise RuntimeError('加载的后端与指定工作树不一致，请用独立 Python 进程运行')
    configured = {'model': app.DEEPSEEK.configured, 'pkulaw': app.PKULAW.configured, 'web': app.WEB.configured}
    missing = sorted({name for case in cases for name in case['requires'] if not configured[name]})
    report = {'mode': 'live_api', 'label': args.label, 'revision': revision(project_root),
              'created_at': datetime.now(timezone.utc).isoformat(), 'configured': configured,
              'model': app.DEEPSEEK.model, 'cases_manifest': manifest,
              'planned_case_ids': [case['id'] for case in cases],
              'max_upstream_requests_per_case': args.max_upstream_requests,
              'case_set_hash': hashlib.sha256(json.dumps(cases, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
              'status': 'not_run', 'cases': [], 'upstream_requests': [], 'missing': missing}
    code = 2 if missing else 0
    if not missing and not args.check_config:
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        audit = UpstreamAudit(app, args.max_upstream_requests)
        try:
            prior = {}
            with audit:
                for case in cases:
                    result = run_case(httpd.server_port, case, prior, audit)
                    report['cases'].append(result)
                    prior[case['id']] = result
                    if not result['execution_passed']:
                        code = 1
                        break
            report['status'] = 'completed' if code == 0 else 'failed'
        except Exception as error:
            report['status'], report['error'], code = 'failed', str(error), 1
        finally:
            report['upstream_requests'] = audit.events
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)
    elif args.check_config and not missing:
        report['status'] = 'configuration_ready_not_run'
    report = redact(report, [app.DEEPSEEK.api_key, app.PKULAW.client.token])
    label = ''.join(c for c in args.label if c.isalnum() or c in '-_') or 'current'
    output = args.output or ROOT / '.validation' / 'live' / f'{label}-{time.time_ns()}.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'status': report['status'], 'missing': missing, 'report': str(output),
                      'completed_cases': len(report['cases']), 'legal_quality': '尚待独立人工核验'}, ensure_ascii=False))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
