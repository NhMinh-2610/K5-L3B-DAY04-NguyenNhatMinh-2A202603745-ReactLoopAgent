"""Kiểm tra nguồn trên snapshot, không gọi LLM API."""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent import run_agent
from status_tools import StatusTools
from test_lab import FakeProvider, final, tool_call


class SourceValidationRegressionTests(unittest.TestCase):
    def setUp(self):
        self.tools = StatusTools('snapshot')
        self.github_args = {'provider': 'github', 'incident_id': 'xr3myzj7ms51'}
        self.cloudflare_args = {'provider': 'cloudflare', 'incident_id': 'c60k3g4vsoc1'}
        self.github_url = self.tools.get_incident_details(**self.github_args)['url']
        self.cloudflare_url = self.tools.get_incident_details(**self.cloudflare_args)['url']

    def details_call(self, call_id, args):
        return tool_call(call_id, 'get_incident_details', json.dumps(args))

    def test_homepage_citation_does_not_replace_observed_incident_url(self):
        draft = final('Theo snapshot, Phoenix đang monitoring. Nguồn: https://www.cloudflarestatus.com')
        fake = FakeProvider([
            self.details_call('details', self.cloudflare_args),
            draft,
            final('Theo snapshot, Phoenix đang monitoring. ' + self.cloudflare_url),
        ])
        result = run_agent('Đối chiếu incident Spectrum ở Phoenix.', fake, self.tools, max_steps=3)
        self.assertEqual(result['status'], 'completed')
        self.assertIn(self.cloudflare_url, result['answer'])
        self.assertEqual(len(fake.histories), 3)
        self.assertEqual(len(result['usage']), 3)
        self.assertIs(fake.histories[2][-2], draft['message'])
        feedback = fake.histories[2][-1]
        self.assertEqual(feedback['role'], 'user')
        self.assertIn(self.cloudflare_url, feedback['content'])
        validations = [e for e in result['trace'] if e['event'] == 'validation_missing_source']
        self.assertEqual([e['step'] for e in validations], [2])

    def test_repeated_missing_sources_stop_at_model_call_budget(self):
        fake = FakeProvider([
            self.details_call('details', self.github_args),
            final('Incident đã xử lý, thiếu nguồn lần một.'),
            final('Incident đã xử lý, vẫn thiếu nguồn.'),
            final('Không được gọi tới câu trả lời này. ' + self.github_url),
        ])
        result = run_agent('Kiểm tra incident.', fake, self.tools, max_steps=3)
        self.assertEqual(result['status'], 'max_steps')
        self.assertEqual(result['answer'], '')
        self.assertEqual(len(fake.histories), 3)
        self.assertEqual(len(result['usage']), 3)
        self.assertEqual([e['step'] for e in result['trace'] if e['event'] == 'validation_missing_source'], [2, 3])
        self.assertEqual(result['trace'][-1], {'event': 'max_steps', 'step': 3})

    def test_either_observed_url_suffices_after_multiple_tool_calls(self):
        first = self.details_call('github', self.github_args)
        second = self.details_call('cloudflare', self.cloudflare_args)
        first['calls'] += second['calls']
        first['message']['tool_calls'] += second['message']['tool_calls']
        fake = FakeProvider([first, final('Incident GitHub đã xử lý. ' + self.github_url)])
        result = run_agent('Kiểm tra hai incident.', fake, self.tools, max_steps=2)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual([m['tool_call_id'] for m in fake.histories[1][-2:]], ['github', 'cloudflare'])
        self.assertFalse(any(e['event'] == 'validation_missing_source' for e in result['trace']))

    def test_failed_later_tool_call_keeps_previous_source_requirement(self):
        fake = FakeProvider([
            self.details_call('found', self.github_args),
            self.details_call('missing', {'provider': 'github', 'incident_id': 'missing'}),
            final('Một incident đã xử lý, incident còn lại không tìm thấy.'),
            final('Một incident đã xử lý; incident còn lại không tìm thấy. ' + self.github_url),
        ])
        result = run_agent('Kiểm tra incident.', fake, self.tools, max_steps=4)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(len(fake.histories), 4)
        self.assertFalse(json.loads(fake.histories[2][-1]['content'])['ok'])
        self.assertIn(self.github_url, result['answer'])
        self.assertEqual([e['step'] for e in result['trace'] if e['event'] == 'validation_missing_source'], [3])


if __name__ == '__main__':
    unittest.main()
