"""What the evals page shows: each case with its expected result, the published
milestone run, any live run from the page, and the owner's verdict.

The page only displays these rows; the export (step 9) bakes the same rows into
the submission file.
"""

import json
from datetime import datetime
from pathlib import Path

from evals.run import CASES_DIR, PUBLISHED, load_cases

VERDICTS = Path(__file__).parent / "verdicts.json"
STORIES = Path(__file__).parent / "stories.json"
VERDICT_CHOICES = ("Pass", "Needs work", "Fail")


def evals_view(live_runs: dict | None = None) -> dict:
    live_runs = live_runs or {}
    published = json.loads(PUBLISHED.read_text()) if PUBLISHED.exists() else None
    verdicts = json.loads(VERDICTS.read_text()) if VERDICTS.exists() else {}
    rows = []
    for case in load_cases():
        runs = [r for r in (published or {}).get("runs", []) if r["case"] == case["id"]]
        shown = next((r for r in runs if not r["passed"]), runs[0] if runs else None)
        failing = [a for r in runs for a in r["assertions"] if not a["passed"]]
        rows.append({
            "id": case["id"],
            "type": case["type"],
            "title": case["title"],
            "expected": case.get("expected", ""),
            "email": case.get("email"),
            "question": case.get("question"),
            "instructions": (case.get("revision") or {}).get("instructions"),
            "setup": _setup(case),
            "published": {
                "runs": len(runs),
                "passed_runs": sum(r["passed"] for r in runs),
                "passed": bool(runs) and all(r["passed"] for r in runs),
                "actual": shown["actual"] if shown else None,
                "assertions": shown["assertions"] if shown else [],
                "failing": _unique(failing),
                "trail": shown["trail"] if shown else [],
                "messages": shown["messages"] if shown else [],
                "answer": shown.get("answer") if shown else None,
                "avg_cost_usd": round(sum(r["cost_usd"] for r in runs) / len(runs), 3) if runs else None,
                "avg_seconds": round(sum(r["seconds"] for r in runs) / len(runs), 1) if runs else None,
            } if runs else None,
            "live": live_runs.get(case["id"]),
            "verdict": verdicts.get(case["id"], {"verdict": None, "note": ""}),
        })
    summary = None
    if published:
        summary = {k: published.get(k) for k in ("run_at", "model", "effort", "prompt_version", "tools_version",
                                                  "chat_prompt_version", "repeat", "cases_passed", "cases",
                                                  "total_cost_usd")}
    stories = json.loads(STORIES.read_text()) if STORIES.exists() else {"note": "", "stories": []}
    return {"summary": summary, "rows": rows, "stories": stories, "verdict_choices": VERDICT_CHOICES}


def save_verdict(case_id: str, verdict: str | None, note: str) -> dict:
    if verdict is not None and verdict not in VERDICT_CHOICES:
        raise ValueError(f"Verdict must be one of {VERDICT_CHOICES}")
    if not (CASES_DIR / f"{case_id}.json").exists():
        raise ValueError(f"Unknown case {case_id}")
    verdicts = json.loads(VERDICTS.read_text()) if VERDICTS.exists() else {}
    verdicts[case_id] = {"verdict": verdict, "note": note.strip(), "at": datetime.now().isoformat(timespec="minutes")}
    VERDICTS.write_text(json.dumps(verdicts, indent=2, ensure_ascii=False) + "\n")
    return verdicts[case_id]


def _setup(case):
    notes = []
    if case.get("fixture"):
        notes.append(f"starts at: {case['fixture'].replace('-', ' ')}")
    if case.get("start"):
        notes.append("starts after: " + "; ".join(f"{c['op']} {c.get('leg_id') or c.get('service_id', '')}".strip()
                                                  for c in case["start"]))
    if case.get("clock"):
        notes.append(f"clock set to {case['clock']}")
    for key, value in (case.get("policies") or {}).items():
        notes.append(f"{key.replace('_', ' ')}: {'on' if value else 'off'}")
    return notes


def _unique(assertions):
    seen, out = set(), []
    for a in assertions:
        key = (a["type"], a["actual"])
        if key not in seen:
            seen.add(key)
            out.append(a)
    return out
