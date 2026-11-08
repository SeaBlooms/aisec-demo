"""Default-deny policy engine for agent tool calls.

Default deny applied to agents: nothing runs unless a rule explicitly allows it.
Rules come from two places only:
  1. A baseline drafted from learn-mode observations and approved by a human.
  2. Exceptions: a blocked action, a written justification, a narrow scope and an expiry.

This module is deliberately pure and deterministic; it never consults the model.
"""

from __future__ import annotations

import fnmatch
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import yaml

from demo.catalog import ToolCallInfo
from demo.config import AGENT_ID

Effect = Literal["allow", "deny"]


def utcnow() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat(timespec="seconds") if dt else None


def _parse(ts: str | None) -> datetime | None:
    return datetime.fromisoformat(ts) if ts else None


@dataclass
class Rule:
    id: str
    tools: list[str]
    action_classes: list[str]
    resources: list[str] = field(default_factory=lambda: ["*"])
    # None means "this rule permits no outbound destinations". Egress must be scoped explicitly.
    destinations: list[str] | None = None
    agents: list[str] = field(default_factory=lambda: [AGENT_ID])
    effect: Effect = "allow"
    source: Literal["learn", "exception", "manual"] = "manual"
    justification: str = ""
    approved_by: str = ""
    expires_at: str | None = None
    observed_count: int = 0

    def expired(self, now: datetime) -> bool:
        exp = _parse(self.expires_at)
        return exp is not None and now >= exp

    def matches_identity(self, call: ToolCallInfo) -> bool:
        return (
            any(fnmatch.fnmatchcase(call.agent, p) for p in self.agents)
            and any(fnmatch.fnmatchcase(call.tool, p) for p in self.tools)
            and call.action_class in self.action_classes
        )

    def matches_resource(self, call: ToolCallInfo) -> bool:
        return any(fnmatch.fnmatchcase(call.resource, p) for p in self.resources)

    def destinations_in_scope(self, call: ToolCallInfo) -> list[str]:
        """Return the call's destinations that this rule does NOT permit."""
        if not call.destinations:
            return []
        allowed = self.destinations or []
        return [d for d in call.destinations if not any(fnmatch.fnmatchcase(d, p) for p in allowed)]


@dataclass
class Policy:
    agent: str = AGENT_ID
    default: Literal["deny"] = "deny"
    version: int = 1
    approved_by: str = ""
    approved_at: str | None = None
    rules: list[Rule] = field(default_factory=list)
    # Writes seen in learn mode. Never active: they need a justification before becoming rules.
    proposed: list[Rule] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Policy:
        data = dict(data)
        data["rules"] = [Rule(**r) for r in data.get("rules") or []]
        data["proposed"] = [Rule(**r) for r in data.get("proposed") or []]
        return cls(**data)

    def to_yaml(self) -> str:
        header = "# Default-deny policy for the Customer Success agent.\n# Anything not explicitly allowed below is blocked.\n"
        return header + yaml.safe_dump(self.to_dict(), sort_keys=False, width=100)

    @classmethod
    def from_yaml(cls, text: str) -> Policy:
        return cls.from_dict(yaml.safe_load(text) or {})


@dataclass
class Decision:
    effect: Effect
    reason: str
    rule_id: str | None = None

    @property
    def allowed(self) -> bool:
        return self.effect == "allow"


def evaluate(policy: Policy | None, call: ToolCallInfo, now: datetime | None = None) -> Decision:
    """Default deny: allow only if an unexpired rule matches tool, action, resource and destinations."""
    now = now or utcnow()
    if policy is None:
        return Decision("deny", "No approved policy: everything is denied by default.")

    near_miss: str | None = None
    for rule in policy.rules:
        if rule.effect != "allow" or not rule.matches_identity(call):
            continue
        if rule.expired(now):
            near_miss = near_miss or f"Rule {rule.id} allowed this but expired at {rule.expires_at}."
            continue
        if not rule.matches_resource(call):
            near_miss = near_miss or f"Rule {rule.id} allows {call.tool} only on {', '.join(rule.resources)}."
            continue
        outside = rule.destinations_in_scope(call)
        if outside:
            scope = ", ".join(rule.destinations or []) or "no outbound destinations"
            near_miss = near_miss or (f"Rule {rule.id} allows {call.tool} only to {scope}; {', '.join(outside)} is out of scope.")
            continue
        return Decision("allow", f"Allowed by rule {rule.id} ({rule.source}).", rule.id)

    if call.resource.startswith("unknown:"):
        base = f"{call.tool} is not in the approved baseline (unvetted tool"
        base += f" on {', '.join(call.destinations)})" if call.destinations else ")"
    else:
        base = f"No rule allows {call.tool} ({call.action_class})"
        if call.destinations:
            base += f" to {', '.join(call.destinations)}"
    return Decision("deny", near_miss or f"{base}. Default deny.")


# --- Learn mode -------------------------------------------------------------------------------


def generalize_resource(resource: str) -> str:
    """crm:account/acme/doc -> crm:account/*/doc, so a baseline covers the same kind of record."""
    parts = resource.split("/")
    if len(parts) >= 3 and parts[0].startswith("crm:account"):
        parts[1] = "*"
    return "/".join(parts)


def draft_policy(observations: list[dict[str, Any]], agent: str = AGENT_ID) -> Policy:
    """Turn observed tool calls into a draft baseline.

    Reads become allow rules scoped to the resource types actually observed.
    Writes are never auto-allowed: they become proposals that need a justification.
    """
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for obs in observations:
        key = (obs["tool"], obs["action_class"])
        g = grouped.setdefault(key, {"resources": set(), "destinations": set(), "count": 0})
        g["resources"].add(generalize_resource(obs["resource"]))
        g["destinations"].update(obs.get("destinations") or [])
        g["count"] += 1

    policy = Policy(agent=agent)
    for (tool, action), g in sorted(grouped.items()):
        rule = Rule(
            id=f"{'allow' if action == 'read' else 'proposed'}-{tool}",
            tools=[tool],
            action_classes=[action],
            resources=sorted(g["resources"]),
            destinations=sorted(g["destinations"]) or None,
            agents=[agent],
            source="learn",
            justification=f"Observed {g['count']}x in learn mode",
            observed_count=g["count"],
        )
        (policy.rules if action == "read" else policy.proposed).append(rule)
    return policy


# --- Exceptions -------------------------------------------------------------------------------


@dataclass
class ExceptionRequest:
    id: str
    created_at: str
    tool: str
    action_class: str
    resource: str
    destinations: list[str]
    args_preview: str
    deny_reason: str
    run_id: str = ""
    status: Literal["pending", "approved", "rejected"] = "pending"
    justification: str = ""
    approved_scope: list[str] | None = None
    expires_at: str | None = None
    decided_by: str = ""
    rule_id: str | None = None


def new_exception_request(call: ToolCallInfo, deny_reason: str, run_id: str = "") -> ExceptionRequest:
    preview = json.dumps(call.args)[:400]
    return ExceptionRequest(
        id=f"exc-{uuid.uuid4().hex[:6]}",
        created_at=_iso(utcnow()) or "",
        tool=call.tool,
        action_class=call.action_class,
        resource=call.resource,
        destinations=call.destinations,
        args_preview=preview,
        deny_reason=deny_reason,
        run_id=run_id,
    )


def exception_rule(
    req: ExceptionRequest,
    justification: str,
    destination_scope: list[str] | None,
    ttl_minutes: int,
    approver: str,
    now: datetime | None = None,
) -> Rule:
    if not justification.strip():
        raise ValueError("A justification is required to approve an exception.")
    if ttl_minutes <= 0:
        raise ValueError("Exceptions must be time-bound (ttl_minutes > 0).")
    now = now or utcnow()
    scope = [s.strip() for s in destination_scope or [] if s.strip()] or None
    return Rule(
        id=f"exception-{req.id}",
        tools=[req.tool],
        action_classes=[req.action_class],
        resources=[req.resource],
        destinations=scope,
        source="exception",
        justification=justification.strip(),
        approved_by=approver,
        expires_at=_iso(now + timedelta(minutes=ttl_minutes)),
    )


# --- Persistence ------------------------------------------------------------------------------


class PolicyStore:
    """File-backed policy state: approved policy, draft, learn ledger, exceptions and audit log."""

    def __init__(self, policy_dir: Path):
        self.dir = policy_dir
        self.dir.mkdir(parents=True, exist_ok=True)

    approved_path = property(lambda self: self.dir / "policy.approved.yaml")
    draft_path = property(lambda self: self.dir / "policy.draft.yaml")
    ledger_path = property(lambda self: self.dir / "ledger.json")
    exceptions_path = property(lambda self: self.dir / "exceptions.json")
    audit_path = property(lambda self: self.dir / "audit.jsonl")

    # policy
    def approved(self) -> Policy | None:
        return Policy.from_yaml(self.approved_path.read_text()) if self.approved_path.exists() else None

    def save_approved(self, policy: Policy) -> None:
        self.approved_path.write_text(policy.to_yaml())

    def draft(self) -> Policy | None:
        return Policy.from_yaml(self.draft_path.read_text()) if self.draft_path.exists() else None

    def save_draft(self, policy: Policy) -> None:
        self.draft_path.write_text(policy.to_yaml())

    def approve_draft(self, approver: str) -> Policy:
        draft = self.draft()
        if draft is None:
            raise ValueError("No draft policy to approve. Run learn mode first.")
        draft.approved_by = approver
        draft.approved_at = _iso(utcnow())
        current = self.approved()
        if current:
            draft.version = current.version + 1
            # Keep any still-valid exception rules granted against the previous version.
            draft.rules.extend(r for r in current.rules if r.source == "exception")
        self.save_approved(draft)
        self.draft_path.unlink(missing_ok=True)
        self.audit("policy_approved", {"approver": approver, "version": draft.version})
        return draft

    # ledger
    def ledger(self) -> list[dict[str, Any]]:
        return json.loads(self.ledger_path.read_text()) if self.ledger_path.exists() else []

    def observe(self, call: ToolCallInfo, run_id: str = "") -> dict[str, Any]:
        entry = {**call.to_dict(), "ts": _iso(utcnow()), "run_id": run_id}
        entry.pop("args", None)
        entries = self.ledger()
        entries.append(entry)
        self.ledger_path.write_text(json.dumps(entries, indent=2))
        return entry

    def append_ledger(self, entry: dict[str, Any]) -> None:
        entries = self.ledger()
        entries.append(entry)
        self.ledger_path.write_text(json.dumps(entries, indent=2))

    def draft_from_ledger(self) -> Policy:
        draft = draft_policy(self.ledger())
        self.save_draft(draft)
        self.audit("policy_drafted", {"rules": len(draft.rules), "proposed": len(draft.proposed)})
        return draft

    # exceptions
    def exceptions(self) -> list[ExceptionRequest]:
        if not self.exceptions_path.exists():
            return []
        return [ExceptionRequest(**e) for e in json.loads(self.exceptions_path.read_text())]

    def _save_exceptions(self, items: list[ExceptionRequest]) -> None:
        self.exceptions_path.write_text(json.dumps([asdict(e) for e in items], indent=2))

    def add_exception(self, req: ExceptionRequest) -> ExceptionRequest:
        items = self.exceptions()
        if not any(e.id == req.id for e in items):
            items.append(req)
            self._save_exceptions(items)
        return req

    def decide_exception(
        self,
        req_id: str,
        approve: bool,
        approver: str,
        justification: str = "",
        destination_scope: list[str] | None = None,
        ttl_minutes: int = 60,
    ) -> ExceptionRequest:
        items = self.exceptions()
        req = next((e for e in items if e.id == req_id), None)
        if req is None:
            raise KeyError(req_id)
        if req.status != "pending":
            raise ValueError(f"Exception {req_id} is already {req.status}.")
        if approve:
            policy = self.approved()
            if policy is None:
                raise ValueError("Approve a baseline policy before granting exceptions.")
            rule = exception_rule(req, justification, destination_scope, ttl_minutes, approver)
            policy.rules.append(rule)
            self.save_approved(policy)
            req.status, req.rule_id, req.expires_at = "approved", rule.id, rule.expires_at
            req.approved_scope = rule.destinations
        else:
            req.status = "rejected"
        req.justification, req.decided_by = justification.strip(), approver
        self._save_exceptions(items)
        self.audit(f"exception_{req.status}", asdict(req))
        return req

    # audit
    def audit(self, kind: str, payload: dict[str, Any]) -> None:
        with self.audit_path.open("a") as fh:
            fh.write(json.dumps({"ts": _iso(utcnow()), "kind": kind, **payload}) + "\n")

    def audit_entries(self, limit: int = 200) -> list[dict[str, Any]]:
        if not self.audit_path.exists():
            return []
        lines = self.audit_path.read_text().splitlines()[-limit:]
        return [json.loads(line) for line in lines if line.strip()]

    def reset(self, keep_approved: bool = False) -> None:
        paths = [self.draft_path, self.ledger_path, self.exceptions_path, self.audit_path]
        if not keep_approved:
            paths.append(self.approved_path)
        for p in paths:
            p.unlink(missing_ok=True)
