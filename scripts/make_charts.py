"""Generate the survival-study SVG charts from batch_survival.py results.

Usage:
    python scripts/make_charts.py RESULTS.json OUTDIR

Emits, for light and dark mode each (GitHub READMEs switch via <picture>):
- survival-curves-{light,dark}.svg   — small-multiples: per-repo cohort curves
- survival-gap-{light,dark}.svg      — per-repo AI-vs-human gap at day 30

Design follows the repo's chart conventions: series colors are fixed per
cohort (never by rank), 2px lines with surface-ringed end markers, hairline
grids, text in ink tokens. Palette validated for CVD safety in both modes.
No pooled cross-repo rates anywhere — within-repo comparison only.
"""

import json
import math
import sys
from pathlib import Path

# Fixed cohort → color mapping (validated 3-slot categorical palette).
MODES = {
    "light": {
        "surface": "#fcfcfb",
        "ink": "#0b0b0b",
        "ink2": "#52514e",
        "muted": "#898781",
        "grid": "#e1e0d9",
        "axis": "#c3c2b7",
        "series": {"ai-agent": "#2a78d6", "human": "#eb6834", "bot": "#1baf7a"},
        "div_pos": "#d03b3b",   # AI churned more
        "div_neg": "#2a78d6",   # AI survived more
    },
    "dark": {
        "surface": "#1a1a19",
        "ink": "#ffffff",
        "ink2": "#c3c2b7",
        "muted": "#898781",
        "grid": "#2c2c2a",
        "axis": "#383835",
        "series": {"ai-agent": "#3987e5", "human": "#d95926", "bot": "#199e70"},
        "div_pos": "#e66767",
        "div_neg": "#3987e5",
    },
}

COHORT_LABELS = {"ai-agent": "AI agent", "human": "Human", "bot": "Bot (control)"}
FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'
X_MAX_DAYS = 180            # keep panels comparable; 365d values live in the table
X_TICKS = [0, 7, 30, 90, 180]
MIN_SERIES_N = 5            # draw a cohort line only with a real sample behind it


def x_scale(days: float, width: float) -> float:
    """sqrt-day axis: checkpoints are log-spaced; linear crushes 7/30 together."""
    return math.sqrt(days / X_MAX_DAYS) * width


def series_points(repo: dict, cohort: str) -> list[tuple[int, float, int]]:
    for curve in repo.get("curves_by_cohort", []):
        if curve["cohort"] == cohort:
            pts = [
                (p["days"], p["rate"], p["n"])
                for p in curve["points"]
                if p["days"] <= X_MAX_DAYS and p["n"] >= MIN_SERIES_N
            ]
            if len(pts) >= 2:
                return pts
    return []


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


UPHEAVAL_PP = 50    # adjacent-checkpoint swing that flags a repo
UPHEAVAL_MIN_N = 30


def has_upheaval(repo: dict) -> bool:
    """Plausibility screen: a >50pp swing between adjacent checkpoints of any
    well-sampled cohort means a tree-scale rewrite happened inside the window
    (mass reformat, rebrand, restructure). Blame lineage is unreliable there:
    the repo stays in the curve grid with a marker but is excluded from the
    single-number gap chart."""
    for curve in repo.get("curves_by_cohort", []):
        pts = [p for p in curve["points"] if p["n"] >= UPHEAVAL_MIN_N]
        for a, b in zip(pts, pts[1:]):
            if abs(b["rate"] - a["rate"]) * 100 > UPHEAVAL_PP:
                return True
    return False


def small_multiples(repos: list[dict], mode: str) -> str:
    m = MODES[mode]
    cols = 4
    panel_w, panel_h = 218, 150
    pad_x, pad_y = 16, 14
    plot_l, plot_r, plot_t, plot_b = 30, 12, 26, 20
    rows = math.ceil(len(repos) / cols)
    legend_h = 34
    footnote = any(has_upheaval(r) for r in repos)
    width = pad_x * 2 + cols * panel_w
    height = pad_y * 2 + legend_h + rows * panel_h + 18 + (13 if footnote else 0)

    y_min = 40.0
    for r in repos:
        for cohort in ("ai-agent", "human", "bot"):
            for _, rate, _ in series_points(r, cohort):
                y_min = min(y_min, rate * 100)
    y_floor = max(0, math.floor(y_min / 10) * 10)

    def sy(rate_pct: float, h: float) -> float:
        return h * (1 - (rate_pct - y_floor) / (100 - y_floor))

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family=\'{FONT}\'>',
        f'<rect width="{width}" height="{height}" fill="{m["surface"]}" rx="8"/>',
    ]

    lx = pad_x + 2
    out.append(
        f'<text x="{lx}" y="{pad_y + 12}" font-size="13" font-weight="600" '
        f'fill="{m["ink"]}">Line survival after merge — AI vs human, within each repo</text>'
    )
    ly = pad_y + legend_h - 6
    for cohort in ("ai-agent", "human", "bot"):
        color = m["series"][cohort]
        label = COHORT_LABELS[cohort]
        out.append(f'<circle cx="{lx + 5}" cy="{ly - 4}" r="5" fill="{color}"/>')
        out.append(
            f'<text x="{lx + 15}" y="{ly}" font-size="11" fill="{m["ink2"]}">{label}</text>'
        )
        lx += 15 + 8 * len(label) + 24

    for i, repo in enumerate(repos):
        col, row = i % cols, i // cols
        ox = pad_x + col * panel_w
        oy = pad_y + legend_h + row * panel_h
        w = panel_w - plot_l - plot_r
        h = panel_h - plot_t - plot_b

        name = repo["label"] + (" †" if has_upheaval(repo) else "")
        n_ai = repo["cohorts"].get("ai-agent", 0)
        out.append(
            f'<text x="{ox + plot_l}" y="{oy + 13}" font-size="11.5" font-weight="600" '
            f'fill="{m["ink"]}">{esc(name)}</text>'
        )
        out.append(
            f'<text x="{ox + panel_w - plot_r}" y="{oy + 13}" font-size="10" '
            f'text-anchor="end" fill="{m["muted"]}">{repo["commits"]}c · {n_ai} AI</text>'
        )

        gx, gy = ox + plot_l, oy + plot_t
        for pct in range(y_floor, 101, 20):
            yy = gy + sy(pct, h)
            out.append(
                f'<line x1="{gx}" y1="{yy:.1f}" x2="{gx + w}" y2="{yy:.1f}" '
                f'stroke="{m["grid"]}" stroke-width="1"/>'
            )
            out.append(
                f'<text x="{gx - 5}" y="{yy + 3.5:.1f}" font-size="9" text-anchor="end" '
                f'fill="{m["muted"]}">{pct}</text>'
            )
        for tick in X_TICKS:
            tx = gx + x_scale(tick, w)
            out.append(
                f'<text x="{tx:.1f}" y="{gy + h + 13}" font-size="9" text-anchor="middle" '
                f'fill="{m["muted"]}">{tick}</text>'
            )
        out.append(
            f'<line x1="{gx}" y1="{gy + h}" x2="{gx + w}" y2="{gy + h}" '
            f'stroke="{m["axis"]}" stroke-width="1"/>'
        )

        for cohort in ("bot", "human", "ai-agent"):  # draw AI last, on top
            pts = series_points(repo, cohort)
            if not pts:
                continue
            color = m["series"][cohort]
            path = " ".join(
                f'{"M" if j == 0 else "L"}{gx + x_scale(d, w):.1f},{gy + sy(rate * 100, h):.1f}'
                for j, (d, rate, _) in enumerate(pts)
            )
            out.append(
                f'<path d="{path}" fill="none" stroke="{color}" stroke-width="2" '
                f'stroke-linecap="round" stroke-linejoin="round"/>'
            )
            ex = gx + x_scale(pts[-1][0], w)
            ey = gy + sy(pts[-1][1] * 100, h)
            out.append(
                f'<circle cx="{ex:.1f}" cy="{ey:.1f}" r="6" fill="{m["surface"]}"/>'
                f'<circle cx="{ex:.1f}" cy="{ey:.1f}" r="4" fill="{color}"/>'
            )

    footer_y = height - (21 if footnote else 8)
    out.append(
        f'<text x="{pad_x + 2}" y="{footer_y}" font-size="9.5" fill="{m["muted"]}">'
        f'% of merged lines still attributed to their commit N days later (git blame -w, '
        f'histogram diff) · x-axis √days · within-repo comparison only</text>'
    )
    if footnote:
        out.append(
            f'<text x="{pad_x + 2}" y="{height - 8}" font-size="9.5" fill="{m["muted"]}">'
            f'† tree-scale rewrite inside the window — blame lineage unreliable there</text>'
        )
    out.append("</svg>")
    return "".join(out)


def gap_chart(repos: list[dict], mode: str, at_days: int = 30) -> str:
    m = MODES[mode]
    repos = [r for r in repos if not has_upheaval(r)]
    entries = []
    for r in repos:
        by_cohort = {}
        for cohort in ("ai-agent", "human"):
            for d, rate, n in series_points(r, cohort):
                if d == at_days:
                    by_cohort[cohort] = rate
        if len(by_cohort) == 2:
            gap = (by_cohort["human"] - by_cohort["ai-agent"]) * 100
            entries.append((r["label"], gap))
    entries.sort(key=lambda e: -e[1])

    row_h, bar_h = 30, 16
    pad, label_w, value_w = 16, 150, 60
    title_h, footer_h = 46, 26
    plot_w = 420
    width = pad * 2 + label_w + plot_w + value_w
    height = title_h + len(entries) * row_h + footer_h + pad

    max_abs = max(5.0, max(abs(g) for _, g in entries))
    zero_x = pad + label_w + plot_w / 2

    def bx(gap: float) -> float:
        return zero_x + (gap / max_abs) * (plot_w / 2 - 10)

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family=\'{FONT}\'>',
        f'<rect width="{width}" height="{height}" fill="{m["surface"]}" rx="8"/>',
        f'<text x="{pad + 2}" y="{pad + 8}" font-size="13" font-weight="600" '
        f'fill="{m["ink"]}">Survival gap at day {at_days}: human − AI (percentage points)</text>',
        f'<text x="{pad + 2}" y="{pad + 24}" font-size="10.5" fill="{m["ink2"]}">'
        f'Right of zero: AI-agent lines churned more than human lines in the same repo</text>',
        f'<line x1="{zero_x}" y1="{title_h - 4}" x2="{zero_x}" '
        f'y2="{title_h + len(entries) * row_h}" stroke="{m["axis"]}" stroke-width="1"/>',
    ]

    for i, (label, gap) in enumerate(entries):
        cy = title_h + i * row_h + row_h / 2
        color = m["div_pos"] if gap >= 0 else m["div_neg"]
        x0, x1 = sorted((zero_x, bx(gap)))
        bar_w = max(x1 - x0, 1.5)
        rx = min(4, bar_w / 2)
        out.append(
            f'<text x="{pad + label_w - 10}" y="{cy + 4:.1f}" font-size="11.5" '
            f'text-anchor="end" fill="{m["ink"]}">{esc(label)}</text>'
        )
        out.append(
            f'<rect x="{x0:.1f}" y="{cy - bar_h / 2:.1f}" width="{bar_w:.1f}" '
            f'height="{bar_h}" fill="{color}" rx="{rx:.1f}"/>'
        )
        tx = x1 + 8 if gap >= 0 else x0 - 8
        anchor = "start" if gap >= 0 else "end"
        out.append(
            f'<text x="{tx:.1f}" y="{cy + 4:.1f}" font-size="11" text-anchor="{anchor}" '
            f'fill="{m["ink2"]}">{gap:+.1f}</text>'
        )

    out.append(
        f'<text x="{pad + 2}" y="{height - 10}" font-size="9.5" fill="{m["muted"]}">'
        f'Per-repo, line-weighted; commits observed at day {at_days} only (right-censored). '
        f'Absolute values are not comparable across repos.</text>'
    )
    out.append("</svg>")
    return "".join(out)


def summary_table(repos: list[dict]) -> str:
    """Markdown table — the accessible data view behind the charts."""
    lines = [
        "| Repo | Commits | AI commits | Tools | 30d AI | 30d human | 30d bot | Gap (pp) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in repos:
        at30 = {}
        for cohort in ("ai-agent", "human", "bot"):
            for d, rate, n in series_points(r, cohort):
                if d == 30:
                    at30[cohort] = (rate, n)
        ai = at30.get("ai-agent")
        hu = at30.get("human")
        bot = at30.get("bot")
        if has_upheaval(r):
            gap = "†"
        elif ai and hu:
            gap = f"{(hu[0] - ai[0]) * 100:+.1f}"
        else:
            gap = "—"
        top_tools = sorted(
            ((t, n) for t, n in r["tools"].items() if t != "bot"),
            key=lambda kv: -kv[1],
        )[:2]
        tools = ", ".join(t for t, _ in top_tools) or "—"
        if r.get("github"):
            name = f"[{r['label']}](https://github.com/{r['github']})"
        else:
            name = r["label"]

        def cell(v):
            return f"{v[0] * 100:.1f}% (n={v[1]})" if v else "—"

        lines.append(
            f"| {name} | {r['commits']} | {r['cohorts'].get('ai-agent', 0)} | {tools} "
            f"| {cell(ai)} | {cell(hu)} | {cell(bot)} | {gap} |"
        )
    lines.append("")
    lines.append(
        "† tree-scale rewrite inside the analysis window (a >50pp swing between "
        "adjacent checkpoints) — blame lineage unreliable, excluded from the gap chart."
    )
    return "\n".join(lines)


def main() -> None:
    results_path, outdir = Path(sys.argv[1]), Path(sys.argv[2])
    outdir.mkdir(parents=True, exist_ok=True)
    results = json.loads(results_path.read_text())
    repos = [r for r in results if "skipped" not in r]
    repos.sort(key=lambda r: -r["cohorts"].get("ai-agent", 0))

    for mode in MODES:
        (outdir / f"survival-curves-{mode}.svg").write_text(small_multiples(repos, mode))
        (outdir / f"survival-gap-{mode}.svg").write_text(gap_chart(repos, mode))
    (outdir / "summary-table.md").write_text(summary_table(repos) + "\n")
    print(f"charts for {len(repos)} repos → {outdir}")


if __name__ == "__main__":
    main()
