"""Record the last-resort fallback video: the whole demo run of show, replayed in the real UI.

  uv run python scripts/record_demo.py            # -> deck/out/demo-fallback.mp4

Uses the curated traces (re-curate with the real model first), an isolated state dir, and headless
Chrome. Narrate over the video live if both the live run and Replay are unavailable.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from storyboard import OUTCOMES, free_port, wait_up  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "deck" / "out" / "demo-fallback.mp4"
SIZE = {"width": 1600, "height": 900}


def replay(page: Page, mode: str, model: str, scenario: str, trace: str, speed: str = "1") -> None:
    page.evaluate(
        """([m, md, s, t, sp]) => {
            setMode(m); setModel(md); setScenario(s);
            const sel = document.getElementById('traceSelect');
            if (![...sel.options].some(o => o.value === t)) sel.appendChild(new Option(t, t));
            sel.value = t;
            document.getElementById('speedSelect').value = sp;
            startReplay();
        }""",
        [mode, model, scenario, f"curated/{trace}.json", speed],
    )
    page.wait_for_function("document.getElementById('runBtn').disabled", timeout=10_000)
    page.wait_for_selector(OUTCOMES, timeout=90_000)
    page.wait_for_function("!document.getElementById('runBtn').disabled", timeout=30_000)


def tab(page: Page, name: str, hold_ms: int = 2500) -> None:
    page.click(f"#tabs button[data-tab={name}]")
    page.wait_for_timeout(hold_ms)


def run_of_show(page: Page) -> None:
    page.wait_for_timeout(1500)

    # 1. Learn, review, approve
    replay(page, "learn", "older", "benign", "learn__benign", speed="2")
    tab(page, "ledger", 3000)
    tab(page, "policy", 2000)
    page.click("#approvePolicy")
    page.wait_for_timeout(3000)

    # 2. Disguised attack with no defense, then show the hidden text and the outbox
    replay(page, "none", "older", "poisoned_v2", "none__poisoned_v2__older")
    page.wait_for_timeout(2000)
    tab(page, "doc", 1000)
    page.click("#docRaw")
    page.wait_for_timeout(4000)
    tab(page, "outbox", 3500)

    # 3. Fix the model: show the added rules, the hardened prompt still loses; the newer model catches it this time
    page.click("#modeButtons button[data-mode=hardened_prompt]")
    page.wait_for_timeout(5000)
    replay(page, "hardened_prompt", "older", "poisoned_v2", "hardened_prompt__poisoned_v2__older")
    page.wait_for_timeout(3000)
    replay(page, "none", "newer", "poisoned_v2", "none__poisoned_v2__newer")
    page.wait_for_timeout(3500)

    # 4. Default deny on the older model: the send is blocked and filed as an exception request
    replay(page, "enforce", "older", "poisoned_v2", "enforce__poisoned_v2__older")
    page.wait_for_timeout(2500)
    card = page.locator("#panel-exceptions .exc[data-id]").first
    card.locator("textarea").fill("Send QBR invite to Acme stakeholders, ticket CS-1042")
    page.wait_for_timeout(1200)
    card.locator("button.approve").click()
    page.wait_for_timeout(3000)

    # 5. Scoreboard
    tab(page, "score", 6000)


def main() -> None:
    port = free_port()
    url = f"http://127.0.0.1:{port}/"
    state = tempfile.mkdtemp(prefix="aisec-record-")
    video_dir = Path(tempfile.mkdtemp(prefix="aisec-video-"))
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "demo.server.app:app", "--port", str(port), "--log-level", "warning"],
        cwd=ROOT,
        env={**os.environ, "DEMO_STATE_DIR": state},
    )
    try:
        wait_up(url)
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="chrome", headless=True)
            context = browser.new_context(viewport=SIZE, record_video_dir=str(video_dir), record_video_size=SIZE)
            page = context.new_page()
            page.goto(url)
            run_of_show(page)
            context.close()
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=10)

    webm = next(video_dir.glob("*.webm"))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    if shutil.which("ffmpeg"):
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(webm), "-c:v", "libx264", "-pix_fmt", "yuv420p", str(OUT)],
            check=True,
        )
        print("wrote", OUT.relative_to(ROOT))
    else:
        dest = OUT.with_suffix(".webm")
        shutil.copyfile(webm, dest)
        print("ffmpeg not found; wrote", dest.relative_to(ROOT))


if __name__ == "__main__":
    main()
