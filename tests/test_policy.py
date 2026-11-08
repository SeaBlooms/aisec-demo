from datetime import timedelta

import pytest

from demo.agent import policy as pol
from demo.catalog import contact_count, describe


def baseline() -> pol.Policy:
    observations = [
        {**describe("get_account_doc", {"account": "Globex"}).to_dict()},
        {**describe("get_account_doc", {"account": "Acme Corp"}).to_dict()},
        {**describe("search_kb", {"query": "renewal"}).to_dict()},
        {**describe("list_contacts", {"account": "Acme Corp"}).to_dict()},
    ]
    return pol.draft_policy(observations)


ATTACK = describe("send_email", {"to": "backup@contact-archive.net", "subject": "x", "body": "y"})
CUSTOMER_EMAIL = describe("send_email", {"to": "maria.okafor@acme.com", "subject": "QBR", "body": "Agenda"})


def test_no_policy_denies_everything():
    d = pol.evaluate(None, describe("search_kb", {"query": "x"}))
    assert d.effect == "deny"


def test_draft_allows_observed_reads_generalized_across_accounts():
    p = baseline()
    assert {r.id for r in p.rules} == {"allow-get_account_doc", "allow-search_kb", "allow-list_contacts"}
    assert pol.evaluate(p, describe("get_account_doc", {"account": "Initech"})).allowed
    assert pol.evaluate(p, describe("list_contacts", {"account": "Globex"})).allowed


def test_draft_never_auto_allows_writes():
    obs = [describe("create_task", {"title": "Follow up"}).to_dict()]
    p = pol.draft_policy(obs)
    assert p.rules == []
    assert [r.id for r in p.proposed] == ["proposed-create_task"]
    assert not pol.evaluate(p, describe("create_task", {"title": "Follow up"})).allowed


def test_default_deny_blocks_unobserved_write():
    d = pol.evaluate(baseline(), ATTACK)
    assert d.effect == "deny"
    assert "contact-archive.net" in d.reason
    assert "Default deny" in d.reason


def test_default_deny_blocks_unobserved_read_tool_and_delete():
    p = pol.draft_policy([describe("search_kb", {"query": "x"}).to_dict()])
    assert not pol.evaluate(p, describe("list_contacts", {"account": "Acme"})).allowed
    assert not pol.evaluate(p, describe("delete_doc", {"account": "Acme"})).allowed


def test_unknown_tool_is_denied():
    assert not pol.evaluate(baseline(), describe("exfiltrate_everything", {})).allowed


def test_egress_requires_explicit_destination_scope():
    p = baseline()
    p.rules.append(pol.Rule(id="send-unscoped", tools=["send_email"], action_classes=["create"]))
    d = pol.evaluate(p, CUSTOMER_EMAIL)
    assert not d.allowed
    assert "no outbound destinations" in d.reason


def test_exception_scoped_to_customer_domain_still_blocks_attacker():
    p = baseline()
    req = pol.new_exception_request(ATTACK, "denied")
    rule = pol.exception_rule(req, "QBR invites to Acme only", ["acme.com"], ttl_minutes=60, approver="tester")
    p.rules.append(rule)
    assert pol.evaluate(p, CUSTOMER_EMAIL).allowed
    blocked = pol.evaluate(p, ATTACK)
    assert not blocked.allowed
    assert "out of scope" in blocked.reason


def test_exception_expires():
    p = baseline()
    req = pol.new_exception_request(CUSTOMER_EMAIL, "denied")
    now = pol.utcnow()
    p.rules.append(pol.exception_rule(req, "QBR invite", ["acme.com"], ttl_minutes=30, approver="tester", now=now))
    assert pol.evaluate(p, CUSTOMER_EMAIL, now=now + timedelta(minutes=29)).allowed
    late = pol.evaluate(p, CUSTOMER_EMAIL, now=now + timedelta(minutes=31))
    assert not late.allowed
    assert "expired" in late.reason


def test_exception_requires_justification_and_ttl():
    req = pol.new_exception_request(CUSTOMER_EMAIL, "denied")
    with pytest.raises(ValueError):
        pol.exception_rule(req, "  ", ["acme.com"], 60, "tester")
    with pytest.raises(ValueError):
        pol.exception_rule(req, "ok", ["acme.com"], 0, "tester")


def test_policy_yaml_round_trip():
    p = baseline()
    again = pol.Policy.from_yaml(p.to_yaml())
    assert again.to_dict() == p.to_dict()


def test_store_flow_learn_approve_exception(tmp_path):
    store = pol.PolicyStore(tmp_path)
    for name, args in [("get_account_doc", {"account": "Acme"}), ("search_kb", {"query": "x"})]:
        store.observe(describe(name, args))
    store.draft_from_ledger()
    approved = store.approve_draft("tester")
    assert approved.version == 1 and approved.approved_by == "tester"

    req = store.add_exception(pol.new_exception_request(CUSTOMER_EMAIL, "denied"))
    with pytest.raises(ValueError):
        store.decide_exception(req.id, True, "tester", justification="")
    done = store.decide_exception(req.id, True, "tester", "QBR invite to sponsor", ["acme.com"], 60)
    assert done.status == "approved"
    assert pol.evaluate(store.approved(), CUSTOMER_EMAIL).allowed
    with pytest.raises(ValueError):
        store.decide_exception(req.id, False, "tester")

    # Re-approving a new baseline keeps granted exceptions.
    store.draft_from_ledger()
    v2 = store.approve_draft("tester")
    assert v2.version == 2
    assert any(r.source == "exception" for r in v2.rules)

    kinds = [e["kind"] for e in store.audit_entries()]
    assert kinds.count("policy_approved") == 2 and "exception_approved" in kinds


def test_contact_count_ignores_the_recipient():
    body = "a@acme.com, b@acme.com"
    assert contact_count(describe("send_email", {"to": "x@evil.net", "subject": "s", "body": body})) == 2
    assert contact_count(describe("enrich_contacts", {"account": "Acme", "contacts": body})) == 2
