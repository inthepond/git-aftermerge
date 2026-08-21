"""Batch survival study driver.

Clones (or reuses) a set of repositories, runs the git-aftermerge pipeline on
each, and writes one aggregate JSON with per-repo cohort survival curves.

Usage:
    python scripts/batch_survival.py CONFIG.json WORKDIR RESULTS.json [--jobs N]

CONFIG.json is a list of entries:
    [
      {"source": "github:Aider-AI/aider", "label": "aider"},
      {"source": "local:/path/to/repo",   "label": "private-a"},
      {"source": "github:big/repo", "label": "big", "since": "2024-01-01"}
    ]

Labels are what appears in published results — use them to anonymize private
repos. Repos that fail the mix criteria (needs both AI-agent and human
commits) are recorded with "skipped" and a reason, never silently dropped.

Methodology guardrails baked in (do not remove):
- Within-repo comparison only; the output stores per-repo curves and the
  AI-minus-human gap, never a pooled cross-repo rate.
- The bot cohort (dependabot/renovate) rides along as a negative control.
- All diffing/blaming inherits histogram + whitespace flags from the library.
"""

import json
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

# Allow running from a source checkout without installation.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# Absolute cohort floors — shares are irrelevant: a 4% AI share in a 4,000
# commit repo is still a 160-commit cohort.
MIN_COMMITS = 100          # enough history for curves
MIN_AI = 30                # need a real AI cohort…
MIN_HUMAN = 30             # …and a real human cohort to compare against


def process_repo(entry: dict, workdir: str) -> dict:
    from collections import Counter

    from git_aftermerge.analyzer.attribution import attribute
    from git_aftermerge.analyzer.curves import compute_curves
    from git_aftermerge.analyzer.git_reader import GitReader
    from git_aftermerge.cli import _run_scan
    from git_aftermerge.storage.db import Database

    label = entry["label"]
    source = entry["source"]
    result: dict = {"label": label, "source_kind": source.split(":", 1)[0]}
    t0 = time.time()

    try:
        if source.startswith("github:"):
            name = source.split(":", 1)[1]
            result["github"] = name
            repo_path = Path(workdir) / label
            if not repo_path.exists():
                clone = subprocess.run(
                    ["git", "clone", "--quiet", f"https://github.com/{name}.git",
                     str(repo_path)],
                    capture_output=True, text=True, timeout=1800,
                )
                if clone.returncode != 0:
                    result["skipped"] = f"clone failed: {clone.stderr.strip()[:200]}"
                    return result
        elif source.startswith("local:"):
            repo_path = Path(source.split(":", 1)[1])
            if not (repo_path / ".git").exists():
                result["skipped"] = "not a git repository"
                return result
        else:
            result["skipped"] = f"unknown source scheme: {source}"
            return result

        reader = GitReader(repo_path)
        commits = reader.get_merge_commits(since=entry.get("since"))
        cohort_counts = Counter(
            attribute(c.author_name, c.author, c.body).cohort.value for c in commits
        )
        tool_counts = Counter(
            attribute(c.author_name, c.author, c.body).tool
            for c in commits
        )
        tool_counts.pop(None, None)
        total = len(commits)
        ai = cohort_counts.get("ai-agent", 0)
        human = cohort_counts.get("human", 0)
        result["commits"] = total
        result["cohorts"] = dict(cohort_counts)
        result["tools"] = dict(tool_counts)

        if total < MIN_COMMITS:
            result["skipped"] = f"only {total} commits (< {MIN_COMMITS})"
            return result
        if ai < MIN_AI or human < MIN_HUMAN:
            result["skipped"] = f"cohort too small (ai={ai}, human={human})"
            return result

        db_path = Path(workdir) / f"{label}.db"
        db = Database(db_path)
        with db:
            db.initialize_schema()
            scanned = _run_scan(repo_path, db, full=True, since=entry.get("since"))
            rows = db.get_curve_rows()

        def curves_to_dict(curves):
            return [
                {
                    "cohort": c.cohort,
                    "maturity": c.maturity,
                    "points": [
                        {"days": p.days, "rate": p.survival_rate,
                         "n": p.commit_count, "lines": p.lines_tracked}
                        for p in c.points
                    ],
                }
                for c in curves
            ]

        result["scanned"] = scanned
        result["curves_by_cohort"] = curves_to_dict(compute_curves(rows, by="cohort"))
        result["curves_by_cohort_maturity"] = curves_to_dict(
            compute_curves(rows, by="cohort+maturity")
        )
        result["maturity_mix"] = dict(Counter(r["maturity"] for r in rows))
        result["seconds"] = round(time.time() - t0, 1)
        return result
    except Exception as exc:  # record, never crash the batch
        result["skipped"] = f"error: {exc!r}"
        result["seconds"] = round(time.time() - t0, 1)
        return result


def main() -> None:
    if len(sys.argv) < 4:
        print(__doc__)
        raise SystemExit(1)
    config_path, workdir, out_path = sys.argv[1], sys.argv[2], sys.argv[3]
    jobs = 3
    if "--jobs" in sys.argv:
        jobs = int(sys.argv[sys.argv.index("--jobs") + 1])

    entries = json.loads(Path(config_path).read_text())
    Path(workdir).mkdir(parents=True, exist_ok=True)

    results = []
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        futures = {pool.submit(process_repo, e, workdir): e for e in entries}
        for future in as_completed(futures):
            r = future.result()
            status = r.get(
                "skipped", f"ok: {r.get('scanned', 0)} commits, {r.get('seconds', '?')}s"
            )
            print(f"[{r['label']}] {status}", flush=True)
            results.append(r)

    results.sort(key=lambda r: r["label"])
    Path(out_path).write_text(json.dumps(results, indent=2))
    ok = sum(1 for r in results if "skipped" not in r)
    print(f"\n{ok}/{len(results)} repos analyzed → {out_path}")


if __name__ == "__main__":
    main()
