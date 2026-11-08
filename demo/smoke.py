"""Smoke test: confirms the API key works and both pinned model IDs (older and newer) are real.

Usage: uv run demo-smoke
"""

from __future__ import annotations

import asyncio
import os
import sys

import httpx

from demo import config

ANTHROPIC_MODELS_URL = "https://api.anthropic.com/v1/models"


def list_anthropic_models(api_key: str) -> list[str]:
    resp = httpx.get(
        ANTHROPIC_MODELS_URL,
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
        params={"limit": 1000},
        timeout=15,
    )
    resp.raise_for_status()
    return [m["id"] for m in resp.json().get("data", [])]


async def one_call(model: str) -> str:
    from pydantic_ai import Agent

    result = await Agent(model).run("Reply with exactly: OK")
    return str(result.output).strip()


def run() -> int:
    models = config.model_choices()
    for key, model in models.items():
        print(f"{key:<5} = {model}")
    if config.is_simulated():
        print("Simulated models selected: no API call needed. Smoke test skipped.")
        return 0
    if not config.api_key_present():
        print("FAIL: ANTHROPIC_API_KEY is not set (add it to .env).")
        return 1

    try:
        ids = list_anthropic_models(os.environ["ANTHROPIC_API_KEY"])
    except httpx.HTTPError as exc:
        print(f"FAIL: could not list models: {exc}")
        return 1
    print(f"Haiku models visible to this key: {', '.join(sorted(i for i in ids if 'haiku' in i)) or '(none)'}")

    failed = False
    for key, model in models.items():
        model_id = model.split(":", 1)[-1]
        if model_id not in ids:
            env = "DEMO_MODEL" if key == "newer" else "DEMO_OLDER_MODEL"
            print(f"FAIL: {model_id!r} is not in the model list for this key. Set {env} in .env.")
            failed = True
            continue
        try:
            output = asyncio.run(one_call(model))
        except Exception as exc:  # surface any provider error verbatim for the operator
            print(f"FAIL: {model_id} errored: {type(exc).__name__}: {exc}")
            failed = True
            continue
        print(f"OK: {model_id} replied {output!r}")
    return 1 if failed else 0


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
