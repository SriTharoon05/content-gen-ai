import unittest
from unittest.mock import patch

from app.agents import roles
from app.schemas import Beat, Premise, QAFinding, Script


class EditorialReviewTests(unittest.TestCase):
    def test_model_approval_cannot_bypass_writer_flagged_claim(self):
        premise = Premise(title_working='Display latency', premise_summary='Explain how a display produces light.',
                          archetype='explainer', theme_tags=['tech', 'science'],
                          plot_beat_summary='Explain the physical mechanism without inventing a breakthrough.')
        script = Script(hook_kind='question', hook='Why does a fast display still lag?',
                        beats=[Beat(shot_id='s001', narration='Why does a fast display still lag?')]
                              + [Beat(shot_id=f's{i:03d}', narration='A connected explanatory sentence continues.') for i in range(2, 7)],
                        flagged_claims=['A new neural screen removes all latency.'])
        with patch.object(roles, 'context', return_value=''), patch.object(roles, 'generate_model', return_value=QAFinding(passed=True)):
            result = roles.review({}, premise, script)
        self.assertFalse(result.passed)
        self.assertIn('neural screen', result.findings[0])


if __name__ == '__main__':
    unittest.main()
