"""Run the agent over the eval cases and report what passed and failed.

    uv run python -m evals.run                    every case, once
    uv run python -m evals.run --case 4 --case 5b only these cases
    uv run python -m evals.run --repeat 3 --publish   a milestone, shown on the evals page

Each run starts from fresh base data (plus the case's starting changes) and
goes through the same receive() as every other input, including the sends that
need no approval. A case passes only if
every run passes every assertion. Results go to evals/results/ as JSON (full
detail, including each run's trace) and Markdown (the readable report).
"""

import argparse
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from agentflight import chat, config
from agentflight.store import load_state
from agentflight.whatif import apply_changes
from agentflight.intake import intake
from agentflight.workflow import finish_change, receive, start_change
from evals.fixtures import load_fixture
from evals.grading import grade
from agentflight.agent.package import _status as package_status

CASES_DIR = Path(__file__).parent / "cases"
RESULTS_DIR = Path(__file__).parent / "results"
PUBLISHED = Path(__file__).parent / "published.json"   # the milestone run shown on the evals page
PARALLEL_RUNS = 4


def load_cases(ids=None):
    cases = [json.loads(path.read_text()) for path in sorted(CASES_DIR.glob("*.json"))]
    if ids:
        unknown = set(ids) - {c["id"] for c in cases}
        if unknown:
            raise SystemExit(f"Unknown case id(s): {', '.join(sorted(unknown))}")
        cases = [c for c in cases if c["id"] in ids]
    return cases


def run_chat_case_once(case, client=None):
    """A trip chat case: one question about a trip, starting from a saved moment."""
    state = load_fixture(load_state(), case["fixture"]) if case.get("fixture") else load_state()
    before = copy.deepcopy(state)
    try:
        result = chat.ask(state, case["trip"], case["question"], client=client)
    except Exception as error:
        return {"case": case["id"], "error": repr(error), "passed": False, "assertions": [],
                "cost_usd": 0, "seconds": 0}
    record = {"chat": result, "state_changed": state != before, "sent": state["outbox"][len(before["outbox"]):]}
    assertions = grade(case, record)
    return {
        "case": case["id"], "passed": all(a["passed"] for a in assertions), "assertions": assertions,
        "status": "Chat answer", "actual": describe_chat(result),
        "cost_usd": result["cost_usd"], "seconds": result["seconds"], "record": record,
    }


def describe_chat(result):
    checked = [t for t in dict.fromkeys(result["tools_used"]) if t != "suggest_revision"]
    text = " ".join(result["answer"].split())
    line = f"Answer: “{text[:220]}{'…' if len(text) > 220 else ''}”"
    if checked:
        line += f" · ran {', '.join(checked)}"
    if result.get("suggestion"):
        line += " · suggested a revision (not applied)"
    return line


def run_case_once(case, client=None):
    if case.get("kind") == "chat":
        return run_chat_case_once(case, client)
    base = load_fixture(load_state(), case["fixture"]) if case.get("fixture") else load_state()
    state = apply_changes(base, case.get("start", []))
    if "clock" in case:
        state["clock"]["now"] = case["clock"]
    state["policies"].update(case.get("policies", {}))
    email = case["email"]
    try:
        if "revision" in case:  # the trip manager rejected an earlier proposal and gave instructions
            change = start_change(state, "revision", email["from"], email["subject"])
            record = intake(state, "revision", email["from"], email["subject"], email["body"],
                            client=client, revision=case["revision"])
            finish_change(state, change, record)
        else:
            _, record = receive(state, "email", email["from"], email["subject"], email["body"], client=client)
    except Exception as error:  # an API failure counts as a failed run, not a crashed suite
        return {"case": case["id"], "error": repr(error), "passed": False, "assertions": [],
                "cost_usd": 0, "seconds": 0}
    assertions = grade(case, record)
    return {
        "case": case["id"],
        "passed": all(a["passed"] for a in assertions),
        "assertions": assertions,
        "status": f"{record['package']['status']}: {record['package']['reason']}",
        "actual": describe_outcome(record),
        "cost_usd": record["agent"]["cost_usd"],
        "seconds": record["agent"]["seconds"],
        "record": record,
    }


def run(cases, repeat=1, client=None):
    jobs = [case for case in cases for _ in range(repeat)]
    with ThreadPoolExecutor(max_workers=PARALLEL_RUNS) as pool:
        runs = list(pool.map(lambda case: run_case_once(case, client), jobs))
    summary = []
    for case in cases:
        case_runs = [r for r in runs if r["case"] == case["id"]]
        summary.append({
            "case": case["id"],
            "title": case["title"],
            "type": case["type"],
            "passed_runs": sum(r["passed"] for r in case_runs),
            "runs": len(case_runs),
            "passed": all(r["passed"] for r in case_runs),
            "avg_cost_usd": round(sum(r["cost_usd"] for r in case_runs) / len(case_runs), 4),
            "avg_seconds": round(sum(r["seconds"] for r in case_runs) / len(case_runs), 1),
        })
    return {
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "model": config.MODEL,
        "effort": config.EFFORT,
        "prompt_version": config.AGENT_PROMPT.stem,
        "tools_version": config.AGENT_TOOLS.stem,
        "chat_prompt_version": config.CHAT_PROMPT.stem,
        "chat_effort": config.CHAT_EFFORT,
        "repeat": repeat,
        "cases_passed": sum(s["passed"] for s in summary),
        "cases": len(summary),
        "total_cost_usd": round(sum(r["cost_usd"] for r in runs), 3),
        "summary": summary,
        "runs": runs,
        "case_definitions": cases,
    }


def describe_outcome(record):
    """What the agent did, in one line a person can compare with the expected result."""
    package = record["package"]
    facts = []
    cp = package.get("change_package")
    if cp:
        for leg in cp.get("legs", []):
            facts.append(f"{leg['leg']} departs {leg['departure'][11:]}")
        services = [c for c in cp.get("record_updates", []) if c["op"] == "update_service"]
        if services:
            facts.append(f"{len(services)} service update(s)")
        passengers = [c for c in cp.get("record_updates", []) if c["op"] == "set_passengers"]
        for change in passengers:
            facts.append(f"{change['leg_id']} passengers → {len(change['passengers'])}")
        roles = [m["recipient_role"] for m in cp.get("messages", [])]
        if roles:
            facts.append(f"{len(roles)} message(s): {', '.join(sorted(set(roles)))}")
        waived = [f for f in cp.get("priced_fees", []) if f.get("waived")]
        charge = f"charges ${cp.get('fee_total', 0):,}"
        if waived:
            charge += f" ({', '.join('$' + format(f['amount'], ',') for f in waived)} waived)"
        facts.append(charge)
    clar = package.get("clarification")
    if clar:
        options = clar.get("options", [])
        if options:
            facts.append(f"{len(options)} option(s): " + "; ".join(f"{o['label']} ${o['fee_total']:,}" for o in options))
        else:
            facts.append("question to the client")
    routing = package.get("sales_routing")
    if routing:
        facts.append(f"routed to sales, {len(routing.get('held_updates', []))} update(s) held")
    for flag in package.get("flags", []):
        facts.append(f"flagged: {flag['category']}")
    automatic = [e for e in record.get("sent", []) if e["condition"] == "auto_informational"]
    if automatic:
        facts.append("auto-sent to " + ", ".join(e["to"] for e in automatic))
    if package.get("problems"):
        facts.append(f"{len(package['problems'])} verification problem(s)")
    return f"{package['status']}: {package['reason']}" + (" · " + " · ".join(facts) if facts else "")


def trim_run(run):
    """A run without its bulky trace: what the evals page needs to show it."""
    record = run.get("record") or {}
    if "chat" in record:
        result = record["chat"]
        return {
            "case": run["case"], "passed": run["passed"], "error": run.get("error"),
            "status": run.get("status"), "actual": run.get("actual"),
            "assertions": run["assertions"], "cost_usd": run["cost_usd"], "seconds": run["seconds"],
            "trail": [{"kind": "check", "text": f"{step['tool']}: {step['result']['summary'].splitlines()[0]}",
                       "ok": step["result"].get("result") != "fail"}
                      for step in result["trace"] if isinstance(step.get("result"), dict) and "summary" in step["result"]],
            "messages": [], "answer": result["answer"], "suggestion": result.get("suggestion"),
            "problems": [],
        }
    package = record.get("package") or {}
    messages = []
    for part in ("change_package", "clarification", "sales_routing"):
        for m in (package.get(part) or {}).get("messages", []):
            messages.append({k: m.get(k) for k in ("to", "recipient_role", "subject", "body")})
    events = (record.get("agent") or {}).get("events", [])
    return {
        "case": run["case"], "passed": run["passed"], "error": run.get("error"),
        "status": run.get("status"), "actual": run.get("actual"),
        "assertions": run["assertions"], "cost_usd": run["cost_usd"], "seconds": run["seconds"],
        "trail": [{k: e.get(k) for k in ("kind", "text", "ok") if k in e} for e in events
                  if e["kind"] in ("check", "proposal", "note", "tool_error", "stopped")],
        "messages": messages,
        "problems": package.get("problems", []),
    }


def publish(results):
    """Save a trimmed copy of a milestone run for the evals page and the exported file."""
    published = {k: v for k, v in results.items() if k not in ("runs", "case_definitions")}
    published["runs"] = [trim_run(run) for run in results["runs"]]
    PUBLISHED.write_text(json.dumps(published, indent=2, default=str) + "\n")
    return PUBLISHED


def report_markdown(results):
    lines = [
        f"# Eval results — {results['run_at']}",
        "",
        f"Model {results['model']} (effort {results['effort']}), prompt {results['prompt_version']}, "
        f"tools {results['tools_version']}. {results['repeat']} run(s) per case. "
        f"Total cost ${results['total_cost_usd']:.2f}.",
        "",
        f"**{results['cases_passed']} of {results['cases']} cases pass** "
        "(a case passes only if every run passes every assertion).",
        "",
        "| Case | Type | Title | Runs passed | Avg cost | Avg time |",
        "|---|---|---|---|---|---|",
    ]
    for s in results["summary"]:
        mark = "✓" if s["passed"] else "✗"
        lines.append(f"| {mark} {s['case']} | {s['type']} | {s['title']} | {s['passed_runs']}/{s['runs']} "
                     f"| ${s['avg_cost_usd']:.3f} | {s['avg_seconds']} s |")

    failures = [r for r in results["runs"] if not r["passed"]]
    if failures:
        lines += ["", "## Failures", ""]
        for r in failures:
            lines.append(f"**Case {r['case']}**" + (f" — run error: {r['error']}" if r.get("error") else
                                                    f" — status {r['status']}"))
            for a in r["assertions"]:
                if not a["passed"]:
                    lines.append(f"- `{a['type']}`: expected {a['expected']}; got {a['actual']}")
            lines.append("")
    return "\n".join(lines) + "\n"


def save(results):
    RESULTS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    json_path = RESULTS_DIR / f"{stamp}.json"
    md_path = RESULTS_DIR / f"{stamp}.md"
    json_path.write_text(json.dumps(results, indent=2, default=str))
    md_path.write_text(report_markdown(results))
    return json_path, md_path


def regrade(results_path: Path) -> dict:
    """Grade saved runs again against the current answer keys, without calling the agent.
    For when an answer key changes but the agent's behaviour didn't."""
    results = json.loads(results_path.read_text())
    cases = {c["id"]: c for c in load_cases()}
    results["runs"] = [r for r in results["runs"] if r["case"] in cases and r.get("record")]
    for run in results["runs"]:
        # The status is worked out from the package's contents; recompute it so a relabel
        # (e.g. "On sales" became "Routed to sales") applies to saved runs too.
        if "chat" in run["record"]:
            run["actual"] = describe_chat(run["record"]["chat"])
        else:
            package = run["record"]["package"]
            package["status"], package["reason"] = package_status(package)
            run["status"] = f"{package['status']}: {package['reason']}"
            run["actual"] = describe_outcome(run["record"])
        run["assertions"] = grade(cases[run["case"]], run["record"])
        run["passed"] = all(a["passed"] for a in run["assertions"])
    results["case_definitions"] = [cases[c] for c in dict.fromkeys(r["case"] for r in results["runs"])]
    summary = []
    for case in results["case_definitions"]:
        case_runs = [r for r in results["runs"] if r["case"] == case["id"]]
        summary.append({"case": case["id"], "title": case["title"], "type": case["type"],
                        "passed_runs": sum(r["passed"] for r in case_runs), "runs": len(case_runs),
                        "passed": all(r["passed"] for r in case_runs),
                        "avg_cost_usd": round(sum(r["cost_usd"] for r in case_runs) / len(case_runs), 4),
                        "avg_seconds": round(sum(r["seconds"] for r in case_runs) / len(case_runs), 1)})
    results["summary"] = summary
    results["cases"] = len(summary)
    results["cases_passed"] = sum(s["passed"] for s in summary)
    results["regraded_at"] = datetime.now().isoformat(timespec="seconds")
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(prog="evals.run")
    parser.add_argument("--case", action="append", help="Run only this case id (repeatable)")
    parser.add_argument("--repeat", type=int, default=1,
                        help="Runs per case: 1 day to day, 3 at milestones, 5 for the final published run")
    parser.add_argument("--publish", action="store_true",
                        help="Save this run as the published results shown on the evals page")
    parser.add_argument("--regrade", metavar="RESULTS_JSON",
                        help="Grade a saved results file again against the current answer keys (no agent runs)")
    args = parser.parse_args(argv)

    if args.regrade:
        results = regrade(Path(args.regrade))
        json_path, md_path = save(results)
        print(report_markdown(results))
        print(f"Saved {md_path}")
        if args.publish:
            print(f"Published {publish(results)}")
        return

    cases = load_cases(args.case)
    print(f"Running {len(cases)} case(s) × {args.repeat} run(s) "
          f"({config.MODEL}, effort {config.EFFORT}, {config.AGENT_PROMPT.stem}, {config.AGENT_TOOLS.stem})…")
    results = run(cases, args.repeat)
    json_path, md_path = save(results)
    print()
    print(report_markdown(results))
    print(f"Saved {md_path}\n      {json_path}")
    if args.publish:
        print(f"Published {publish(results)}")


if __name__ == "__main__":
    main()
