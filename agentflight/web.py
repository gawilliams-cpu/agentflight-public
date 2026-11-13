"""The local web server (live mode). Routes only call service functions and return JSON.

    uv run python -m agentflight.web     then open http://localhost:8000

One working state lives in memory, guarded by a lock, and is saved to runtime/
after every change. The agent runs in a background thread on a snapshot of the
state, so the page stays responsive while it works; approving its package later
re-checks against the then-current state anyway. Every change is announced on
/api/events (server-sent events), so any open page updates live.
"""

import asyncio
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agentflight import chat, config, views, workspace
from evals import page as evals_page
from evals import run as eval_runner
from agentflight.intake import intake, save_run
from agentflight.store import copy_state
from agentflight.workflow import (WorkflowError, approve, finish_change, note_contacts, note_record, note_step,
                                  prepare_note, reject, start_change, start_revision)

PAGE = Path(__file__).parent / "static" / "index.html"

lock = threading.RLock()
state = workspace.load_working_state()


class EventBus:
    """Fans each event out to every open page. Safe to publish from the agent's thread."""

    def __init__(self):
        self.subscribers = set()
        self.loop = None

    def subscribe(self):
        queue = asyncio.Queue()
        self.subscribers.add(queue)
        return queue

    def unsubscribe(self, queue):
        self.subscribers.discard(queue)

    def publish(self, event):
        if self.loop is None:
            return
        for queue in list(self.subscribers):
            self.loop.call_soon_threadsafe(queue.put_nowait, event)


bus = EventBus()


@asynccontextmanager
async def lifespan(_app):
    bus.loop = asyncio.get_running_loop()
    yield


app = FastAPI(title="AgentFlight", lifespan=lifespan)


def _save_and_announce(event):
    workspace.save_working_state(state)
    bus.publish(event)


# --- Pages and reads -------------------------------------------------------------

class FreshStaticFiles(StaticFiles):
    """Static files the browser re-checks on every load, so a replaced photo shows up straight away."""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


# Photos and map images for the Reference view (live mode only; the exported file leaves them out).
for folder in ("photos", "maps"):
    app.mount(f"/static/{folder}", FreshStaticFiles(directory=PAGE.parent / folder), name=folder)


@app.get("/")
def page():
    # Never cached, so a reload always shows the latest page.
    return FileResponse(PAGE, headers={"Cache-Control": "no-store"})


@app.get("/api/overview")
def get_overview():
    with lock:
        return {**views.overview(state), "mode": "live"}


@app.get("/api/trips/{trip_id}")
def get_trip_view(trip_id: str):
    with lock:
        try:
            return views.trip_view(state, trip_id)
        except ValueError as error:
            raise HTTPException(404, str(error))


@app.get("/api/schedule")
def get_schedule_board():
    with lock:
        return views.schedule_board(state)


@app.get("/api/reference")
def get_reference():
    with lock:
        return views.reference(state)


@app.get("/api/changes/{change_id}")
def get_change(change_id: str):
    with lock:
        change = _change(change_id)
        if change is None:
            raise HTTPException(404, f"Unknown change {change_id}")
        return {**views.change_card(state, change), "number": 1, "drafted_before": []}


@app.get("/api/inbox")
def get_inbox():
    messages = [json.loads(p.read_text()) for p in sorted(config.INBOX_DIR.glob("*.json"))]
    return [{"id": m["id"], "from": m["from"], "subject": m["subject"]} for m in messages]


@app.get("/api/outbox")
def get_outbox():
    with lock:
        return state["outbox"]


@app.get("/api/events")
async def events():
    queue = bus.subscribe()

    async def stream():
        quiet_seconds = 0
        try:
            yield "event: hello\ndata: {}\n\n"
            # Check once a second whether the server is stopping, so Ctrl+C ends the
            # connection cleanly instead of waiting on it.
            while not (server and server.should_exit):
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=1)
                except asyncio.TimeoutError:
                    quiet_seconds += 1
                    if quiet_seconds >= 15:
                        quiet_seconds = 0
                        yield ": keep-alive\n\n"  # also lets the server notice a closed page
                    continue
                quiet_seconds = 0
                yield f"data: {json.dumps(event, default=str)}\n\n"
        finally:
            bus.unsubscribe(queue)

    return StreamingResponse(stream(), media_type="text/event-stream")


# --- Actions ---------------------------------------------------------------------

@app.post("/api/inbox/{message_id}/deliver")
def deliver(message_id: str):
    path = config.INBOX_DIR / f"{message_id}.json"
    if not path.exists():
        raise HTTPException(404, f"No inbox message {message_id}")
    message = json.loads(path.read_text())
    with lock:
        change = start_change(state, "email", message["from"], message["subject"])
        snapshot = copy_state(state)
        _save_and_announce({"type": "changed", "change": change["id"]})
    threading.Thread(target=_run_agent, args=(change["id"], snapshot, message), daemon=True).start()
    return {"change": change["id"]}


class LoggedNote(BaseModel):
    sender: str
    channel: str
    text: str


@app.get("/api/notes/contacts")
def get_note_contacts():
    with lock:
        return note_contacts(state)


@app.post("/api/notes")
def log_note(body: LoggedNote):
    """A call or text the trip manager logged: the same intake path as an email."""
    with lock:
        try:
            subject, text = prepare_note(state, body.sender, body.channel, body.text)
        except WorkflowError as error:
            raise HTTPException(400, str(error))
        change = start_change(state, "note", body.sender, subject)
        change["note"] = note_record(body.channel, body.text)
        snapshot = copy_state(state)
        _save_and_announce({"type": "changed", "change": change["id"]})
    message = {"id": None, "source": "note", "from": body.sender, "subject": subject, "body": text}
    threading.Thread(target=_run_agent, args=(change["id"], snapshot, message), daemon=True).start()
    return {"change": change["id"]}


def _run_agent(change_id, snapshot, message, revision=None):
    def on_event(event):
        with lock:
            change = _change(change_id)
            if change is None:  # the demo was reset mid-run
                return
            note_step(change, event)
        bus.publish({"type": "step", "change": change_id, "kind": event["kind"], "text": event["text"],
                     "ok": event.get("ok"), "trip": change.get("trip")})

    try:
        record = intake(snapshot, "revision" if revision else message.get("source", "email"), message["from"], message["subject"],
                        message["body"], on_event=on_event, revision=revision)
    except Exception as error:  # e.g. the API is unreachable: the change must not stay Processing
        with lock:
            change = _change(change_id)
            if change is None:
                return
            change.update({"status": "Needs review", "reason": "Run stopped",
                           "summary": f"The agent could not run: {error}"})
            change.pop("step", None)
            _save_and_announce({"type": "changed", "change": change_id})
        return
    save_run(record, message.get("id") or change_id)
    with lock:
        change = _change(change_id)
        if change is None:
            return
        finish_change(state, change, record)
        _save_and_announce({"type": "changed", "change": change_id})


class Decisions(BaseModel):
    decisions: dict = {}


class Note(BaseModel):
    note: str | None = None


class Instructions(BaseModel):
    instructions: str


@app.post("/api/changes/{change_id}/approve")
def approve_change(change_id: str, body: Decisions):
    with lock:
        try:
            result = approve(state, change_id, body.decisions)
        except WorkflowError as error:
            raise HTTPException(409, str(error))
        _save_and_announce({"type": "changed", "change": change_id})
        return result


@app.post("/api/changes/{change_id}/reject")
def reject_change(change_id: str, body: Note):
    with lock:
        try:
            reject(state, change_id, body.note)
        except WorkflowError as error:
            raise HTTPException(409, str(error))
        _save_and_announce({"type": "changed", "change": change_id})
        return {"ok": True}


@app.post("/api/changes/{change_id}/revise")
def revise_change(change_id: str, body: Instructions):
    with lock:
        try:
            new, revision = start_revision(state, change_id, body.instructions)
        except WorkflowError as error:
            raise HTTPException(409, str(error))
        email = _change(change_id)["email"]
        snapshot = copy_state(state)
        _save_and_announce({"type": "changed", "change": new["id"]})
    message = {"id": None, "from": email["from"], "subject": email["subject"], "body": email["body"]}
    threading.Thread(target=_run_agent, args=(new["id"], snapshot, message, revision), daemon=True).start()
    return {"change": new["id"]}


# --- Trip chat (read-only) ---------------------------------------------------------

class ChatQuestion(BaseModel):
    question: str
    history: list = []


@app.post("/api/trips/{trip_id}/chat")
def chat_about_trip(trip_id: str, body: ChatQuestion):
    with lock:
        snapshot = copy_state(state)   # the chat answers from a copy; it never changes the trip
    try:
        return chat.ask(snapshot, trip_id, body.question, body.history)
    except ValueError as error:
        raise HTTPException(400, str(error))


# --- Evals page ---------------------------------------------------------------------
# Eval runs never touch the working state: each starts from the base week in data/.

live_eval_runs = {}   # case id -> {"state": "running"} or {"state": "done", **trimmed run}


@app.get("/api/evals")
def get_evals():
    return evals_page.evals_view(live_eval_runs)


class Verdict(BaseModel):
    verdict: str | None = None
    note: str = ""


@app.post("/api/evals/{case_id}/verdict")
def save_eval_verdict(case_id: str, body: Verdict):
    try:
        return evals_page.save_verdict(case_id, body.verdict, body.note)
    except ValueError as error:
        raise HTTPException(400, str(error))


@app.post("/api/evals/{case_id}/run")
def run_eval_case(case_id: str):
    try:
        case = eval_runner.load_cases([case_id])[0]
    except SystemExit as error:
        raise HTTPException(404, str(error))
    if (live_eval_runs.get(case_id) or {}).get("state") == "running":
        raise HTTPException(409, f"Case {case_id} is already running")
    _start_eval(case)
    return {"case": case_id}


@app.post("/api/evals/run-all")
def run_all_evals():
    cases = [c for c in eval_runner.load_cases() if (live_eval_runs.get(c["id"]) or {}).get("state") != "running"]
    for case in cases:
        live_eval_runs[case["id"]] = {"state": "running"}
        bus.publish({"type": "eval", "case": case["id"], "state": "running"})

    def run_all():
        with ThreadPoolExecutor(max_workers=eval_runner.PARALLEL_RUNS) as pool:
            list(pool.map(_finish_eval, cases))

    threading.Thread(target=run_all, daemon=True).start()
    return {"cases": [c["id"] for c in cases]}


def _start_eval(case):
    live_eval_runs[case["id"]] = {"state": "running"}
    bus.publish({"type": "eval", "case": case["id"], "state": "running"})
    threading.Thread(target=_finish_eval, args=(case,), daemon=True).start()


def _finish_eval(case):
    run = eval_runner.run_case_once(case)
    live_eval_runs[case["id"]] = {"state": "done", "at": datetime.now().isoformat(timespec="seconds"),
                                  **eval_runner.trim_run(run)}
    bus.publish({"type": "eval", "case": case["id"], "state": "done", "passed": run["passed"]})


@app.post("/api/reset")
def reset():
    global state
    with lock:
        workspace.reset()
        state = workspace.load_working_state()
        bus.publish({"type": "reset"})
    return {"ok": True}


def _change(change_id):
    return next((c for c in state["queue"] if c["id"] == change_id), None)


server = None  # the running uvicorn server, so live connections can see when it is stopping


def main():
    global server
    port = int(os.environ.get("AF_PORT", "8000"))
    print(f"AgentFlight is running at http://localhost:{port}  (press Ctrl+C to stop)")
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning",
                                           timeout_graceful_shutdown=5))
    try:
        server.run()
    except KeyboardInterrupt:
        # Ctrl+C in a terminal reaches both uv and the server, so the server can get the
        # stop signal twice. That's a normal stop, not an error worth a traceback.
        pass
    print("AgentFlight stopped.")


if __name__ == "__main__":
    main()
