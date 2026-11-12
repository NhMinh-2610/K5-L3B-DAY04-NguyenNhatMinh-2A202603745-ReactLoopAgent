"""Kiểm tra luồng bonus trên snapshot và HTTP giả lập, không gọi LLM API."""
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent import MAX_CONTEXT_ITEMS, run_agent
from bonus.test_agent import ScriptedProvider, call, final
from bonus.tools import BonusTools, HOSTS


def batch(*responses):
    combined = responses[0]
    for response in responses[1:]:
        combined['calls'] += response['calls']
        combined['message']['tool_calls'] += response['message']['tool_calls']
    return combined


class ExtendedAgentTests(unittest.TestCase):
    def test_five_status_apis_return_all_results_before_next_model_call(self):
        first = batch(*(call(name, f'get_{name}_status', {}) for name in HOSTS))
        first['message']['reasoning_content'] = 'provider metadata'
        provider = ScriptedProvider([first, final('Đã tổng hợp năm nguồn theo snapshot.')])
        result = run_agent('Tổng hợp năm nhà cung cấp.', provider, BonusTools(), max_steps=2)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual({s['function']['name'] for s in provider.schemas[0]},
                         {f'get_{name}_status' for name in HOSTS} | {'get_incident_details'})
        self.assertIn('DigitalOcean', provider.histories[0][0]['content'])
        self.assertIs(provider.histories[1][2], first['message'])
        returned = provider.histories[1][3:]
        self.assertEqual([m['tool_call_id'] for m in returned], list(HOSTS))
        self.assertEqual({json.loads(m['content'])['provider'] for m in returned}, set(HOSTS))
        self.assertEqual(len(result['usage']), 2)

    def test_pagination_uses_actual_next_offset_and_preserves_remaining_pages(self):
        tools = BonusTools()
        args = {'provider': 'vercel', 'incident_id': 'sps6n39gm4sg'}
        first = tools.get_incident_details(**args)
        second = tools.get_incident_details(**args, offset=first['next_offset'])
        self.assertTrue(any('github' in u['body'].casefold() for u in second['updates']))
        provider = ScriptedProvider([
            call('page1', 'get_incident_details', args),
            call('page2', 'get_incident_details', {**args, 'offset': first['next_offset']}),
            final('Trang đã đọc nhắc GitHub; timeline còn trang chưa đọc. ' + first['url']),
        ])
        result = run_agent('Đọc update nhắc GitHub.', provider, tools, max_steps=3)
        self.assertEqual(result['status'], 'completed')
        sent_first = json.loads(provider.histories[1][-1]['content'])
        sent_second = json.loads(provider.histories[2][-1]['content'])
        self.assertEqual(sent_first['next_offset'], 2)
        self.assertEqual(sent_second['offset'], first['next_offset'])
        self.assertEqual(sent_second['next_offset'], 4)
        self.assertEqual(sent_second['total_updates'], first['total_updates'])
        self.assertTrue(set(u['created_at'] for u in sent_first['updates']).isdisjoint(
            u['created_at'] for u in sent_second['updates']))

    def test_persistent_429_stops_execution_after_three_attempts_and_keeps_other_provider(self):
        tools = BonusTools(scenario='rate_limit_always')
        provider = ScriptedProvider([
            call('one', 'get_vercel_status', {}),
            call('two', 'get_vercel_status', {'component': 'Builds'}),
            call('three', 'get_vercel_status', {'component': 'Dashboard'}),
            batch(call('blocked', 'get_vercel_status', {'component': 'Another', 'provider': 'ignored'}),
                  call('github', 'get_github_status', {})),
            final('Chưa kiểm tra được Vercel do rate limit; GitHub có dữ liệu. ' + HOSTS['github']),
        ])
        result = run_agent('Thử Vercel tối đa ba lần và kiểm tra GitHub.', provider, tools, max_steps=5)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(tools.calls[('get_vercel_status', 'vercel')], 3)
        self.assertEqual(tools.calls[('get_github_status', 'github')], 1)
        limited = [e for e in result['trace'] if e.get('event') == 'tool' and e['name'] == 'get_vercel_status']
        self.assertEqual(len(limited), 4)  # Trace vẫn ghi cả yêu cầu bị chặn.
        self.assertEqual([e['executed'] for e in limited], [True, True, True, False])
        self.assertFalse(limited[2]['result']['retriable'])
        self.assertTrue(any(e['event'] == 'tool_retry_limit' for e in result['trace']))
        self.assertTrue(any(e['event'] == 'tool_retry_blocked' for e in result['trace']))

    def test_live_retry_after_defers_retry_without_second_http_request(self):
        for header in ['2', 'Wed, 21 Oct 2037 07:28:00 GMT', None]:
            with self.subTest(retry_after=header):
                headers = {'Retry-After': header} if header is not None else {}
                error = urllib.error.HTTPError('https://example.invalid', 429, 'rate limited', headers, io.BytesIO(b''))
                tools = BonusTools(mode='live')
                provider = ScriptedProvider([
                    call('first', 'get_vercel_status', {}),
                    call('blocked', 'get_vercel_status', {}),
                    final('Cần chờ trước khi kiểm tra lại; chưa biết trạng thái Vercel.'),
                ])
                try:
                    with patch('bonus.tools.urllib.request.urlopen', side_effect=error) as request:
                        result = run_agent('Kiểm tra Vercel.', provider, tools, max_steps=3)
                    self.assertEqual(result['status'], 'completed')
                    self.assertEqual(request.call_count, 1)
                    observation = json.loads(provider.histories[1][-1]['content'])
                    self.assertEqual(observation['retry_after'], header)
                    self.assertFalse(observation['retriable'])
                    self.assertTrue(any(e['event'] == 'tool_retry_deferred' for e in result['trace']))
                finally:
                    error.close()

    def test_context_is_bounded_but_trace_and_incident_evidence_are_preserved(self):
        tools = BonusTools(scenario='noisy')
        provider = ScriptedProvider([
            call('status', 'get_vercel_status', {'component': 'Builds'}),
            final('Theo snapshot, Vercel có nguồn trạng thái: ' + HOSTS['vercel']),
        ])
        result = run_agent('Kiểm tra Vercel Builds.', provider, tools, max_steps=2)
        self.assertEqual(result['status'], 'completed')
        raw = next(e['result'] for e in result['trace'] if e.get('event') == 'tool')
        sent = json.loads(provider.histories[1][-1]['content'])
        self.assertEqual(len(raw['distractors']), 160)
        self.assertNotIn('agent_context', raw)
        self.assertEqual(len(sent['distractors']), MAX_CONTEXT_ITEMS)
        self.assertEqual(sent['agent_context']['truncated_lists']['distractors']['total'], 160)
        for key in ['provider', 'overall', 'captured_at', 'incidents', 'source']:
            self.assertEqual(sent[key], raw[key])
        self.assertTrue(any(e['event'] == 'context_trimmed' for e in result['trace']))

    def test_simulated_format_error_returns_to_model_before_correct_retry(self):
        tools = BonusTools(scenario='format_once')
        args = {'provider': 'npm', 'incident_id': 'cc0t2r4403sy', 'offset': 0, 'limit': 2}
        url = tools.get_incident_details(**args)['url']
        provider = ScriptedProvider([
            call('first', 'get_incident_details', args),
            call('fixed', 'get_incident_details', args),
            final('Theo dữ liệu đã đọc, incident đã resolved. ' + url),
        ])
        result = run_agent('Đọc incident npm.', provider, tools, max_steps=3)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(json.loads(provider.histories[1][-1]['content'])['error'], 'INVALID_ARGUMENTS')
        self.assertTrue(json.loads(provider.histories[2][-1]['content'])['ok'])
        self.assertEqual(tools.calls[('get_incident_details', 'npm')], 2)


if __name__ == '__main__':
    unittest.main()
