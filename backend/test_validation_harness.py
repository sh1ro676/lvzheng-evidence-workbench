import copy
import json
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import compare_validation
import validate_agent_live as live
import server
from test_conversation import http_server
from test_server import StreamingClient


class ValidationHarnessTest(unittest.TestCase):
    def test_followup_selection_includes_real_preceding_question_and_rejects_cycles(self):
        manifest = {'cases': [{'id': 'first'}, {'id': 'next', 'history_from': 'first'}]}
        self.assertEqual([case['id'] for case in live.select_cases(manifest, 'next')], ['first', 'next'])
        with self.assertRaises(ValueError):
            live.select_cases({'cases': [{'id': 'a', 'history_from': 'a'}]}, 'a')
        with self.assertRaises(ValueError):
            live.select_cases(manifest, 'missing')

    def test_reports_redact_keys_before_json_escaping(self):
        secret = 'secret-"\n-sensitive'
        report = live.redact({'answer': secret, 'nested': [f'failed: {secret}']}, [secret])
        self.assertEqual(report['answer'], '[REDACTED]')
        self.assertNotIn('sensitive', json.dumps(report))

    def test_upstream_audit_enforces_budget_without_recording_credentials(self):
        app = SimpleNamespace(PKULAW=SimpleNamespace(client=SimpleNamespace(opener=lambda *_a, **_k: 'ok')))
        original = app.PKULAW.client.opener
        request = urllib.request.Request('https://example.com/path?token=private', headers={'Authorization': 'Bearer private'})
        with live.UpstreamAudit(app, 1) as audit:
            audit.case_id = 'one'
            self.assertEqual(app.PKULAW.client.opener(request), 'ok')
            with self.assertRaises(urllib.error.URLError):
                app.PKULAW.client.opener(request)
        self.assertIs(app.PKULAW.client.opener, original)
        self.assertEqual(audit.events[0]['path'], '/path')
        self.assertNotIn('private', str(audit.events))

    def test_real_http_stream_and_school_endpoints_are_both_covered(self):
        section = {'status': 'verified', 'section': '学校观点。【证据1】', 'evidence': [{'text': '正文'}],
                   'verification': {'passed': True}, 'retrieval': {'web_search': {
                       'search_count': 2, 'fetch_attempt_count': 3,
                       'institution_results': [{'institution': '中国政法大学', 'found': True}],
                   }}}
        with http_server() as base, patch.object(server, 'DEEPSEEK', StreamingClient()), \
             patch.object(server, 'route_question', return_value={'kind': 'concept'}), \
             patch.object(server, 'evidence_by_plan', return_value=([], {})), \
             patch.object(server, 'school_payload', return_value=section) as schools:
            audit = SimpleNamespace(case_id='', events=[])
            result = live.run_case(int(base.rsplit(':', 1)[1]),
                                   {'id': 'one', 'question': '问题', 'web_search': True}, {}, audit)
        schools.assert_called_once()
        self.assertEqual(result['metrics']['school_evidence'], 1)
        self.assertEqual(result['metrics']['web_search_requests'], 2)
        self.assertFalse(result['checks']['real_upstream_calls_observed'], 'fixture calls must not masquerade as live provider requests')
        self.assertFalse(result['execution_passed'])
        self.assertEqual(result['quality_review']['status'], 'pending_manual_review')

    def test_followup_requests_use_the_observed_preceding_answer(self):
        main = {'done': True, 'status': 'verified', 'answer': '回答', 'evidence': [],
                'verification': {'passed': True}, 'followup': None}
        prior = {'first': {'question': '前一问', 'main': {'answer': '真实上一轮返回文本'}}}
        with patch.object(live, 'post', return_value=(main, [])) as post:
            live.run_case(1, {'id': 'next', 'question': '追问', 'history_from': 'first', 'web_search': False},
                          prior, SimpleNamespace(case_id='', events=[]))
        history = post.call_args.args[2]['history']
        self.assertEqual(history[-1]['content'], '真实上一轮返回文本')

    def test_missing_configuration_is_not_reported_as_a_live_success(self):
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(server.DEEPSEEK, 'api_key', ''), \
             patch.object(server.WEB, 'api_key', ''), \
             patch.object(server.PKULAW.client, 'token', ''), \
             patch('urllib.request.urlopen', side_effect=AssertionError('must not contact providers')):
            output = Path(temp) / 'report.json'
            code = live.main(['--check-config', '--output', str(output)])
            report = json.loads(output.read_text(encoding='utf-8'))
        self.assertEqual(code, 2)
        self.assertEqual(report['status'], 'not_run')
        self.assertEqual(report['upstream_requests'], [])


def report():
    metrics = {'elapsed_seconds': 10, 'main_evidence': 1, 'school_evidence': 1,
               'web_search_requests': 4, 'web_fetch_attempts': 5, 'upstream_http_requests': 12,
               'schools_hit': ['中国政法大学']}
    return {'mode': 'live_api', 'status': 'completed', 'planned_case_ids': ['one'],
            'case_set_hash': 'fixed', 'model': 'same-model', 'configured': {'model': True},
            'max_upstream_requests_per_case': 100, 'revision': {'commit': 'sha', 'dirty': False},
            'cases': [{'id': 'one', 'question': '同一问', 'web_search': True, 'history_from': None,
                       'execution_passed': True, 'metrics': metrics}]}


class ValidationComparisonTest(unittest.TestCase):
    def test_numeric_gains_remain_pending_manual_quality_review(self):
        before, after = report(), report()
        after['cases'][0]['metrics']['school_evidence'] = 2
        result = compare_validation.compare(before, after)
        self.assertEqual(result['rows'][0]['delta']['school_evidence'], 1)
        self.assertEqual(result['legal_quality'], 'pending_manual_review')

    def test_mismatched_or_nonexecuted_reports_are_rejected(self):
        for mutate in (lambda r: r.update(mode='fixture_browser'), lambda r: r.update(status='not_run'),
                       lambda r: r.update(model='different'), lambda r: r.update(cases=[]),
                       lambda r: r['cases'][0].update(question='不同问题')):
            after = copy.deepcopy(report())
            mutate(after)
            with self.assertRaises(ValueError):
                compare_validation.compare(report(), after)
