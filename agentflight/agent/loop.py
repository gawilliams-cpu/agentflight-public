"""The tool-use loop: a plain loop written against the Claude API, no framework.

Each turn sends the conversation to Claude. If Claude asks for tools, the code
runs them and sends the results back. The run ends when Claude stops asking for
tools. Every step becomes an activity event, for the live feed and the trace.

Stops early, and flags the run, if: a tool fails for a reason other than bad
arguments, the model declines, a response is cut off, or the turn limit is hit.
"""

import json
import re
import time
from datetime import datetime

import anthropic

from agentflight import config
from agentflight.agent.schema import schema_errors
from agentflight.agent.tools import (OUTPUT_TOOLS, ToolInputError, load_tool_definitions,
                                     run_read_or_check_tool)

OUTPUT_TOOL_REPLY = "Recorded for the trip manager's review. Nothing has been sent."


def run_agent(state: dict, user_content: str, client=None, on_event=None) -> dict:
    """Run the change agent on one prepared input. Returns the trace, proposals, and usage."""
    client = client or anthropic.Anthropic()
    tools_version, tools = load_tool_definitions()
    schemas = {tool["name"]: tool["input_schema"] for tool in tools}
    system = config.AGENT_PROMPT.read_text()
    messages = [{"role": "user", "content": user_content}]

    events, trace, outputs = [], [], []
    usage = {"input": 0, "cache_write": 0, "cache_read": 0, "output": 0}
    stopped = None
    model_used = config.MODEL
    started = time.monotonic()

    def event(kind, text, **extra):
        entry = {"at": datetime.now().isoformat(timespec="seconds"), "kind": kind, "text": text, **extra}
        events.append(entry)
        if on_event:
            on_event(entry)

    event("start", "Agent started")
    for _ in range(config.MAX_TURNS):
        response = client.beta.messages.create(
            model=config.MODEL,
            max_tokens=config.MAX_TOKENS,
            system=system,
            tools=tools,
            messages=messages,
            output_config={"effort": config.EFFORT},
            cache_control={"type": "ephemeral"},  # caches tools + system + conversation so far
            betas=[config.FALLBACK_BETA],
            fallbacks="default",
        )
        _add_usage(usage, response.usage)
        model_used = response.model
        messages.append({"role": "assistant", "content": response.content})

        for block in response.content:
            if block.type == "text" and block.text.strip():
                event("note", block.text.strip())

        if response.stop_reason == "refusal":
            stopped = "The model declined to continue."
            break
        if response.stop_reason == "max_tokens":
            stopped = "A model response was cut off at the token limit."
            break

        tool_uses = [block for block in response.content if block.type == "tool_use"]
        if not tool_uses:
            break

        results = []
        for block in tool_uses:
            event("step", step_label(block.name, block.input), tool=block.name, input=block.input)
            problems = schema_errors(block.input, schemas.get(block.name, {}))
            if problems:
                message = "Input doesn't match the tool's schema: " + "; ".join(problems[:5])
                trace.append({"tool": block.name, "input": block.input, "error": message})
                event("tool_error", f"{block.name}: {message}")
                results.append(_tool_result(block.id, f"Error: {message}", is_error=True))
                continue
            problem = proposal_problem(block.name, block.input)
            if problem:   # bounced back to redo, like a schema error
                trace.append({"tool": block.name, "input": block.input, "error": problem})
                event("tool_error", f"{block.name}: {problem}")
                results.append(_tool_result(block.id, f"Error: {problem}", is_error=True))
                continue
            if block.name in OUTPUT_TOOLS:
                outputs.append({"tool": block.name, "input": block.input})
                event("proposal", _describe_output(block.name, block.input))
                results.append(_tool_result(block.id, OUTPUT_TOOL_REPLY))
                continue
            try:
                result = run_read_or_check_tool(state, block.name, block.input)
            except ToolInputError as error:
                trace.append({"tool": block.name, "input": block.input, "error": str(error)})
                event("tool_error", f"{block.name}: {error}")
                results.append(_tool_result(block.id, f"Error: {error}", is_error=True))
                continue
            except Exception as error:  # a real failure, not bad input: stop the run
                trace.append({"tool": block.name, "input": block.input, "error": repr(error)})
                stopped = f"Tool {block.name} failed: {error!r}"
                event("stopped", stopped)
                break
            trace.append({"tool": block.name, "input": block.input, "result": result})
            kind, text, ok = _describe_tool(block.name, result)
            event(kind, text, tool=block.name, ok=ok)
            results.append(_tool_result(block.id, json.dumps(result, default=str)))
        if stopped:
            break
        messages.append({"role": "user", "content": results})
    else:
        stopped = f"The run reached the {config.MAX_TURNS}-turn limit."

    if stopped:
        if events[-1]["kind"] != "stopped":
            event("stopped", stopped)
    elif not outputs:
        stopped = "The agent finished without proposing anything."
        event("stopped", stopped)
    else:
        event("done", "Agent finished")

    return {
        "model": model_used,
        "prompt_version": config.AGENT_PROMPT.stem,
        "tools_version": tools_version,
        "effort": config.EFFORT,
        "events": events,
        "trace": trace,
        "outputs": outputs,
        "stopped": stopped,
        "usage": usage,
        "cost_usd": _cost(usage),
        "seconds": round(time.monotonic() - started, 1),
    }


PRICE = re.compile(r"\$\s?\d[\d,]*")


def proposal_problem(name: str, arguments: dict) -> str | None:
    """A proposal code can't verify, caught before it reaches the trip manager.

    Options for the client must be in the `options` field: code re-checks and prices each one
    there. Options written only into the email can't be verified (eval case 4, 10/23: the
    right options, priced correctly, but only in the email, so the package went to review)."""
    if name != "propose_clarification" or arguments.get("options"):
        return None
    to_client = [m for m in arguments.get("messages") or [] if m.get("recipient_role") == "client"]
    prices = sorted({p.strip() for m in to_client for p in PRICE.findall(m.get("body", ""))})
    if not prices:
        return None
    return (f"The email to the client quotes prices ({', '.join(prices)}), but `options` is empty. "
            "Code can only verify and price options listed in `options`. "
            "Call propose_clarification again with each option you offer in `options`.")


def _tool_result(tool_use_id, content, is_error=False):
    result = {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}
    if is_error:
        result["is_error"] = True
    return result


def _add_usage(totals, usage):
    totals["input"] += usage.input_tokens or 0
    totals["cache_write"] += usage.cache_creation_input_tokens or 0
    totals["cache_read"] += usage.cache_read_input_tokens or 0
    totals["output"] += usage.output_tokens or 0


def _cost(usage):
    prices = config.PRICES[config.MODEL]
    dollars = sum(usage[kind] * prices[kind] for kind in usage) / 1_000_000
    return round(dollars, 4)


CHECK_NAMES = {
    "check_crew_duty": "Crew duty",
    "check_capacity": "Seats",
    "check_range_and_flight_time": "Range and flight time",
    "check_airport_hours": "Airport hours",
    "check_watchlist": "Watchlist",
    "find_available_crew": "Available crew",
    "check_aircraft_availability": "Aircraft availability",
    "check_aircraft_schedule": "Aircraft schedule",
}


def _describe_tool(name, result):
    """An activity line for a read or check tool: (kind, plain text, passed?)."""
    if name == "find_duty_fixes":
        count = len(result["details"]["fixes"])
        return "check", f"Fixes: {count} way{'s' if count != 1 else ''} to make it work, each passing every check", count > 0
    if name == "compute_change_fee":
        return "check", f"Pricing: {result['summary'].splitlines()[0]}", result["defined"]
    if isinstance(result, dict) and "check" in result:
        first_line = result["summary"].splitlines()[0]
        return "check", f"{CHECK_NAMES.get(name, name)}: {first_line}", result["result"] == "pass"
    return "read", STEP_LABELS.get(name, name), None


STEP_LABELS = {
    "find_trips_for_sender": "Matching the email to a trip",
    "get_trip": "Reading the trip",
    "get_schedule": "Reading the schedule",
    "get_vendors": "Looking up vendors",
    "get_rate_card": "Reading the rate card",
    "get_fleet": "Reading the fleet",
    "check_crew_duty": "Checking crew duty",
    "check_capacity": "Checking seats",
    "check_range_and_flight_time": "Checking range and flight time",
    "check_airport_hours": "Checking airport hours",
    "check_watchlist": "Screening passengers",
    "find_available_crew": "Looking for available crew",
    "check_aircraft_availability": "Checking aircraft availability",
    "check_aircraft_schedule": "Checking the aircraft's schedule",
    "compute_change_fee": "Pricing the change",
    "find_duty_fixes": "Searching for ways to make it work",
    "route_to_sales": "Writing the note to sales",
    "flag_message": "Flagging for the trip manager",
}


def step_label(name, arguments):
    """Plain words for the live ticker: what the agent is doing right now."""
    if name in ("propose_change_package", "propose_clarification"):
        count = len(arguments.get("messages", []) or [])
        return f"Drafting {count} message{'s' if count != 1 else ''}"
    return STEP_LABELS.get(name, name.replace("_", " ").capitalize())


def _describe_output(name, arguments):
    labels = {
        "propose_change_package": "Proposed a change package",
        "propose_clarification": "Proposed a clarification",
        "route_to_sales": "Routed to sales",
        "flag_message": "Flagged for the trip manager",
    }
    detail = arguments.get("summary") or arguments.get("reason") or ""
    return f"{labels[name]}: {detail}"
