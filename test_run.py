"""Regression checks for the fixed offline opportunity-scan scorecard."""
import json
from pathlib import Path
import unittest

import run


class ScorecardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = run.scorecard()
        cls.cases = json.loads((Path(__file__).parent/'cases.json').read_text())['cases']

    def test_same_fixture_and_reset_starting_state(self):
        result = self.result
        self.assertEqual(result['execution']['starting_state'],
                         'new in-memory SQLite from identical case priors per config')
        self.assertEqual(result['baseline']['planned_cases'],len(self.cases))
        self.assertEqual(result['candidate']['planned_cases'],len(self.cases))
        self.assertEqual(result['execution']['crm_file_writes'],0)
        self.assertEqual(result['fixture_binding']['sha256'],run.sha((Path(__file__).parent/'cases.json').read_bytes()))

    def test_known_relevant_and_adversarial_cases_are_not_lost(self):
        for key in ('baseline','candidate'):
            routes = self.result[key]['routes']
            by_id = {x['case']:x for x in routes}
            self.assertEqual(by_id['reddit-logistics']['route'],'review')
            self.assertEqual(by_id['oneseven-caio']['route'],'review')
            self.assertEqual(by_id['injected-buyer']['route'],'review')
            self.assertEqual(by_id['incomplete']['route'],'read_original')
            self.assertEqual(by_id['changed-version']['route'],'review')
            self.assertEqual(by_id['new-reply']['route'],'review')
            self.assertEqual(by_id['different-buyer-b']['route'],'review')
            self.assertEqual(by_id['different-buyer-b']['possible_crosspost_of'],'different-buyer-a')
            self.assertEqual(self.result[key]['forbidden_action_errors'],[])
            self.assertEqual(self.result[key]['duplicate_suppression'],1)
            self.assertEqual(self.result[key]['unchanged_suppression'],1)
            self.assertEqual(self.result[key]['originals_needed'],1)

    def test_config_comparison_and_explicit_limits(self):
        self.assertEqual(self.result['baseline']['deferred'],2)
        self.assertEqual(self.result['candidate']['deferred'],0)
        self.assertEqual(self.result['regressions'],[])
        self.assertIsNone(self.result['candidate']['model_task_success'])
        self.assertIsNone(self.result['candidate']['model_cost_usd'])
        self.assertEqual(self.result['status']['semantic_extraction'],'not_reached')
        self.assertEqual(self.result['status']['live_coverage'],'not_reached')
        self.assertTrue(all(x['metadata_due'] for x in self.result['source_plan']))


if __name__=='__main__':unittest.main()
