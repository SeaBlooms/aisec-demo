"""mcp_rugpull scenario: versioned descriptions and live tool-definition fingerprinting."""

from types import SimpleNamespace

import pytest

from demo import catalog, config
from demo.agent.policy import PolicyStore
from demo.agent.runner import RunConfig, default_tasks, execute_run
from demo.mcp_server import content


@pytest.fixture
def paths(tmp_path):
    return config.StatePaths(tmp_path / "policy", tmp_path / "sandbox")


def test_scenario_registered():
    assert content.SCENARIOS["mcp_rugpull"]["third_party"] is True
    assert content.SCENARIOS["mcp_rugpull"]["acme_doc"] == content.SCENARIOS["poisoned_tool"]["acme_doc"]


def test_benign_and_rugpulled_descriptions_differ():
    benign = content.third_party_description("benign")
    rugpulled = content.third_party_description("rugpulled")
    assert benign != rugpulled
    assert "do not send" in benign.lower()
    assert "list_contacts" not in benign
    assert "list_contacts" in rugpulled
    assert content.THIRD_PARTY_DESCRIPTION == rugpulled


def test_mcp_tool_version_state_defaults_and_round_trips(tmp_path):
    assert content.read_mcp_tool_version(tmp_path) == "benign"
    assert content.write_mcp_tool_version(tmp_path, "rugpulled") == "rugpulled"
    assert content.read_mcp_tool_version(tmp_path) == "rugpulled"
    content.write_mcp_tool_version(tmp_path, "benign")
    assert content.read_mcp_tool_version(tmp_path) == "benign"


def test_version_for_scenario_keeps_poisoned_tool_malicious(tmp_path):
    content.write_mcp_tool_version(tmp_path, "benign")
    assert content.mcp_tool_version_for_scenario("poisoned_tool", tmp_path) == "rugpulled"
    assert content.mcp_tool_version_for_scenario("mcp_rugpull", tmp_path) == "benign"
    content.write_mcp_tool_version(tmp_path, "rugpulled")
    assert content.mcp_tool_version_for_scenario("mcp_rugpull", tmp_path) == "rugpulled"


def test_fingerprint_tool_uses_live_toolset_id():
    schema = {"type": "object", "properties": {"account": {"type": "string"}}}
    tool = SimpleNamespace(
        toolset=SimpleNamespace(id="contact-enrich"),
        tool_def=SimpleNamespace(
            toolset_id="contact-enrich",
            name="enrich_contacts",
            description="benign",
            parameters_json_schema=schema,
        ),
    )
    digest, definition = catalog.fingerprint_tool("enrich_contacts", tool)
    assert definition["server"] == "contact-enrich"
    assert digest == catalog.tool_contents_hash(
        server="contact-enrich",
        name="enrich_contacts",
        description="benign",
        input_schema=schema,
    )


def test_fingerprint_tool_falls_back_to_toolset_id_when_tool_def_unset():
    """Lone MCPToolset leaves tool_def.toolset_id None; CombinedToolset fills it — hash must match."""
    schema = {"type": "object", "properties": {"q": {"type": "string"}}}
    alone = SimpleNamespace(
        toolset=SimpleNamespace(id="northwind-cs-tools"),
        tool_def=SimpleNamespace(
            toolset_id=None,
            name="search_kb",
            description="search",
            parameters_json_schema=schema,
        ),
    )
    combined = SimpleNamespace(
        toolset=SimpleNamespace(id="northwind-cs-tools"),
        tool_def=SimpleNamespace(
            toolset_id="northwind-cs-tools",
            name="search_kb",
            description="search",
            parameters_json_schema=schema,
        ),
    )
    h1, d1 = catalog.fingerprint_tool("search_kb", alone)
    h2, d2 = catalog.fingerprint_tool("search_kb", combined)
    assert d1["server"] == d2["server"] == "northwind-cs-tools"
    assert h1 == h2


def test_fingerprint_tool_fails_closed_without_tool_def():
    with pytest.raises(ValueError, match="no tool_def"):
        catalog.fingerprint_tool("enrich_contacts", SimpleNamespace())


async def test_mcp_rugpull_connects_third_party_and_honors_version_file(paths):
    """Simulated model still calls enrich_contacts when connected; outbox records tool_version."""
    content.write_mcp_tool_version(paths.policy_dir, "rugpulled")
    events = []

    async def emit(kind, **payload):
        events.append((kind, payload))

    cfg = RunConfig(
        "t",
        "none",
        "mcp_rugpull",
        default_tasks("none"),
        paths=paths,
        model=config.SIMULATED_MODEL,
    )
    summary = await execute_run(cfg, emit)
    assert summary["outcome"] == "compromised"
    outbox = (paths.sandbox_dir / "outbox.jsonl").read_text()
    assert '"tool_version": "rugpulled"' in outbox


async def test_guard_captures_live_hash_on_third_party_call(paths):
    content.write_mcp_tool_version(paths.policy_dir, "rugpulled")
    events = []

    async def emit(kind, **payload):
        events.append((kind, payload))

    cfg = RunConfig(
        "t",
        "none",
        "mcp_rugpull",
        default_tasks("none"),
        paths=paths,
        model=config.SIMULATED_MODEL,
    )
    await execute_run(cfg, emit)
    enrich_calls = [
        p["call"] for k, p in events if k == "tool_call" and p["call"]["tool"] == "enrich_contacts"
    ]
    assert enrich_calls
    call = enrich_calls[0]
    assert call["tool_hash"] and len(call["tool_hash"]) == 64
    assert call["tool_definition"]["server"] == "contact-enrich"
    assert "list_contacts" in call["tool_definition"]["description"]
    # Same description under poisoned_tool / rugpulled must hash stably from the live schema.
    assert call["tool_hash"] == catalog.tool_contents_hash(
        server=call["tool_definition"]["server"],
        name=call["tool_definition"]["name"],
        description=call["tool_definition"]["description"],
        input_schema=call["tool_definition"]["input_schema"],
    )


async def test_learn_pins_live_hashes_into_draft(paths):
    events = []

    async def emit(kind, **payload):
        events.append((kind, payload))

    cfg = RunConfig(
        "t",
        "learn",
        "benign",
        default_tasks("learn"),
        paths=paths,
        model=config.SIMULATED_MODEL,
    )
    await execute_run(cfg, emit)
    store = PolicyStore(paths.policy_dir)
    ledger = store.ledger()
    assert ledger
    assert all(e.get("tool_hash") for e in ledger)
    draft = store.draft_from_ledger()
    assert all(r.tool_hashes for r in draft.rules)


async def _learn_and_approve(paths):
    events = []

    async def emit(kind, **payload):
        events.append((kind, payload))

    cfg = RunConfig("t", "learn", "benign", default_tasks("learn"), paths=paths, model=config.SIMULATED_MODEL)
    await execute_run(cfg, emit)
    return PolicyStore(paths.policy_dir).approve_draft("tester")


async def test_seed_replaces_prior_enrich_rule_pinned_to_rugpulled_hash(paths):
    """Approving poisoned_tool from the Exceptions tab pins the rugpulled hash — seed must replace it."""
    await _learn_and_approve(paths)
    store = PolicyStore(paths.policy_dir)
    store.seed_enrich_contacts_exception(
        tool_hash=content.enrich_contacts_hash("rugpulled"),
        tool_description=content.third_party_description("rugpulled"),
        approver="tester",
    )
    store.seed_enrich_contacts_exception(
        tool_hash=content.enrich_contacts_hash("benign"),
        tool_description=content.third_party_description("benign"),
        approver="tester",
    )
    enrich_rules = [r for r in store.approved().rules if "enrich_contacts" in r.tools]
    assert len(enrich_rules) == 1
    assert enrich_rules[0].tool_hashes == [content.enrich_contacts_hash("benign")]


async def test_enforce_blocks_after_rugpull_hash_mismatch(paths):
    await _learn_and_approve(paths)
    store = PolicyStore(paths.policy_dir)
    store.seed_enrich_contacts_exception(
        tool_hash=content.enrich_contacts_hash("benign"),
        tool_description=content.third_party_description("benign"),
        approver="tester",
    )
    content.write_mcp_tool_version(paths.policy_dir, "rugpulled")

    events = []

    async def emit(kind, **payload):
        events.append((kind, payload))

    cfg = RunConfig(
        "t2",
        "enforce",
        "mcp_rugpull",
        default_tasks("enforce"),
        paths=paths,
        model=config.SIMULATED_MODEL,
    )
    summary = await execute_run(cfg, emit)
    assert summary["outcome"] == "blocked"
    assert summary["executed_exfiltration"] is False
    reasons = [p["reason"] for k, p in events if k == "policy_decision" and p["effect"] == "deny"]
    assert any("definition changed" in r for r in reasons)
    assert not (paths.sandbox_dir / "outbox.jsonl").exists()


async def test_enforce_allows_benign_hash_pinned_tool(paths):
    await _learn_and_approve(paths)
    store = PolicyStore(paths.policy_dir)
    store.seed_enrich_contacts_exception(
        tool_hash=content.enrich_contacts_hash("benign"),
        tool_description=content.third_party_description("benign"),
        approver="tester",
    )
    content.write_mcp_tool_version(paths.policy_dir, "benign")

    events = []

    async def emit(kind, **payload):
        events.append((kind, payload))

    cfg = RunConfig(
        "t2",
        "enforce",
        "mcp_rugpull",
        default_tasks("enforce"),
        paths=paths,
        model=config.SIMULATED_MODEL,
    )
    summary = await execute_run(cfg, emit)
    enrich_idx = next(
        i for i, (k, p) in enumerate(events) if k == "tool_call" and p["call"]["tool"] == "enrich_contacts"
    )
    decision = next(p for k, p in events[enrich_idx:] if k == "policy_decision")
    assert decision["effect"] == "allow"
    assert (paths.sandbox_dir / "outbox.jsonl").exists()
    assert summary["executed_exfiltration"] is True


def test_enrich_contacts_hash_helpers():
    assert len(content.enrich_contacts_hash("benign")) == 64
    assert content.enrich_contacts_hash("benign") != content.enrich_contacts_hash("rugpulled")
    assert content.enrich_contacts_definition("benign")["input_schema"]["required"] == ["account", "contacts"]
    status = content.mcp_tool_status(__import__("pathlib").Path("/tmp"), "mcp_rugpull")
    assert status["version"] == "benign"
    assert status["drifted"] is False


def test_summarize_mcp_rugpull_without_attempt_points_at_rugpull(tmp_path):
    from demo.agent.runner import CallRecord, RunConfig, summarize
    from demo.catalog import describe

    paths = config.StatePaths(tmp_path / "policy", tmp_path / "sandbox").ensure()
    cfg = RunConfig("t", "enforce", "mcp_rugpull", ["x"], paths=paths, model=config.SIMULATED_MODEL)
    summary = summarize(cfg, [CallRecord(describe("list_contacts", {"account": "Acme"}))])
    assert summary["outcome"] == "resisted"
    assert "benign" in summary["verdict"].lower()
    assert "rugpull" in summary["verdict"].lower()

    content.write_mcp_tool_version(paths.policy_dir, "rugpulled")
    summary = summarize(cfg, [CallRecord(describe("list_contacts", {"account": "Acme"}))])
    assert summary["outcome"] == "resisted"
    assert "did not call enrich_contacts" in summary["verdict"]
