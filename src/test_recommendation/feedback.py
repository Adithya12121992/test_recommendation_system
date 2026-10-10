"""Reviewed human feedback contributes at most five points to a 0–100 score."""
import math

from test_recommendation.catalog import require

FEEDBACK_CAP = 5.0


def adjust_score(technical_score, signals, *, cap=FEEDBACK_CAP):
    """Signals are deduplicated, approved, relevant votes: -1, 0, or +1.

    The caller must enforce revision and review-time availability. This function
    adjusts an existing candidate only; it never creates or removes candidates.
    """
    require(isinstance(technical_score, (int, float)) and math.isfinite(technical_score)
            and 0 <= technical_score <= 100, 'Technical score must be between 0 and 100')
    require(isinstance(cap, (int, float)) and math.isfinite(cap) and 0 <= cap <= FEEDBACK_CAP,
            'Feedback cap must be between 0 and 5 points')
    signals = list(signals)
    require(all(type(value) is int and value in (-1, 0, 1) for value in signals), 'Invalid feedback signal')
    # A prior neutral vote reduces the impact of a single reviewer.
    adjustment = cap * sum(signals) / (len(signals) + 1)
    return max(0.0, min(100.0, technical_score + adjustment))
