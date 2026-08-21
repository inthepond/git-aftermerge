"""Tests for analyzer/attribution.py."""

from git_aftermerge.analyzer.attribution import attribute, format_trailers
from git_aftermerge.analyzer.git_reader import GitReader
from git_aftermerge.storage.models import Cohort


def test_convention_trailers():
    msg = (
        "feat: add thing\n\n"
        "Generated-By: claude-code\n"
        "AI-Model: claude-sonnet-4-6\n"
        "AI-Session: 5e5b533b\n"
        "AI-Human-Ratio: 0.15\n"
    )
    attr = attribute("Dekko", "dekko@test.com", msg)
    assert attr.cohort == Cohort.AI_AGENT
    assert attr.tool == "claude-code"
    assert attr.model == "claude-sonnet-4-6"
    assert attr.session_id == "5e5b533b"
    assert attr.human_ratio == 0.15
    assert attr.source == "trailer"


def test_convention_trailer_roundtrip():
    block = format_trailers("claude-code", model="claude-fable-5", session_id="s1",
                            human_ratio=0.5)
    attr = attribute("Dekko", "dekko@test.com", f"feat: x\n\n{block}")
    assert attr.tool == "claude-code"
    assert attr.model == "claude-fable-5"
    assert attr.human_ratio == 0.5


def test_human_ratio_clamped():
    attr = attribute("D", "d@t.com", "x\n\nGenerated-By: aider\nAI-Human-Ratio: 3.7")
    assert attr.human_ratio == 1.0


def test_claude_co_author():
    msg = (
        "fix: handle edge case\n\n"
        "\U0001f916 Generated with [Claude Code](https://claude.com/claude-code)\n\n"
        "Co-Authored-By: Claude <noreply@anthropic.com>"
    )
    attr = attribute("Dekko", "dekko@test.com", msg)
    assert attr.cohort == Cohort.AI_AGENT
    assert attr.tool == "claude-code"
    assert attr.source == "co-author"


def test_claude_co_author_with_model():
    msg = "feat: y\n\nCo-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
    attr = attribute("Dekko", "dekko@test.com", msg)
    assert attr.tool == "claude-code"
    assert attr.model == "Opus 4.5"


def test_cursor_co_author():
    msg = "feat: z\n\nCo-authored-by: Cursor Agent <cursoragent@cursor.com>"
    attr = attribute("Dekko", "dekko@test.com", msg)
    assert attr.cohort == Cohort.AI_AGENT
    assert attr.tool == "cursor"


def test_bot_is_negative_control_not_ai():
    attr = attribute(
        "dependabot[bot]",
        "49699333+dependabot[bot]@users.noreply.github.com",
        "chore(deps): bump requests",
    )
    assert attr.cohort == Cohort.BOT
    attr2 = attribute("renovate[bot]", "bot@renovateapp.com", "chore: update deps")
    assert attr2.cohort == Cohort.BOT


def test_devin_and_copilot_authors():
    attr = attribute(
        "devin-ai-integration[bot]",
        "158243242+devin-ai-integration[bot]@users.noreply.github.com",
        "feat: x",
    )
    assert attr.cohort == Cohort.AI_AGENT
    assert attr.tool == "devin"

    attr2 = attribute(
        "Copilot", "198982749+Copilot@users.noreply.github.com", "fix: y"
    )
    assert attr2.cohort == Cohort.AI_AGENT
    assert attr2.tool == "github-copilot"


def test_aider_author_suffix():
    attr = attribute("Dekko (aider)", "dekko@test.com", "feat: add x")
    assert attr.cohort == Cohort.AI_AGENT
    assert attr.tool == "aider"


def test_plain_human():
    attr = attribute("Dekko Shi", "dekko@test.com", "feat: add thing\n\nDetails here.")
    assert attr.cohort == Cohort.HUMAN
    assert attr.tool is None
    assert attr.source == "none"


def test_human_named_claudette_not_ai():
    attr = attribute("Claudette Martin", "claudette.martin@company.com", "fix: typo")
    assert attr.cohort == Cohort.HUMAN


def test_backfill_from_real_history(ai_repo):
    """The detector recovers attribution already sitting in git history."""
    reader = GitReader(ai_repo["repo_dir"])
    commits = {c.sha: c for c in reader.get_merge_commits()}

    claude = commits[ai_repo["shas"]["claude"]]
    attr = attribute(claude.author_name, claude.author, claude.body)
    assert attr.cohort == Cohort.AI_AGENT
    assert attr.tool == "claude-code"

    bot = commits[ai_repo["shas"]["bot"]]
    attr_bot = attribute(bot.author_name, bot.author, bot.body)
    assert attr_bot.cohort == Cohort.BOT

    conv = commits[ai_repo["shas"]["convention"]]
    attr_conv = attribute(conv.author_name, conv.author, conv.body)
    assert attr_conv.source == "trailer"
    assert attr_conv.model == "claude-sonnet-4-6"
    assert attr_conv.human_ratio == 0.25

    human = commits[ai_repo["shas"]["human_rewrite"]]
    attr_h = attribute(human.author_name, human.author, human.body)
    assert attr_h.cohort == Cohort.HUMAN


def test_more_agent_co_authors():
    cases = [
        ("Co-authored-by: openhands <openhands@all-hands.dev>", "openhands"),
        ("Co-Authored-By: Crush <crush@charm.land>", "crush"),
        ("Co-authored-by: Amp <amp@ampcode.com>", "amp"),
        ("Co-Authored-By: Factory Droid <droid@factory.ai>", "droid"),
        ("Co-authored-by: gemini-code-assist[bot] <gemini@google.com>", "gemini"),
    ]
    for trailer, tool in cases:
        attr = attribute("Dev", "dev@test.com", f"feat: x\n\n{trailer}")
        assert attr.cohort == Cohort.AI_AGENT, trailer
        assert attr.tool == tool, trailer
