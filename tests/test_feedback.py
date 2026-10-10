import unittest
from test_recommendation.catalog import CatalogError
from test_recommendation.feedback import adjust_score


class FeedbackTests(unittest.TestCase):
    def test_no_feedback_preserves_score(self):
        self.assertEqual(adjust_score(72, []), 72)

    def test_repeated_votes_cannot_exceed_cap(self):
        self.assertLessEqual(adjust_score(50, [1]*1000), 55)
        self.assertGreaterEqual(adjust_score(50, [-1]*1000), 45)
        self.assertEqual(adjust_score(50, [1, -1]), 50)

    def test_single_reviewer_is_damped_and_scores_bounded(self):
        self.assertEqual(adjust_score(50, [1]), 52.5)
        self.assertEqual(adjust_score(99, [1]), 100)
        self.assertEqual(adjust_score(1, [-1]), 0)

    def test_invalid_inputs_rejected(self):
        for score, votes, cap in [(101, [], 5), (float('nan'), [], 5), (50, [2], 5), (50, [], 6)]:
            with self.assertRaises(CatalogError):
                adjust_score(score, votes, cap=cap)
