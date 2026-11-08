"""FastAPI server: runs the agent, streams events to the UI over SSE, and exposes policy state."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from demo import catalog, config
from demo.agent import policy as pol
from demo.agent.prompts import ATTACK_TASK, LEARN_TASKS, hardening_rules
from demo.agent.runner import MODES, RunConfig, default_tasks, execute_run
from demo.mcp_server import content
from demo.server import traces

PATHS = config.StatePaths().ensure()
STORE = pol.PolicyStore(PATHS.policy_dir)
PRESENTER = "Presenter"

app = FastAPI(title="aisec-demo")


class Run:
    def __init__(self, run_id: str, replay_of: str | None = None):
        self.id = run_id
        self.replay_of = replay_of
        self.events: list[dict[str, Any]] = []
        self.done = False
        self.cond = asyncio.Condition()
        self.trace_path: str | None = None

    async def emit(self, type: str, **payload: Any) -> None:
        event = {"seq": len(self.events), "type": type, "ts": time.time(), **payload}
        async with self.cond:
            self.events.append(event)
            self.cond.notify_all()

    async def finish(self) -> None:
        async with self.cond:
            self.done = True
            self.cond.notify_all()


RUNS: dict[str, Run] = {}


# --- runs -------------------------------------------------------------------------------------


class RunRequest(BaseModel):
    mode: str = "none"
    scenario: str = "benign"
    model: str = "older"
    tasks: list[str] | None = None


async def _execute(run: Run, cfg: RunConfig) -> None:
    try:
        await execute_run(cfg, run.emit)
    except Exception as exc:
        await run.emit("run_error", error=f"{type(exc).__name__}: {exc}")
    finally:
        run.trace_path = str(traces.save_run(run.events))
        await run.finish()


@app.post("/api/runs")
async def start_run(req: RunRequest) -> dict[str, str]:
    if req.mode not in MODES:
        raise HTTPException(400, f"mode must be one of {list(MODES)}")
    if req.scenario not in content.SCENARIOS:
        raise HTTPException(400, f"scenario must be one of {list(content.SCENARIOS)}")
    models = config.model_choices()
    if req.model not in models:
        raise HTTPException(400, f"model must be one of {list(models)}")
    run = Run(uuid.uuid4().hex[:8])
    RUNS[run.id] = run
    tasks = req.tasks or default_tasks(req.mode)
    cfg = RunConfig(run.id, req.mode, req.scenario, tasks, paths=PATHS, model=models[req.model])  # type: ignore[arg-type]
    asyncio.create_task(_execute(run, cfg))
    return {"run_id": run.id}


@app.get("/api/runs/{run_id}/events")
async def stream_events(run_id: str) -> StreamingResponse:
    run = RUNS.get(run_id)
    if run is None:
        raise HTTPException(404, "unknown run")

    async def gen() -> AsyncIterator[str]:
        idx = 0
        while True:
            async with run.cond:
                if idx >= len(run.events) and not run.done:
                    try:
                        await asyncio.wait_for(run.cond.wait(), timeout=15)
                    except TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                pending = run.events[idx:]
                finished = run.done
            for event in pending:
                yield f"data: {json.dumps(event, default=str)}\n\n"
            idx += len(pending)
            if finished and idx >= len(run.events):
                yield "event: end\ndata: {}\n\n"
                return

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.post("/api/runs/{run_id}/curate")
async def curate_run(run_id: str) -> dict[str, str]:
    run = RUNS.get(run_id)
    if run is None or not run.trace_path:
        raise HTTPException(404, "run not finished or unknown")
    dest = traces.curate(Path(run.trace_path))
    return {"curated": dest.name}


# --- replay -----------------------------------------------------------------------------------


class ReplayRequest(BaseModel):
    trace: str
    speed: float = Field(default=1.0, gt=0)


def _materialize(event: dict[str, Any]) -> None:
    """Re-apply a replayed event's side effects so the policy/exception panels stay interactive."""
    kind = event["type"]
    if kind == "ledger_entry":
        STORE.append_ledger(event["entry"])
    elif kind == "policy_drafted":
        STORE.save_draft(pol.Policy.from_yaml(event["yaml"]))
    elif kind == "exception_requested":
        STORE.add_exception(pol.ExceptionRequest(**event["request"]))
    elif kind == "sandbox_write" and event.get("tool") == "send_email":
        _append_outbox(event["args"])
    elif kind == "sandbox_write" and event.get("tool") in catalog.THIRD_PARTY_TOOLS:
        tool, args = event["tool"], event["args"]
        _append_outbox(
            {
                "to": f"{catalog.THIRD_PARTY_TOOLS[tool]} (third-party MCP tool)",
                "subject": f"{tool}: {args.get('account', '')}",
                "body": args.get("contacts", ""),
                "outside": True,
            }
        )


def _append_outbox(entry: dict[str, Any]) -> None:
    PATHS.sandbox_dir.mkdir(parents=True, exist_ok=True)
    with (PATHS.sandbox_dir / "outbox.jsonl").open("a") as fh:
        fh.write(json.dumps({"ts": time.time(), "run_id": "replay", **entry}) + "\n")


async def _replay(run: Run, trace_id: str, speed: float) -> None:
    try:
        data = traces.load(trace_id)
        prev_ts: float | None = None
        for event in data["events"]:
            if prev_ts is not None:
                await asyncio.sleep(min(max((event["ts"] - prev_ts) / speed, 0.15), 2.0))
            prev_ts = event["ts"]
            payload = {k: v for k, v in event.items() if k not in ("seq", "type", "ts")}
            if event["type"] == "run_started":
                payload |= {"replay": True, "replay_of": trace_id, "run_id": run.id}
            _materialize(event)
            await run.emit(event["type"], **payload)
    except Exception as exc:
        await run.emit("run_error", error=f"Replay failed: {exc}")
    finally:
        await run.finish()


@app.post("/api/replay")
async def start_replay(req: ReplayRequest) -> dict[str, str]:
    try:
        traces.resolve(req.trace)
    except FileNotFoundError as exc:
        raise HTTPException(404, "unknown trace") from exc
    run = Run(uuid.uuid4().hex[:8], replay_of=req.trace)
    RUNS[run.id] = run
    asyncio.create_task(_replay(run, req.trace, req.speed))
    return {"run_id": run.id}


@app.get("/api/traces")
async def list_traces() -> list[dict[str, Any]]:
    return traces.list_traces()


# --- policy, ledger, exceptions ---------------------------------------------------------------


def _policy_view(p: pol.Policy | None) -> dict[str, Any] | None:
    return {"yaml": p.to_yaml(), **p.to_dict()} if p else None


@app.get("/api/policy")
async def get_policy() -> dict[str, Any]:
    return {"approved": _policy_view(STORE.approved()), "draft": _policy_view(STORE.draft())}


@app.post("/api/policy/draft")
async def redraft() -> dict[str, Any]:
    if not STORE.ledger():
        raise HTTPException(400, "The behavior ledger is empty. Run learn mode first.")
    return _policy_view(STORE.draft_from_ledger()) or {}


@app.post("/api/policy/approve")
async def approve_policy() -> dict[str, Any]:
    try:
        return _policy_view(STORE.approve_draft(PRESENTER)) or {}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/ledger")
async def get_ledger() -> list[dict[str, Any]]:
    return STORE.ledger()


@app.get("/api/exceptions")
async def get_exceptions() -> list[dict[str, Any]]:
    return [asdict(e) for e in reversed(STORE.exceptions())]


class ExceptionDecision(BaseModel):
    approve: bool
    justification: str = ""
    destination_scope: list[str] = []
    ttl_minutes: int = 60


@app.post("/api/exceptions/{req_id}")
async def decide_exception(req_id: str, body: ExceptionDecision) -> dict[str, Any]:
    try:
        req = STORE.decide_exception(
            req_id, body.approve, PRESENTER, body.justification, body.destination_scope, body.ttl_minutes
        )
    except KeyError as exc:
        raise HTTPException(404, "unknown exception request") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return asdict(req)


@app.get("/api/audit")
async def get_audit() -> list[dict[str, Any]]:
    return list(reversed(STORE.audit_entries()))


# --- sandbox, docs, config, reset -------------------------------------------------------------


@app.get("/api/outbox")
async def get_outbox() -> list[dict[str, Any]]:
    path = PATHS.sandbox_dir / "outbox.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in reversed(path.read_text().splitlines()) if line.strip()]


@app.get("/api/doc")
async def get_doc(scenario: str = "benign") -> dict[str, Any]:
    raw = content.account_doc("acme", scenario) or ""
    visible, hidden = content.split_hidden(raw)
    agent_view = content.agent_text(raw)
    if content.SCENARIOS.get(scenario, {}).get("third_party"):
        agent_view += (
            f"\n\n---\nTool descriptions from the third-party MCP server \u201c{content.THIRD_PARTY_SERVER}\u201d"
            f" (the agent reads these; the CSM never sees them):\n\nenrich_contacts: {content.THIRD_PARTY_DESCRIPTION}"
        )
        hidden = [*hidden, content.THIRD_PARTY_DESCRIPTION]
    return {"scenario": scenario, "raw": agent_view, "visible": visible, "hidden": hidden}


@app.get("/api/config")
async def get_config() -> dict[str, Any]:
    return {
        "models": {k: {"id": m, "label": config.model_label(m)} for k, m in config.model_choices().items()},
        "default_model": "older",
        "simulated": config.is_simulated(),
        "api_key_present": config.api_key_present(),
        "modes": MODES,
        "scenarios": {k: v["label"] for k, v in content.SCENARIOS.items()},
        "attack_task": ATTACK_TASK,
        "learn_tasks": LEARN_TASKS,
        "hardening_rules": hardening_rules(),
    }


class ResetRequest(BaseModel):
    keep_policy: bool = False


@app.post("/api/reset")
async def reset(body: ResetRequest) -> dict[str, bool]:
    STORE.reset(keep_approved=body.keep_policy)
    for f in PATHS.sandbox_dir.glob("*.jsonl"):
        f.unlink()
    return {"ok": True}


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(config.UI_DIR / "index.html", headers={"Cache-Control": "no-store"})


class UIFiles(StaticFiles):
    """UI assets change between rehearsals; make the browser revalidate them on every load."""

    def file_response(self, *args: Any, **kwargs: Any) -> Any:
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


app.mount("/static", UIFiles(directory=config.UI_DIR), name="static")
