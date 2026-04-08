"""Tests for jittered retry backoff utilities."""

from agent.retry_utils import jittered_backoff


def test_jittered_backoff_stays_within_expected_attempt_one_bounds():
    delay = jittered_backoff(1, base_delay=5.0, max_delay=120.0, jitter_ratio=0.5)
    assert 5.0 <= delay <= 7.5


def test_jittered_backoff_scales_exponentially_before_cap():
    delay = jittered_backoff(3, base_delay=5.0, max_delay=120.0, jitter_ratio=0.5)
    assert 20.0 <= delay <= 30.0


def test_jittered_backoff_respects_max_cap_before_jitter():
    delay = jittered_backoff(20, base_delay=5.0, max_delay=60.0, jitter_ratio=0.5)
    assert 60.0 <= delay <= 90.0


def test_jittered_backoff_handles_non_positive_base_delay():
    delay = jittered_backoff(2, base_delay=0.0, max_delay=30.0, jitter_ratio=0.5)
    assert 30.0 <= delay <= 45.0
