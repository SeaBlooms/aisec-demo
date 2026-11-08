"""Trace store: every run's events saved as JSON, plus curated traces used as the on-stage fallback."""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from demo.config import CURATED_TRACES_DIR, RUN_TRACES_DIR


def _meta(events: list[dict[str, Any]]) -> dict[str, Any]:
    started = next((e for e in events if e["type"] == "run_started"), {})
    finished = next((e for e in events if e["type"] == "run_finished"), {})
    return {
        "run_id": started.get("run_id"),
        "mode": started.get("mode"),
        "scenario": started.get("scenario"),
        "model": started.get("model"),
        "model_key": started.get("model_key"),
        "simulated": started.get("simulated", False),
        "outcome": finished.get("outcome"),
        "verdict": finished.get("verdict"),
        "created_at": datetime.fromtimestamp(started.get("ts", 0)).isoformat(timespec="seconds"),
    }


def save_run(events: list[dict[str, Any]], directory: Path = RUN_TRACES_DIR) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    meta = _meta(events)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = directory / f"{stamp}_{meta['mode']}_{meta['scenario']}_{meta['model_key'] or 'model'}_{meta['run_id']}.json"
    path.write_text(json.dumps({"meta": meta, "events": events}, indent=1, default=str))
    return path


def curated_name(mode: str, scenario: str, model_key: str | None) -> str:
    """learn__benign.json for the baseline, otherwise <mode>__<scenario>__<model key>.json."""
    return f"{mode}__{scenario}.json" if mode == "learn" else f"{mode}__{scenario}__{model_key or 'older'}.json"


def curate(path: Path, name: str | None = None) -> Path:
    """Copy a run trace into the curated set, named by curated_name() by default."""
    data = json.loads(path.read_text())
    meta = data["meta"]
    CURATED_TRACES_DIR.mkdir(parents=True, exist_ok=True)
    dest = CURATED_TRACES_DIR / (name or curated_name(meta["mode"], meta["scenario"], meta.get("model_key")))
    shutil.copyfile(path, dest)
    return dest


def list_traces(limit_runs: int = 30) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for kind, directory in (("curated", CURATED_TRACES_DIR), ("run", RUN_TRACES_DIR)):
        if not directory.exists():
            continue
        files = sorted(directory.glob("*.json"), reverse=kind == "run")
        if kind == "run":
            files = files[:limit_runs]
        for f in files:
            try:
                meta = json.loads(f.read_text())["meta"]
            except (json.JSONDecodeError, KeyError):
                continue
            out.append({"id": f"{kind}/{f.name}", "kind": kind, "name": f.stem, **meta})
    return out


def resolve(trace_id: str) -> Path:
    kind, _, name = trace_id.partition("/")
    base = {"curated": CURATED_TRACES_DIR, "run": RUN_TRACES_DIR}.get(kind)
    if base is None or "/" in name or ".." in name:
        raise FileNotFoundError(trace_id)
    path = base / name
    if not path.exists():
        raise FileNotFoundError(trace_id)
    return path


def load(trace_id: str) -> dict[str, Any]:
    return json.loads(resolve(trace_id).read_text())
