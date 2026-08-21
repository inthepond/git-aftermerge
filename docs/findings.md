# Findings: AI Code Survival

*What happens to AI-written code after it merges?* We ran git-aftermerge across
open-source repositories that carry AI attribution in their commit history, plus
one anonymized private repo, and compared — **within each repo** — how long
AI-agent lines survive versus human lines.

**10 repositories analyzed · 21,905 commits scanned · 3,346 AI-attributed
(claude-code, openhands, gemini, cursor, aider, crush, copilot, amp, devin) ·
data collected August 2026.**

In the seven repos that pass the plausibility screen, the 30-day survival gap
(human − AI, percentage points) runs from **+32.4** down to **−10.0**: four
repos show AI-agent lines churning meaningfully faster than human lines,
aider sits at parity, and two (LibreChat, browser-use) tilt slightly the
other way. And the gap has a shape: it lives almost entirely in **brand-new
code** — see [the maturity signal](#the-maturity-signal).

## Survival curves, per repo

Each panel is one repository. Lines show the fraction of merged lines still
attributed to their commit N days later (`git blame -w`, histogram diff,
checkpoints at 7/30/90/180 days, right-censored).

![Survival curves by repo](assets/research/survival-curves-light.svg#only-light)
![Survival curves by repo](assets/research/survival-curves-dark.svg#only-dark)

## The gap, repo by repo

The headline number per repo: human 30-day survival minus AI 30-day survival,
in percentage points. Positive means AI-agent lines churned faster than human
lines *in that same repo*.

![Survival gap at day 30](assets/research/survival-gap-light.svg#only-light)
![Survival gap at day 30](assets/research/survival-gap-dark.svg#only-dark)

## Per-repo numbers

| Repo | Commits | AI commits | Tools | 30d AI | 30d human | 30d bot | Gap (pp) |
|---|---|---|---|---|---|---|---|
| [openhands](https://github.com/OpenHands/OpenHands) | 2788 | 1482 | openhands, claude-code | 55.8% (n=1195) | 72.0% (n=960) | 63.6% (n=79) | +16.2 |
| private-a | 984 | 535 | claude-code, cursor | 75.4% (n=530) | 87.2% (n=444) | — | +11.8 |
| [assistant-ui](https://github.com/assistant-ui/assistant-ui) | 2806 | 267 | claude-code, github-copilot | 35.3% (n=140) | 67.7% (n=1693) | 75.7% (n=159) | +32.4 |
| [gemini-cli](https://github.com/google-gemini/gemini-cli) | 2912 | 216 | gemini | 71.2% (n=204) | 93.8% (n=2574) | — | +22.5 |
| [lemmy](https://github.com/badlogic/lemmy) | 324 | 200 | claude-code | 25.3% (n=198) | 13.9% (n=117) | — | † |
| [aider](https://github.com/Aider-AI/aider) | 263 | 197 | aider, claude-code | 88.7% (n=185) | 87.8% (n=60) | — | -0.9 |
| [crush](https://github.com/charmbracelet/crush) | 3719 | 161 | crush, claude-code | 88.8% (n=115) | 13.0% (n=2943) | 36.7% (n=95) | † |
| [browser-use](https://github.com/browser-use/browser-use) | 2179 | 154 | claude-code, cursor | 60.6% (n=146) | 50.6% (n=1793) | — | -10.0 |
| [ghostty](https://github.com/ghostty-org/ghostty) | 3526 | 74 | claude-code, amp | 74.0% (n=62) | 15.0% (n=2722) | 44.0% (n=115) | † |
| [librechat](https://github.com/danny-avila/LibreChat) | 2404 | 60 | claude-code, github-copilot | 95.3% (n=43) | 90.6% (n=1905) | 98.7% (n=112) | -4.6 |

† tree-scale rewrite inside the analysis window (a >50pp swing between adjacent checkpoints) — blame lineage unreliable, excluded from the gap chart.

## How to read this

- **Where AI is a minority contributor, its code churns faster.** Four of the
  seven screened repos show a positive gap (+12 to +32 pp at day 30). The
  repos at or near parity (aider, LibreChat) are projects with
  long-established, review-heavy AI workflows.
- **The inversions are informative, not noise.** The repos where AI code
  *outlives* human code are AI-native projects where most development happens
  through an agent; there, "human" commits are often small manual fixes that the
  next agent pass rewrites.
- **The negative control holds.** Dependabot/Renovate commits (the `bot`
  cohort) are fully automated but deterministic. Where present in volume, bot
  lines survive *better* than both AI and human lines — automated-but-correct
  changes stick. If the detection logic were simply penalizing automation, the
  bot curve would look like the AI curve. It doesn't.

## The maturity signal

Every file change records how old the touched code was, bucketed into
tiers: **new** (< 30 days), **young** (30–365 days), **mature** (> 1 year).
Slicing the 30-day gap by tier shows where the AI-vs-human difference
actually lives:

| Repo | Gap, all commits | Gap, young-code commits only |
|---|---|---|
| assistant-ui | +32.4 | +2.3 (n=12 AI, 196 human) |
| openhands | +16.2 | −2.8 (n=164, 149) |
| gemini-cli | +22.5 | +15.6 (n=18, 235) |
| browser-use | −10.0 | −2.4 (n=9, 105) |
| librechat | −4.6 | −3.0 (n=6, 317) |
| aider | −0.9 | +1.4 (n=30, 9) |

In five of six repos with a young-code slice, the gap collapses to within
±3 pp once the commit touches code at least 30 days old — even in
assistant-ui, whose overall gap is +32. gemini-cli is the exception that
keeps a gap. Preliminary (the AI × young cells are small), but the shape is
consistent: **AI churn concentrates in freshly-written code being reworked
again and again; when an agent edits established code, its changes stick
about as well as a human's.** This is exactly why churn numbers without a
maturity layer are noise — healthy iteration on new features and AI breaking
mature logic would otherwise count as the same thing. The mature (> 1 year)
tier is still too thin to read; that needs older repos.

## Methodology

- **Within-repo comparison only.** Repos that accept AI-attributed commits skew
  young, small, and single-maintainer. Comparing their churn to other projects
  measures selection, not AI. Every number here compares cohorts *inside* one
  repo, holding project maturity, team, domain, and test culture constant.
  Across repos we only look at the distribution of the *difference*.
- **Attribution is trailer-based and conservative.** A commit counts as
  `ai-agent` only if it carries a machine-readable trace: `Co-Authored-By:
  Claude` (Claude Code's default), Cursor/Copilot/aider/Devin/OpenHands/Gemini
  identities, or the `Generated-By:` convention. AI use without trailers is
  invisible to us — cohort sizes are a **lower bound**, and repos where
  maintainers strip trailers show tiny AI cohorts regardless of actual AI use.
- **Survival = blame attribution.** A line "survives" while `git blame -w`
  (whitespace-insensitive, histogram diff) still attributes it to its commit.
  Checkpoints at 7/30/90/180/365 days after the *committer date* (when the
  change landed), observed only at repo states that contain the commit
  (`merge-base --is-ancestor`), so unmerged branch time counts as censoring,
  never as death.
- **Right-censoring, not extrapolation.** A commit contributes to a checkpoint
  only once it has aged past it. Each point shows its eligible cohort size
  (`n=`); points with n < 5 are dropped.
- **Plausibility screen.** A swing larger than 50 pp between adjacent
  checkpoints of a well-sampled cohort (n ≥ 30) means a tree-scale rewrite
  (mass reformat, restructure, rebrand) happened inside the window — blame
  lineage is unreliable there. Such repos (†) stay in the curve grid but are
  excluded from the single-number gap chart. This screen exists because our
  ground-truth validation kept finding that implausible curves trace back to
  history events, not to code quality.

## Caveats

- Cohort composition differs between checkpoints of the same curve (this is a
  per-checkpoint estimator, not Kaplan–Meier), so curves can wiggle where
  eligible sets change quickly.
- File renames break line lineage — renamed code reads as died.
- Nearly all analyzed changes touch *new* code (< 30 days old); the young-tier
  slices above are small, and the mature (> 1 year) tier is too thin to read
  until older repos join the sample.
- A revert of an AI commit authored by a human counts against the AI cohort,
  and vice versa — this is the intended semantics, but keep it in mind.

## Reproduce

```bash
git clone https://github.com/inthepond/git-aftermerge && cd git-aftermerge
pip install -e .

# any repo, no init needed — how much attributed history is there?
git-aftermerge attribution --repo /path/to/repo

# the batch study
python scripts/batch_survival.py config.json workdir results.json --jobs 4
python scripts/make_charts.py results.json docs/assets/research
```

The exact per-repo results behind these charts: [batch-results.json](data/batch-results.json).
Private repos appear as `private-*` with paths stripped.
