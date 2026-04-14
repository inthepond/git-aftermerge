"""JSON export for git-aftermerge data."""

import json
from dataclasses import asdict
from datetime import datetime
from typing import Any

from git_aftermerge.storage.models import CommitFate, PatternReport, RiskyArea


def _serialize(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.isoformat()
    if hasattr(obj, "value"):
        return obj.value
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def commit_fate_to_dict(fate: CommitFate) -> dict:
    d = asdict(fate)
    # Convert enum values to strings
    d["fate"] = fate.fate.value
    d["merged_at"] = fate.merged_at.isoformat()
    for event in d["downstream_events"]:
        et = event["event_type"]
        event["event_type"] = et if isinstance(et, str) else et
        if isinstance(event.get("date"), datetime):
            event["date"] = event["date"].isoformat()
    return d


def pattern_report_to_dict(report: PatternReport) -> dict:
    d = asdict(report)
    d["analysis_window_start"] = report.analysis_window_start.isoformat()
    d["analysis_window_end"] = report.analysis_window_end.isoformat()
    return d


def risky_area_to_dict(area: RiskyArea) -> dict:
    return asdict(area)


def to_json(obj: Any, indent: int = 2) -> str:
    if isinstance(obj, CommitFate):
        data = commit_fate_to_dict(obj)
    elif isinstance(obj, PatternReport):
        data = pattern_report_to_dict(obj)
    elif isinstance(obj, list):
        data = []
        for item in obj:
            if isinstance(item, CommitFate):
                data.append(commit_fate_to_dict(item))
            elif isinstance(item, RiskyArea):
                data.append(risky_area_to_dict(item))
            else:
                data.append(asdict(item))
    else:
        data = asdict(obj)
    return json.dumps(data, default=_serialize, indent=indent)
