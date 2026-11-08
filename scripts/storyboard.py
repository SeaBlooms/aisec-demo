"""Capture the demo storyboard (deck backup slide) by replaying the curated traces in the real UI.

  uv run python scripts/storyboard.py

Starts a throwaway server with an isolated state dir (your stage policy is untouched), replays each
beat in headless Chrome, and writes deck/assets/demo-*.png. Re-run after re-curating traces with the
real model so the images match what you will show live.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "deck" / "assets"

BEATS = [
    ("demo-1-learn.png", "learn", "older", "benign", "learn__benign"),
    ("demo-2-attack.png", "none", "older", "poisoned_v2", "none__poisoned_v2__older"),
    ("demo-3-hardened-v1.png", "hardened_prompt", "older", "poisoned_v1", "hardened_prompt__poisoned_v1__older"),
    ("demo-3-hardened.png", "hardened_prompt", "older", "poisoned_v2", "hardened_prompt__poisoned_v2__older"),
    ("demo-3-newer.png", "none", "newer", "poisoned_v2", "none__poisoned_v2__newer"),
    ("demo-4-enforce.png", "enforce", "older", "poisoned_v2", "enforce__poisoned_v2__older"),
]
ENCORE = [
    ("demo-6-tool.png", "none", "newer", "poisoned_tool", "none__poisoned_tool__newer"),
    ("demo-7-tool-blocked.png", "enforce", "newer", "poisoned_tool", "enforce__poisoned_tool__newer"),
]
OUTCOMES = ".verdict.compromised, .verdict.blocked, .verdict.resisted, .verdict.learned, .verdict.error"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_up(url: str, timeout: float = 20) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(url + "api/config", timeout=1).status_code == 200:
                return
        except httpx.HTTPError:
            time.sleep(0.3)
    raise SystemExit("Demo server did not start")


def play(page: Page, url: str, mode: str, model: str, scenario: str, trace: str) -> None:
    page.goto(f"{url}?mode={mode}&model={model}&scenario={scenario}&replay=curated/{trace}.json&speed=4")
    page.wait_for_selector(OUTCOMES, timeout=60_000)
    page.wait_for_function("!document.getElementById('runBtn').disabled", timeout=30_000)
    page.wait_for_timeout(800)


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    port = free_port()
    url = f"http://127.0.0.1:{port}/"
    state = tempfile.mkdtemp(prefix="aisec-storyboard-")
    env = {**os.environ, "DEMO_STATE_DIR": state}
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "demo.server.app:app", "--port", str(port), "--log-level", "warning"],
        cwd=ROOT,
        env=env,
    )
    try:
        wait_up(url)
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="chrome", headless=True)
            page = browser.new_page(viewport={"width": 1600, "height": 900}, device_scale_factor=2)
            for filename, mode, model, scenario, trace in BEATS:
                play(page, url, mode, model, scenario, trace)
                if mode == "learn":
                    page.click("#approvePolicy")
                    page.wait_for_timeout(500)
                page.screenshot(path=str(ASSETS / filename))
                print("wrote", filename)
            page.click("#tabs button[data-tab=score]")
            page.wait_for_timeout(400)
            page.screenshot(path=str(ASSETS / "demo-5-scoreboard.png"))
            print("wrote demo-5-scoreboard.png")
            page.click("#tabs button[data-tab=doc]")
            page.click("#docRaw")
            page.wait_for_timeout(800)
            page.screenshot(path=str(ASSETS / "demo-0-hidden-text.png"))
            print("wrote demo-0-hidden-text.png")
            for filename, mode, model, scenario, trace in ENCORE:
                play(page, url, mode, model, scenario, trace)
                page.screenshot(path=str(ASSETS / filename))
                print("wrote", filename)
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=10)


if __name__ == "__main__":
    main()
