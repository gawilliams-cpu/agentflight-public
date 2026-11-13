"""Command line for the change workflow. Works on the saved working state (runtime/).

    uv run python -m agentflight.cli deliver demo-1          the agent processes a scripted email
    uv run python -m agentflight.cli approve chg-005         approve every item
    uv run python -m agentflight.cli approve chg-005 --reject message-2
    uv run python -m agentflight.cli reject chg-005 --note "Client withdrew"
    uv run python -m agentflight.cli revise chg-005 "Don't move the morning leg"
    uv run python -m agentflight.cli queue                   the work queue
    uv run python -m agentflight.cli outbox                  what has been sent (mock)
    uv run python -m agentflight.cli readiness               unconfirmed services near departure
    uv run python -m agentflight.cli reset                   back to the base week
    uv run python -m agentflight.cli show demo-2 --details   re-display a saved run (no API cost)

Nothing is ever really sent: the outbox is a file. The package is shown compact,
the way a busy trip manager scans it; --details adds every check result.
"""

import argparse
import json
import sys
import textwrap

from agentflight import config, workspace
from agentflight.intake import save_run
from agentflight.workflow import WorkflowError, approve, readiness_flags, receive, reject, revise


def main(argv=None):
    parser = argparse.ArgumentParser(prog="agentflight")
    commands = parser.add_subparsers(dest="command", required=True)
    deliver = commands.add_parser("deliver", help="Deliver a scripted inbox message to the agent")
    deliver.add_argument("message", help="Inbox message id, e.g. demo-1")
    deliver.add_argument("--details", action="store_true", help="Show every check result")
    approve_cmd = commands.add_parser("approve", help="Approve a change (all items unless rejected)")
    approve_cmd.add_argument("change", help="Change id, e.g. chg-005")
    approve_cmd.add_argument("--reject", action="append", default=[], help="Item id to reject (repeatable)")
    reject_cmd = commands.add_parser("reject", help="Reject a whole change package and close it")
    reject_cmd.add_argument("change")
    reject_cmd.add_argument("--note", help="Why")
    revise_cmd = commands.add_parser("revise", help="Reject a package and have the agent revise it")
    revise_cmd.add_argument("change")
    revise_cmd.add_argument("instructions", help="What should change")
    commands.add_parser("queue", help="Show the work queue")
    commands.add_parser("outbox", help="Show what has been sent (mock outbox)")
    commands.add_parser("readiness", help="Unconfirmed services within the readiness threshold")
    commands.add_parser("reset", help="Back to the base week")
    show = commands.add_parser("show", help="Re-display the latest saved run of an inbox message")
    show.add_argument("message", help="Inbox message id, e.g. demo-2")
    show.add_argument("--details", action="store_true", help="Show every check result")
    args = parser.parse_args(argv)

    if args.command == "show":
        print_package(_latest_run(args.message)[1], details=args.details)
        return
    if args.command == "reset":
        workspace.reset()
        print("Reset to the base week: original schedule and trips, pre-seeded queue, empty outbox.")
        return

    state = workspace.load_working_state()
    try:
        if args.command == "deliver":
            _deliver(state, args.message, args.details)
        elif args.command == "approve":
            result = approve(state, args.change, {item: {"action": "reject"} for item in args.reject})
            if result["executed"]:
                print(f"Approved {args.change}. Sent {len(result['sent'])} message(s):")
                for entry in result["sent"]:
                    print(f"  {entry['id']} → {entry['to']} ({entry['condition']}): {entry['subject']}")
            else:
                print(f"Re-check failed. Nothing was executed. {args.change} returns to Needs review:")
                for problem in result["problems"]:
                    print(f"  ! {problem}")
        elif args.command == "reject":
            reject(state, args.change, args.note)
            print(f"Rejected and closed {args.change}. Nothing was executed.")
        elif args.command == "revise":
            new, record = revise(state, args.change, args.instructions,
                                 on_event=lambda e: print(f"  [{e['kind']}] {_short(e['text'])}"))
            print_package(record)
            print(f"\nFiled the revision as {new['id']}.")
        elif args.command == "queue":
            _print_queue(state)
        elif args.command == "outbox":
            _print_outbox(state)
        elif args.command == "readiness":
            flags = readiness_flags(state)
            print("\n".join(f"  ! {f['text']}" for f in flags) or "No unconfirmed services within the threshold.")
        if args.command in ("approve", "reject", "revise"):
            _print_queue(state)
    except WorkflowError as error:
        sys.exit(f"Can't do that: {error}")
    workspace.save_working_state(state)


def _deliver(state, message_id, details):
    with open(config.INBOX_DIR / f"{message_id}.json") as f:
        message = json.load(f)
    print(f"Delivering {message_id} from {message['from']}: {message['subject']}\n")
    change, record = receive(state, "email", message["from"], message["subject"], message["body"],
                             on_event=lambda e: print(f"  [{e['kind']}] {_short(e['text'])}"))
    path = save_run(record, message_id)
    print_package(record, details=details)
    print(f"\nFiled as {change['id']}. Items to decide: "
          f"{', '.join(i['id'] for i in change['items'] if i['decision'] is None) or 'none'}.")
    print(f"Saved run {path}")


def _print_queue(state):
    print("\nWORK QUEUE")
    for change in state["queue"]:
        reason = f": {change['reason']}" if change.get("reason") else ""
        print(f"  {change['id']}  trip {change['trip'] or '?':<3} {change['status']}{reason}  "
              f"— {_short(change['summary'], 90)}")


def _print_outbox(state):
    if not state["outbox"]:
        print("The outbox is empty.")
    for entry in state["outbox"]:
        print(f"  {entry['id']}  {entry['change']}  → {entry['to']}  [{entry['condition']}]  {entry['subject']}")


def _latest_run(message_id):
    runs = sorted(config.RUNS_DIR.glob(f"*-{message_id}.json"))
    if not runs:
        sys.exit(f"No saved run for {message_id}. Deliver it first.")
    with open(runs[-1]) as f:
        return runs[-1].name, json.load(f)


def print_package(record, details=False):
    package, agent = record["package"], record["agent"]
    print(f"\n{'=' * 72}\n{package['status'].upper()}: {package['reason']}")

    if package["problems"]:
        print("\nPROBLEMS found by the code's own verification:")
        for problem in package["problems"]:
            print(f"  ! {problem}")

    cp = package["change_package"]
    if cp:
        print(f"\nTrip {cp['trip_id']}\n{_wrap(cp['summary'])}\n")
        _print_legs(cp.get("legs", []))
        _print_verdict(cp["verified_checks"], details)
        _print_charges(cp["priced_fees"], cp["fee_total"], details)
        for message in cp["messages"]:
            _print_message(message)

    clar = package["clarification"]
    if clar:
        print(f"\nTrip {clar.get('trip_id', '(not yet known)')}\n{_wrap(clar['summary'])}")
        for option in clar.get("options", []):
            print(f"\n  Option {option['label']}: {option['description']}")
            _print_legs(option.get("legs", []), indent="    ")
            _print_verdict(option["verified_checks"], details, indent="    ")
            _print_charges(option["priced_fees"], option["fee_total"], details, indent="    ")
        for message in clar["messages"]:
            _print_message(message)

    sales = package["sales_routing"]
    if sales:
        print(f"\nTrip {sales['trip_id']}\n{_wrap(sales['summary'])}")
        for message in sales["messages"]:
            _print_message(message)
        for held in sales["held_updates"]:
            print(f"  Held: {held['party']}: {held['update']}")

    for flag in package["flags"]:
        print(f"\nFLAG ({flag['category']}): {flag['reason']}")

    usage = agent["usage"]
    print(f"\n{'-' * 72}")
    print(f"Model {agent['model']}, prompt {agent['prompt_version']}, tools {agent['tools_version']}, "
          f"effort {agent['effort']}")
    print(f"Tool calls: {len(agent['trace'])}. Time: {agent['seconds']} s. "
          f"Tokens: {usage['input']:,} in, {usage['cache_read']:,} cache read, "
          f"{usage['cache_write']:,} cache write, {usage['output']:,} out. "
          f"Cost: ${agent['cost_usd']:.3f}")
    print(f"Sent: {len(record['sent'])} messages (nothing is sent before approval).")
    if not details:
        print("Add --details to see every check result.")


def _print_legs(legs, indent="  "):
    for leg in legs:
        print(f"{indent}{leg['text']}")


def _print_verdict(results, details, indent="  "):
    """One line when everything passes; the failing checks in full otherwise."""
    if not results:
        return
    failed = [r for r in results if r["result"] != "pass"]
    if failed:
        print(f"{indent}✗ {len(failed)} of {len(results)} checks fail:")
    else:
        print(f"{indent}✓ All {len(results)} checks pass")
    for result in (results if details else failed):
        mark = "ok  " if result["result"] == "pass" else "FAIL"
        for i, line in enumerate(result["summary"].splitlines()):
            print(f"{indent}  {mark if i == 0 else '    '} {line}")


def _print_charges(priced, total, details, indent="  "):
    if not priced:
        return
    parts = [f"{fee['label']} ${fee['amount']:,}" if fee["defined"] else f"{fee['charge']} (not defined)"
             for fee in priced]
    print(f"{indent}Charges: ${total:,} ({'; '.join(parts)})")
    if details:
        for fee in priced:
            for line in fee["breakdown"]:
                print(f"{indent}  {line['item']}: {line['explanation']}")


def _print_message(message):
    print(f"\n  --- To: {message['to']} ({message['recipient_role']})\n  Subject: {message['subject']}")
    print(textwrap.indent(message["body"], "  | "))


def _wrap(text):
    return textwrap.fill(text, width=90, initial_indent="  ", subsequent_indent="  ")


def _short(text, limit=160):
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


if __name__ == "__main__":
    main()
