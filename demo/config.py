from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DEMO_DIR = ROOT / "demo"

load_dotenv(ROOT / ".env")

DEFAULT_MODEL = "anthropic:claude-haiku-5-5"
DEFAULT_OLDER_MODEL = "anthropic:claude-haiku-4-5-20251001"
SIMULATED_MODEL = "simulated"
SIMULATED_NEWER_MODEL = "simulated:newer"

# The demo compares two model generations on the same attack. "older" runs the attack and default-deny
# beats; "newer" shows a model upgrade catching this attack, which is not a guarantee for the next one.
MODEL_KEYS = ("older", "newer")
_LABELS = {
    "claude-haiku-5-5": "Claude Haiku 5.5",
    "claude-haiku-4-5": "Claude Haiku 4.5",
    SIMULATED_MODEL: "Simulated older model (offline)",
    SIMULATED_NEWER_MODEL: "Simulated newer model (offline)",
}

AGENT_ID = "cs-assistant"
OUR_COMPANY = "Northwind"
OUR_DOMAIN = "northwind.io"

MCP_SERVER_SCRIPT = DEMO_DIR / "mcp_server" / "server.py"
DATA_DIR = DEMO_DIR / "mcp_server" / "data"
UI_DIR = DEMO_DIR / "ui"
TRACES_DIR = DEMO_DIR / "traces"
CURATED_TRACES_DIR = TRACES_DIR / "curated"
RUN_TRACES_DIR = TRACES_DIR / "runs"


def model_name() -> str:
    return os.getenv("DEMO_MODEL", "").strip() or DEFAULT_MODEL


def is_simulated() -> bool:
    return model_name() == SIMULATED_MODEL


def is_simulated_model(model: str) -> bool:
    return model.split(":", 1)[0] == SIMULATED_MODEL


def model_choices() -> dict[str, str]:
    """Model ID for each key in MODEL_KEYS. DEMO_MODEL=simulated swaps both for offline stand-ins."""
    if is_simulated():
        return {"older": SIMULATED_MODEL, "newer": SIMULATED_NEWER_MODEL}
    return {"older": os.getenv("DEMO_OLDER_MODEL", "").strip() or DEFAULT_OLDER_MODEL, "newer": model_name()}


def model_label(model: str) -> str:
    bare = model.split(":", 1)[1] if model.startswith("anthropic:") else model
    return next((label for prefix, label in _LABELS.items() if bare == prefix or bare.startswith(prefix + "-")), bare)


def model_key(model: str) -> str | None:
    simulated = {SIMULATED_MODEL: "older", SIMULATED_NEWER_MODEL: "newer"}
    return simulated.get(model) or next((k for k, m in model_choices().items() if m == model), None)


def port() -> int:
    return int(os.getenv("DEMO_PORT", "8765"))


def api_key_present() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY", "").strip())


def _state_root() -> Path | None:
    raw = os.getenv("DEMO_STATE_DIR", "").strip()
    return Path(raw) if raw else None


def _default_policy_dir() -> Path:
    root = _state_root()
    return root / "policy" if root else DEMO_DIR / "policy"


def _default_sandbox_dir() -> Path:
    root = _state_root()
    return root / "sandbox" if root else DEMO_DIR / "sandbox"


@dataclass(frozen=True)
class StatePaths:
    """Where a run reads policy and writes sandboxed side effects.

    The live demo uses demo/policy and demo/sandbox (or DEMO_STATE_DIR if set); the reliability
    harness and storyboard capture use isolated dirs so they never touch the policy approved on stage.
    """

    policy_dir: Path = field(default_factory=_default_policy_dir)
    sandbox_dir: Path = field(default_factory=_default_sandbox_dir)

    def ensure(self) -> StatePaths:
        self.policy_dir.mkdir(parents=True, exist_ok=True)
        self.sandbox_dir.mkdir(parents=True, exist_ok=True)
        return self
