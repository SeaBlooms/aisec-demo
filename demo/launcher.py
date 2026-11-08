"""One-command demo launcher with a preflight check.

uv run demo               preflight, then serve the UI on DEMO_PORT (default 8765)
uv run demo --open        same, and open the browser
uv run demo --fresh       clear policy/ledger/exceptions/outbox first (start of the talk)
uv run demo-preflight     checks only
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import sys
import webbrowser

import httpx

from demo import config

REQUIRED_TRACES = [
    "learn__benign",
    "none__poisoned_v2__older",
    "hardened_prompt__poisoned_v1__older",
    "hardened_prompt__poisoned_v2__older",
    "none__poisoned_v2__newer",
    "enforce__poisoned_v2__older",
    "none__poisoned_tool__newer",
    "enforce__poisoned_tool__newer",
]

OK, WARN, FAIL = "\u2714", "!", "\u2716"


def _line(status: str, msg: str) -> None:
    print(f"  {status} {msg}")


def check_model() -> tuple[str, str]:
    models = config.model_choices()
    if config.is_simulated():
        return (
            WARN,
            "DEMO_MODEL=simulated: offline scripted models (fine for rehearsal, not for the live talk)",
        )
    if not config.api_key_present():
        return WARN, "ANTHROPIC_API_KEY missing. Live runs will fail; use Replay."
    try:
        resp = httpx.get(
            "https://api.anthropic.com/v1/models",
            headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01"},
            params={"limit": 1000},
            timeout=5,
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        return WARN, f"Cannot reach the Anthropic API ({exc}). Live runs will fail; use Replay."
    ids = {m["id"] for m in resp.json().get("data", [])}
    missing = [m for m in models.values() if m.split(":", 1)[-1] not in ids]
    if missing:
        return FAIL, f"Not available to this API key: {', '.join(missing)}. Run `uv run demo-smoke`."
    return OK, " and ".join(f"{config.model_label(m)} ({k})" for k, m in models.items()) + " reachable"


def check_mcp() -> tuple[str, str]:
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport

    async def probe() -> int:
        transport = StdioTransport(sys.executable, ["-m", "demo.mcp_server.server"], cwd=str(config.ROOT), keep_alive=False)
        async with Client(transport) as client:
            return len(await client.list_tools())

    try:
        n = asyncio.run(asyncio.wait_for(probe(), timeout=20))
    except Exception as exc:
        return FAIL, f"MCP server failed to start: {exc}"
    return (OK, f"MCP server up, {n} tools") if n == 7 else (FAIL, f"MCP server exposes {n} tools, expected 7")


def check_port(port: int) -> tuple[str, str]:
    with socket.socket() as s:
        if s.connect_ex(("127.0.0.1", port)) == 0:
            return FAIL, f"Port {port} is already in use (another demo server running?)"
    return OK, f"Port {port} free"


def check_traces() -> tuple[str, str]:
    missing, simulated = [], []
    for name in REQUIRED_TRACES:
        path = config.CURATED_TRACES_DIR / f"{name}.json"
        if not path.exists():
            missing.append(name)
            continue
        meta = json.loads(path.read_text())["meta"]
        if meta.get("simulated") or config.is_simulated_model(meta.get("model") or ""):
            simulated.append(name)
    if missing:
        return (
            FAIL,
            f"Missing curated replay traces: {', '.join(missing)}. Run `uv run demo-reliability --curate`.",
        )
    if simulated:
        return WARN, (
            f"{len(simulated)}/{len(REQUIRED_TRACES)} curated traces are from the SIMULATED model. "
            "Re-curate with the real model before the talk."
        )
    return OK, "All curated replay traces present and from a real model"


def check_policy() -> tuple[str, str]:
    from demo.agent.policy import PolicyStore

    store = PolicyStore(config.StatePaths().policy_dir)
    approved = store.approved()
    if approved:
        return OK, f"Approved policy v{approved.version} present ({len(approved.rules)} rules)"
    return WARN, "No approved policy yet (expected at the start of the talk: run Learn mode first)"


def preflight(port: int) -> bool:
    print("Preflight")
    blocking = False
    for check in (check_model, check_mcp, lambda: check_port(port), check_traces, check_policy):
        status, msg = check()
        _line(status, msg)
        blocking |= status == FAIL
    print("  Fallback order: live run -> Replay (curated trace) -> recorded video\n")
    return not blocking


def preflight_main() -> None:
    sys.exit(0 if preflight(config.port()) else 1)


def main() -> None:
    p = argparse.ArgumentParser(description="Run the AI security panel demo")
    p.add_argument("--port", type=int, default=config.port())
    p.add_argument("--open", action="store_true", help="open the UI in the default browser")
    p.add_argument("--fresh", action="store_true", help="clear policy, ledger, exceptions and outbox first")
    p.add_argument("--skip-preflight", action="store_true")
    args = p.parse_args()

    if args.fresh:
        from demo.agent.policy import PolicyStore

        paths = config.StatePaths().ensure()
        PolicyStore(paths.policy_dir).reset()
        for f in paths.sandbox_dir.glob("*.jsonl"):
            f.unlink()
        print("Fresh state: policy, ledger, exceptions, audit log and outbox cleared.\n")

    if not args.skip_preflight and not preflight(args.port):
        print("Preflight failed. Fix the items marked with a cross, or pass --skip-preflight.")
        sys.exit(1)

    url = f"http://127.0.0.1:{args.port}/"
    print(f"Serving the demo at {url}  (Ctrl+C to stop)")
    if args.open:
        webbrowser.open(url)

    import uvicorn

    uvicorn.run("demo.server.app:app", host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
