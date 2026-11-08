"""End-to-end runs through the real MCP server with the offline simulated model."""

import pytest

from demo import config
from demo.agent.policy import PolicyStore
from demo.agent.runner import RunConfig, default_tasks, execute_run


async def run(mode, scenario, paths, model=config.SIMULATED_MODEL):
    events = []

    async def emit(kind, **payload):
        events.append((kind, payload))

    cfg = RunConfig("t", mode, scenario, default_tasks(mode), paths=paths, model=model)
    summary = await execute_run(cfg, emit)
    return summary, events


@pytest.fixture
def paths(tmp_path):
    return config.StatePaths(tmp_path / "policy", tmp_path / "sandbox")


@pytest.mark.parametrize(
    ("mode", "scenario", "outcome"),
    [
        ("none", "benign", "clean"),
        ("none", "poisoned_v1", "compromised"),
        ("hardened_prompt", "poisoned_v1", "resisted"),
        ("hardened_prompt", "poisoned_v2", "compromised"),
    ],
)
async def test_unenforced_outcomes(paths, mode, scenario, outcome):
    summary, _ = await run(mode, scenario, paths)
    assert summary["outcome"] == outcome


@pytest.mark.parametrize("scenario", ["poisoned_v1", "poisoned_v2"])
async def test_newer_simulated_model_resists(paths, scenario):
    summary, events = await run("none", scenario, paths, model=config.SIMULATED_NEWER_MODEL)
    assert summary["outcome"] == "resisted"
    started = next(p for k, p in events if k == "run_started")
    assert started["model_key"] == "newer"
    assert started["simulated"] is True


@pytest.mark.parametrize("model", [config.SIMULATED_MODEL, config.SIMULATED_NEWER_MODEL])
async def test_poisoned_tool_lands_without_runtime_control(paths, model):
    summary, _ = await run("none", "poisoned_tool", paths, model=model)
    assert summary["outcome"] == "compromised"
    assert summary["outside_domains"] == ["contact-enrich.io"]
    outbox = (paths.sandbox_dir / "outbox.jsonl").read_text()
    assert "third-party MCP tool" in outbox


async def test_enforce_blocks_unvetted_tool(paths):
    await run("learn", "benign", paths)
    PolicyStore(paths.policy_dir).approve_draft("tester")
    summary, events = await run("enforce", "poisoned_tool", paths)
    assert summary["outcome"] == "blocked"
    reasons = [p["reason"] for k, p in events if k == "policy_decision" and p["effect"] == "deny"]
    assert any("unvetted tool on contact-enrich.io" in r for r in reasons)
    assert not (paths.sandbox_dir / "outbox.jsonl").exists()


def test_model_labels():
    assert config.model_label("anthropic:claude-haiku-5-5") == "Claude Haiku 5.5"
    assert config.model_label("anthropic:claude-haiku-4-5-20251001") == "Claude Haiku 4.5"
    assert config.model_label(config.SIMULATED_NEWER_MODEL) == "Simulated newer model (offline)"


async def test_learn_then_enforce_blocks_both_injections(paths):
    summary, events = await run("learn", "benign", paths)
    assert summary["outcome"] == "learned"
    assert any(k == "policy_drafted" for k, _ in events)
    PolicyStore(paths.policy_dir).approve_draft("tester")

    for scenario in ("poisoned_v1", "poisoned_v2"):
        summary, events = await run("enforce", scenario, paths)
        assert summary["outcome"] == "blocked"
        assert summary["executed_exfiltration"] is False
        assert any(k == "exception_requested" for k, _ in events)
    assert not (paths.sandbox_dir / "outbox.jsonl").exists()
