"""Pair completed live records; evidence counts do not establish legal correctness."""
import argparse
import json
from pathlib import Path


def compare(before: dict, after: dict) -> dict:
    for report in (before, after):
        if report.get('mode') != 'live_api' or report.get('status') not in {'completed', 'failed'}:
            raise ValueError('只能比较实际执行的 live_api 报告，配置检查或模拟测试不可冒充实测')
        ids = [case['id'] for case in report.get('cases', [])]
        if len(ids) != len(set(ids)) or set(ids) != set(report.get('planned_case_ids', [])):
            raise ValueError('报告缺少计划内问题或编号重复，请先补齐对应实测')
    for field in ('case_set_hash', 'model', 'configured', 'max_upstream_requests_per_case'):
        if before.get(field) != after.get(field):
            raise ValueError(f'对比条件不一致：{field}')
    old = {case['id']: case for case in before['cases']}
    new = {case['id']: case for case in after['cases']}
    if set(old) != set(new):
        raise ValueError('两份报告的问题集合不同')
    rows = []
    numeric = ('elapsed_seconds', 'main_evidence', 'school_evidence',
               'web_search_requests', 'web_fetch_attempts', 'upstream_http_requests')
    for case_id, first in old.items():
        second = new[case_id]
        if any(first.get(key) != second.get(key) for key in ('question', 'web_search', 'history_from')):
            raise ValueError(f'问题或上下文条件不一致：{case_id}')
        a, b = first['metrics'], second['metrics']
        rows.append({'id': case_id, 'execution_before': first['execution_passed'],
                     'execution_after': second['execution_passed'],
                     'before': a, 'after': b,
                     'delta': {key: round(b[key] - a[key], 3) for key in numeric},
                     **{f'{key}_delta': len(b[key]) - len(a[key])
                        for key in ('schools_hit', 'schools_with_authors', 'schools_with_verified_authors')
                        if key in a and key in b}})
    return {'before_revision': before['revision'], 'after_revision': after['revision'],
            'case_set_hash': before['case_set_hash'], 'rows': rows,
            'criminal_directory_before': before.get('criminal_directory_enabled'),
            'criminal_directory_after': after.get('criminal_directory_enabled'),
            'legal_quality': 'pending_manual_review',
            'notes': ['新增证据或学校命中不等于正确命中，须核对原始出处。',
                      '联网资料及模型输出会波动，单次对比不证明稳定提升。',
                      'revision.dirty 为 true 时还包含未提交文件，不能仅凭提交 SHA 复现。']}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('before', type=Path)
    parser.add_argument('after', type=Path)
    parser.add_argument('--output', type=Path, default=Path('.validation/live/comparison.json'))
    args = parser.parse_args(argv)
    result = compare(json.loads(args.before.read_text(encoding='utf-8')),
                     json.loads(args.after.read_text(encoding='utf-8')))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'report': str(args.output), 'paired_cases': len(result['rows']),
                      'legal_quality': result['legal_quality']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
