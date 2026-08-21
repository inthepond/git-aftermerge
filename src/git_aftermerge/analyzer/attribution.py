"""Commit attribution: who (or what) wrote this commit.

Two layers:

1. **Forward convention** — trailers this tool recommends agents/hooks write
   into every AI-assisted commit. Parsed with highest priority:

       Generated-By: claude-code
       AI-Model: claude-sonnet-4-6
       AI-Session: 5e5b533b
       AI-Human-Ratio: 0.15

   ``AI-Human-Ratio`` is the fraction of the final diff that was hand-edited
   by a human after generation (0.0 = fully machine-written).

2. **Backfill detection** — recovers attribution already sitting in history:
   Claude Code's ``Co-Authored-By: Claude`` trailer, Cursor / Copilot /
   aider / Devin traces, and bot accounts. Deterministic bots (Dependabot,
   Renovate…) are classified as their own ``bot`` cohort so they can serve
   as a negative control: they are fully automated but deterministic, so if
   bot commits show AI-like churn the detection logic is suspect.

Attribution is derived purely from the commit object (author + message),
so it is fact-grade: recomputable, never dependent on when the scan ran.
"""

import re
from dataclasses import dataclass
from typing import Optional

from git_aftermerge.storage.models import Cohort

# --- Forward trailer convention -------------------------------------------------

TRAILER_TOOL = "Generated-By"
TRAILER_MODEL = "AI-Model"
TRAILER_SESSION = "AI-Session"
TRAILER_HUMAN_RATIO = "AI-Human-Ratio"

_TRAILER_RE = re.compile(
    r"^(Generated-By|AI-Model|AI-Session|AI-Human-Ratio):[ \t]*(.+?)[ \t]*$",
    re.MULTILINE | re.IGNORECASE,
)

_CO_AUTHOR_RE = re.compile(
    r"^Co-Authored-By:[ \t]*(?P<name>[^<\n]+?)[ \t]*<(?P<email>[^>\n]*)>",
    re.MULTILINE | re.IGNORECASE,
)

# "Claude", optionally followed by a model name: "Co-Authored-By: Claude Opus 4.5 <…>"
_CLAUDE_NAME_RE = re.compile(r"^claude(?:\s+(?P<model>[\w .-]+))?$", re.IGNORECASE)


def format_trailers(
    tool: str,
    model: Optional[str] = None,
    session_id: Optional[str] = None,
    human_ratio: Optional[float] = None,
) -> str:
    """Render the recommended trailer block for an AI-assisted commit."""
    lines = [f"{TRAILER_TOOL}: {tool}"]
    if model:
        lines.append(f"{TRAILER_MODEL}: {model}")
    if session_id:
        lines.append(f"{TRAILER_SESSION}: {session_id}")
    if human_ratio is not None:
        lines.append(f"{TRAILER_HUMAN_RATIO}: {human_ratio:.2f}")
    return "\n".join(lines)


@dataclass
class Attribution:
    cohort: Cohort
    tool: Optional[str] = None       # claude-code, cursor, github-copilot, aider, …
    model: Optional[str] = None
    session_id: Optional[str] = None
    human_ratio: Optional[float] = None
    source: str = "none"             # trailer | co-author | author | message | none


# --- Backfill patterns ----------------------------------------------------------

# (pattern-on-author-name-or-email, tool). Checked case-insensitively.
_AI_AUTHOR_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"devin-ai-integration\[bot\]"), "devin"),
    (re.compile(r"copilot-swe-agent"), "github-copilot"),
    (re.compile(r"github-copilot"), "github-copilot"),
    (re.compile(r"^copilot@|\+copilot@users\.noreply\.github\.com"), "github-copilot"),
    (re.compile(r"cursoragent@cursor\.com|cursor.?agent"), "cursor"),
    (re.compile(r"\(aider\)|aider@|@aider\."), "aider"),
    (re.compile(r"google-labs-jules\[bot\]"), "jules"),
    (re.compile(r"gemini-code-assist"), "gemini"),
    (re.compile(r"openai-codex|codex\[bot\]"), "codex"),
    (re.compile(r"claude\[bot\]|claude-code@|@anthropic\.com"), "claude-code"),
    (re.compile(r"openhands@all-hands\.dev|openhands-agent"), "openhands"),
    (re.compile(r"crush@charm\.land"), "crush"),
    (re.compile(r"droid@factory\.ai"), "droid"),
]

# Deterministic automation — the negative control cohort.
_BOT_AUTHOR_RE = re.compile(
    r"dependabot|renovate(\[bot\]|-bot|@)|greenkeeper|github-actions\[bot\]"
    r"|pre-commit-ci|snyk-bot|imgbot|allcontributors|whitesource|mend\[bot\]",
    re.IGNORECASE,
)

# Message-body markers (outside trailers).
_MESSAGE_MARKERS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"generated with \[?claude code\]?", re.IGNORECASE), "claude-code"),
    (re.compile(r"^aider: ", re.IGNORECASE), "aider"),
    (re.compile(r"generated with \[?cursor\]?", re.IGNORECASE), "cursor"),
]

# Co-author email → tool.
_CO_AUTHOR_EMAIL_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"anthropic\.com", re.IGNORECASE), "claude-code"),
    (re.compile(r"cursor\.com", re.IGNORECASE), "cursor"),
    (re.compile(r"copilot|198982749", re.IGNORECASE), "github-copilot"),
    (re.compile(r"devin-ai", re.IGNORECASE), "devin"),
    (re.compile(r"aider", re.IGNORECASE), "aider"),
    (re.compile(r"chatgpt|openai\.com", re.IGNORECASE), "codex"),
    (re.compile(r"all-hands\.dev|openhands", re.IGNORECASE), "openhands"),
    (re.compile(r"crush@charm", re.IGNORECASE), "crush"),
    (re.compile(r"opencode", re.IGNORECASE), "opencode"),
    (re.compile(r"gemini", re.IGNORECASE), "gemini"),
    (re.compile(r"ampcode\.com", re.IGNORECASE), "amp"),
    (re.compile(r"factory\.ai", re.IGNORECASE), "droid"),
]


def _parse_convention_trailers(message: str) -> Optional[Attribution]:
    fields: dict[str, str] = {}
    for match in _TRAILER_RE.finditer(message):
        fields[match.group(1).lower()] = match.group(2)
    if TRAILER_TOOL.lower() not in fields:
        return None
    ratio: Optional[float] = None
    raw_ratio = fields.get(TRAILER_HUMAN_RATIO.lower())
    if raw_ratio:
        try:
            ratio = min(1.0, max(0.0, float(raw_ratio)))
        except ValueError:
            ratio = None
    return Attribution(
        cohort=Cohort.AI_AGENT,
        tool=fields[TRAILER_TOOL.lower()].lower(),
        model=fields.get(TRAILER_MODEL.lower()),
        session_id=fields.get(TRAILER_SESSION.lower()),
        human_ratio=ratio,
        source="trailer",
    )


def _detect_co_author(message: str) -> Optional[Attribution]:
    for match in _CO_AUTHOR_RE.finditer(message):
        name = match.group("name").strip()
        email = match.group("email").strip()
        claude = _CLAUDE_NAME_RE.match(name)
        if claude and ("anthropic" in email.lower() or not email):
            return Attribution(
                cohort=Cohort.AI_AGENT,
                tool="claude-code",
                model=(claude.group("model") or None),
                source="co-author",
            )
        for pattern, tool in _CO_AUTHOR_EMAIL_PATTERNS:
            if pattern.search(email) or pattern.search(name):
                return Attribution(cohort=Cohort.AI_AGENT, tool=tool, source="co-author")
    return None


def attribute(author_name: str, author_email: str, message: str) -> Attribution:
    """Attribute a commit to a cohort (ai-agent / bot / human) and tool.

    Priority: convention trailers > co-author trailers > author identity
    > message markers. Bots are checked on author identity only.
    """
    author_blob = f"{author_name} {author_email}".lower()

    from_trailer = _parse_convention_trailers(message)
    if from_trailer:
        return from_trailer

    from_co_author = _detect_co_author(message)
    if from_co_author:
        return from_co_author

    for pattern, tool in _AI_AUTHOR_PATTERNS:
        if pattern.search(author_blob):
            return Attribution(cohort=Cohort.AI_AGENT, tool=tool, source="author")

    if _BOT_AUTHOR_RE.search(author_blob):
        return Attribution(cohort=Cohort.BOT, tool="bot", source="author")

    for pattern, tool in _MESSAGE_MARKERS:
        if pattern.search(message):
            return Attribution(cohort=Cohort.AI_AGENT, tool=tool, source="message")

    return Attribution(cohort=Cohort.HUMAN, source="none")
