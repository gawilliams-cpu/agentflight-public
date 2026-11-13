# AgentFlight

**Flawless charter trips.** An AI agent that handles the coordination behind every change to a booked private-jet charter trip. The agent does the legwork, and the trip manager makes the final call.

AgentFlight is a working prototype with an eval suite, built in October 2026 as a capstone project for an AI product management course.

## The problem

In US private-jet charter (FAA Part 135), a booked trip changes ten or more times before departure, and each change takes 10 to 20 emails, texts and calls to get every party back in sync. A single "can we push to 5pm?" ripples into crew duty limits, aircraft availability, change fees, catering, ground transport, the FBOs (the private terminals at each airport) and the client. One missed detail can mean an illegal flight, an unbilled fee or a lost client. Almost all of this still runs on email, phone and copy-and-paste, and it all flows through one person: the trip manager.

## What AgentFlight does

A message arrives in the operations inbox. Usually it's an email; a call or text can also be logged by the trip manager. The agent then:

1. identifies the sender and the trip, and works out what is being asked;
2. runs the deterministic checks that apply: crew duty and rest, crew location, seat capacity, range and flight time, airport hours, aircraft availability, a watchlist, and change fees from the rate card;
3. when a check fails, searches for fixes that pass every check, and prices them;
4. drafts one review package: the record updates, the charges, and a message to every affected party. Instead of a change, it may draft a question or a set of options for the client, or a hand-off to sales when the change needs a new quote.

The trip manager approves, edits, rejects, or rejects with instructions for a revision. Nothing is sent until they approve. On approval, code re-runs every check against the current state before applying anything, and it tracks vendor confirmations afterwards.

**The demo:** the client asks to push a Thursday departure to 6pm. That would put the crew over their 14-hour duty limit, so the agent changes nothing and offers three verified, priced options instead. The trip manager approves sending them. When the client replies "Option A", the agent takes option A from its own record of what it offered (not from the client's quoted text), re-checks it, and drafts the updates for all five affected parties. Approving that package closes the loop.

## How it's built

The design rule: **the model proposes, code checks, a person approves.**

- **The model reasons; code computes.** The agent (Claude, `claude-sonnet-5-5`) reads messages, decides which checks apply, and drafts the package. Every check, price and duty calculation is a deterministic Python tool, and the model never does that arithmetic itself. See `agentflight/tools/`.
- **Every proposal is verified before a person sees it.** Code re-runs the checks, prices every charge itself, confirms every recipient is a real party to the trip, and confirms that every dollar amount in a draft matches a priced charge. A proposal that fails goes to "Needs review" instead of "Needs approval".
- **The model never sends.** All sending goes through one dispatch function, governed by the operator's approval policy, to a mock outbox. No real email is ever sent.
- **Inbound messages are untrusted data.** The agent never follows instructions inside an email, and flags suspicious ones.
- **One intake path.** Real email, the demo control panel and the eval runner all feed the same `intake()` function.
- **Read-only trip chat.** The trip manager can ask questions about a trip ("Why not just push to 6pm?"). The chat uses the same read and check tools, and it can't change anything.
- **Versioned prompts.** System prompts and tool definitions are versioned files in `prompts/`. Every change is a new file followed by a full eval re-run.

Stack: Python 3.12 (via [uv](https://docs.astral.sh/uv/)), FastAPI serving a single HTML/JavaScript page through JSON routes, and JSON files for data. All business logic lives in a service layer; the web routes and the page only display results.

## Evals

There are 21 answer-keyed cases: happy paths, boundaries (both sides of the duty and seat limits), ambiguous requests, a prompt-injection attempt, the approval policy, the trip manager's revisions and fee waivers, the client's reply to an options email, and the trip chat. Code grades each run, with no model judging. On top of each case's own checks, every run automatically checks three things: nothing was sent without approval, the code's verification of the proposal came back clean, and the client got at most one email.

**Final publish: 21 of 21 cases passed, all 105 runs (5 runs per case).** The suite caught real defects along the way. For example, a crew swap passed the checks even though the crew was at the wrong airport, and sales notes were going to the operations address. Each defect became a fix in code or the prompt, and was re-tested across the whole suite. The evals page in the app shows every case with its expected and actual results.

There are also 159 unit tests (no AI involved) covering the checks against hand-worked figures, the approval and sending rules, and the safety nets.

## Running it locally

You need [uv](https://docs.astral.sh/uv/) and an [Anthropic API key](https://console.anthropic.com/).

```bash
echo 'ANTHROPIC_API_KEY=your-key-here' > .env    # git-ignored
uv run python -m agentflight.web                 # then open http://localhost:8000
```

In the app, use the demo control panel to deliver `demo-1`, `demo-2` and `demo-3` in order, approving each package as it arrives. **Reset** restores the demo week.

Other commands:

| Command | What it does |
|---|---|
| `uv run pytest` | Unit tests (no API calls) |
| `uv run python -m evals.run` | Run the eval suite once (about $1 in API usage) |
| `uv run python -m evals.run --repeat 5` | The full 5-run suite (about $4) |
| `uv run python -m agentflight.cli deliver demo-1` | Run the agent on one message from the command line |

## Repository map

| Path | What's there |
|---|---|
| `agentflight/` | The app: intake, the agent loop, workflow (approve, reject, revise), dispatch, web routes, and the page (`static/index.html`) |
| `agentflight/tools/` | The deterministic checks: duty, flight, fees, fix search, reference lookups |
| `prompts/` | Versioned system prompts and tool definitions |
| `data/` | The synthetic operator: fleet, crews, airports, vendors, clients, the demo week, the rate card, policies, and the demo emails |
| `evals/` | Eval cases, grading, fixtures, and published results |
| `tests/` | Unit tests |

## Data and limits

All data is synthetic. The operator, clients, passengers and vendors are fictional, and every email address is on `example.com`.

The prototype simplifies the real world. Duty and rest are a flat 14-hour day with 10 hours of rest (the full Part 135 rules are more complex). Flight time ignores winds. The data is one operator's week of synthetic flights. Nothing connects to a real inbox or scheduling system, and nothing is ever really sent.

## Author

Greg Williams
