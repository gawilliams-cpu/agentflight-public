"""Deterministic tools: read tools and checks.

Every check returns the same shape, so the agent, the activity feed, and the
review package can all show it the same way:

  {"check": "crew_duty", "result": "pass" or "fail",
   "summary": "one or more plain-English lines", "details": {...the numbers...}}
"""


def check_result(check: str, passed: bool, summary_lines: list, **details) -> dict:
    return {
        "check": check,
        "result": "pass" if passed else "fail",
        "summary": "\n".join(summary_lines),
        "details": details,
    }
