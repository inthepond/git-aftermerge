"""Tests for analyzer/scorer.py."""

from datetime import datetime

import pytest

from git_aftermerge.analyzer.scorer import compute_score
from git_aftermerge.storage.models import CommitFate, DownstreamEvent, EventType, Fate


def make_fate(**kwargs) -> CommitFate:
    defaults = dict(
        commit_sha="abc1234",
        author="test@test.com",
        author_tool=None,
        author_model=None,
        merged_at=datetime(2026, 1, 1),
        original_lines_added=100,
        original_lines_modified=0,
        surviving_lines=100,
        survival_score=100,
        fate=Fate.SURVIVED,
        days_since_merge=10,
        downstream_events=[],
        file_paths=["src/foo.py"],
        commit_type="feat",
        ai_attributed=False,
    )
    defaults.update(kwargs)
    return CommitFate(**defaults)


def test_perfect_survival():
    fate = make_fate(surviving_lines=100, original_lines_added=100)
    score = compute_score(fate)
    assert score == 100


def test_zero_original_lines():
    fate = make_fate(original_lines_added=0)
    assert compute_score(fate) == 100


def test_revert_within_24h_penalty():
    merged_at = datetime(2026, 1, 1, 0, 0, 0)
    revert_event = DownstreamEvent(
        event_type=EventType.REVERT_LINKED,
        commit_sha="rev123",
        date=datetime(2026, 1, 1, 12, 0, 0),  # 12 hours later
        lines_affected=50,
        commit_message="Revert something",
        author="human",
    )
    fate = make_fate(
        merged_at=merged_at,
        surviving_lines=50,
        original_lines_added=100,
        downstream_events=[revert_event],
    )
    score = compute_score(fate)
    # base=50, penalty=-20 → 30
    assert score == 30


def test_revert_within_72h_penalty():
    merged_at = datetime(2026, 1, 1, 0, 0, 0)
    revert_event = DownstreamEvent(
        event_type=EventType.REVERT_LINKED,
        commit_sha="rev123",
        date=datetime(2026, 1, 2, 12, 0, 0),  # 36 hours later
        lines_affected=50,
        commit_message="Revert something",
        author="human",
    )
    fate = make_fate(
        merged_at=merged_at,
        surviving_lines=100,
        original_lines_added=100,
        downstream_events=[revert_event],
    )
    score = compute_score(fate)
    # base=100, penalty=-10 → 90
    assert score == 90


def test_longevity_bonus():
    fate = make_fate(
        surviving_lines=100,
        original_lines_added=100,
        days_since_merge=35,
        downstream_events=[],
    )
    score = compute_score(fate)
    # base=100 + bonus=10, capped at 100
    assert score == 100


def test_bug_fix_penalty():
    bug_event = DownstreamEvent(
        event_type=EventType.BUG_FIX_LINKED,
        commit_sha="fix123",
        date=datetime(2026, 1, 5),
        lines_affected=10,
        commit_message="fix: something",
        author="human",
    )
    fate = make_fate(
        merged_at=datetime(2026, 1, 1),
        surviving_lines=100,
        original_lines_added=100,
        downstream_events=[bug_event],
    )
    score = compute_score(fate)
    # base=100, -5 for 1 bug fix = 95
    assert score == 95


def test_early_modification_penalty():
    mod_event = DownstreamEvent(
        event_type=EventType.MODIFICATION,
        commit_sha="mod123",
        date=datetime(2026, 1, 4),  # 3 days later
        lines_affected=60,  # > 50% of 100
        commit_message="fix something",
        author="human",
    )
    fate = make_fate(
        merged_at=datetime(2026, 1, 1),
        surviving_lines=40,
        original_lines_added=100,
        downstream_events=[mod_event],
    )
    score = compute_score(fate)
    # base=40, -10 for early mod = 30
    assert score == 30


def test_extension_bonus():
    ext_event = DownstreamEvent(
        event_type=EventType.MODIFICATION,
        commit_sha="ext123",
        date=datetime(2026, 1, 10),
        lines_affected=5,
        commit_message="feat: extend auth module",
        author="human",
    )
    fate = make_fate(
        merged_at=datetime(2026, 1, 1),
        surviving_lines=100,
        original_lines_added=100,
        downstream_events=[ext_event],
    )
    score = compute_score(fate)
    # base=100, +5 extension bonus, capped at 100
    assert score == 100


def test_score_clamped_to_zero():
    bug_events = [
        DownstreamEvent(
            event_type=EventType.BUG_FIX_LINKED,
            commit_sha=f"fix{i}",
            date=datetime(2026, 1, 5),
            lines_affected=10,
            commit_message="fix bug",
            author="human",
        )
        for i in range(30)
    ]
    fate = make_fate(
        merged_at=datetime(2026, 1, 1),
        surviving_lines=0,
        original_lines_added=100,
        downstream_events=bug_events,
    )
    score = compute_score(fate)
    assert score == 0


@pytest.mark.parametrize(
    "surviving, expected_min, expected_max",
    [
        (100, 95, 100),  # full survival
        (90, 85, 100),   # 90% — at SURVIVED threshold
        (89, 80, 95),    # just below 90%
        (50, 45, 55),    # half survival
        (30, 25, 35),    # at MODIFIED threshold
        (1, 0, 10),      # minimal survival
        (0, 0, 5),       # zero survival
    ],
)
def test_score_boundaries(surviving, expected_min, expected_max):
    fate = make_fate(surviving_lines=surviving, original_lines_added=100)
    score = compute_score(fate)
    assert expected_min <= score <= expected_max, (
        f"score={score} not in [{expected_min}, {expected_max}]"
    )


def test_multiple_bug_fixes_stack():
    bug_events = [
        DownstreamEvent(
            event_type=EventType.BUG_FIX_LINKED,
            commit_sha=f"fix{i}",
            date=datetime(2026, 1, 5),
            lines_affected=5,
            commit_message="fix bug",
            author="human",
        )
        for i in range(3)
    ]
    fate = make_fate(
        merged_at=datetime(2026, 1, 1),
        surviving_lines=100,
        original_lines_added=100,
        downstream_events=bug_events,
    )
    score = compute_score(fate)
    # base=100, -5 * 3 = -15 → 85
    assert score == 85
