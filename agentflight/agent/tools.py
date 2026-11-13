"""The agent's tools: definitions from prompts/, and the Python function behind each.

Read and check tools run immediately against the current state. Output tools
(propose_*, route_to_sales, flag_message) only record what the agent proposes;
they never change data or send anything.
"""

import copy
import json
from pathlib import Path

from agentflight import config
from agentflight.tools import checks, duty, fees, fixes, flight, reference

OUTPUT_TOOLS = ("propose_change_package", "propose_clarification", "route_to_sales", "flag_message")

# Tool name -> function(state, **arguments). Argument names match the tool schemas.
READ_AND_CHECK_TOOLS = {
    "find_trips_for_sender": reference.find_trips_for_sender,
    "get_trip": reference.get_trip,
    "get_schedule": reference.get_schedule,
    "get_vendors": reference.get_vendors,
    "get_rate_card": reference.get_rate_card,
    "get_fleet": reference.get_fleet,
    "check_crew_duty": duty.check_crew_duty,
    "check_capacity": lambda state, aircraft, passenger_count:
        checks.check_capacity(state, aircraft, passenger_count),
    "check_range_and_flight_time": lambda state, aircraft, origin, destination:
        flight.check_range_and_flight_time(state, aircraft, origin, destination),
    "check_airport_hours": lambda state, airport, time:
        checks.check_airport_hours(state, airport, time),
    "check_watchlist": checks.check_watchlist,
    "find_available_crew": checks.find_available_crew,
    "check_aircraft_availability": lambda state, aircraft, start, end, changes=None:
        checks.check_aircraft_availability(state, aircraft, start, end, changes),
    "check_aircraft_schedule": lambda state, aircraft, changes=None:
        checks.check_aircraft_schedule(state, aircraft, changes),
    "compute_change_fee": fees.compute_change_fee,
    "find_duty_fixes": fixes.find_duty_fixes,
}


class ToolInputError(Exception):
    """The agent called a tool with bad arguments. It gets the error back and can correct itself."""


def load_tool_definitions(path: Path = config.AGENT_TOOLS) -> tuple[str, list]:
    """Read the tool file and return (version, tool list) with shared schemas filled in."""
    with open(path) as f:
        spec = json.load(f)
    definitions = spec["definitions"]

    def expand(node):
        if isinstance(node, dict):
            if set(node) == {"$ref"}:
                return expand(copy.deepcopy(definitions[node["$ref"].split("/")[-1]]))
            return {key: expand(value) for key, value in node.items()}
        if isinstance(node, list):
            return [expand(item) for item in node]
        return node

    # Not "strict": the API rejects schemas this size in strict mode. Instead
    # every call is checked in code (agent/schema.py) and a malformed one is
    # bounced back to the agent to redo.
    tools = [expand(tool) for tool in spec["tools"]]
    names = {tool["name"] for tool in tools}
    expected = set(READ_AND_CHECK_TOOLS) | set(OUTPUT_TOOLS)
    if names != expected:
        raise ValueError(f"Tool file and code disagree: {sorted(names ^ expected)}")
    return spec["version"], tools


def run_read_or_check_tool(state: dict, name: str, arguments: dict):
    """Run one read or check tool.

    Bad arguments (an unknown id, a malformed time, a missing field) raise
    ToolInputError so the agent can correct itself. Anything else that goes
    wrong is a real failure and propagates, which stops the run.
    """
    function = READ_AND_CHECK_TOOLS.get(name)
    if function is None:
        raise ToolInputError(f"Unknown tool: {name}")
    try:
        return function(state, **arguments)
    except (ValueError, TypeError) as error:
        raise ToolInputError(str(error)) from error
