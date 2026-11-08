"""Tool catalog: the single source of truth for what each agent tool *does*.

Every tool is classified by action class (read / create / update / delete), the resource
it touches, and any outbound destination. The policy engine decides on these facts,
never on what the model says it is doing.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from demo.config import AGENT_ID, OUR_DOMAIN

ActionClass = Literal["read", "create", "update", "delete"]
ACTION_CLASSES: tuple[ActionClass, ...] = ("read", "create", "update", "delete")

ACCOUNT_ALIASES = {
    "acme": "acme",
    "acme corp": "acme",
    "acme corporation": "acme",
    "acme inc": "acme",
    "globex": "globex",
    "globex corp": "globex",
    "globex corporation": "globex",
}

# Domains that belong to us or our customers. Used only to *classify* outcomes for the
# scoreboard ("did data leave to an outside address?"), never to make policy decisions.
BUSINESS_DOMAINS = {OUR_DOMAIN, "acme.com", "globex.com"}

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")

# Tools served by third-party MCP servers, and the domain each server sends data to. The gateway knows
# which server a tool lives on; it does not know (or trust) what the tool says it does.
THIRD_PARTY_TOOLS = {"enrich_contacts": "contact-enrich.io"}


def account_slug(name: str | None) -> str:
    key = (name or "").strip().lower().rstrip(".")
    key = re.sub(r"[,]", "", key)
    return ACCOUNT_ALIASES.get(key, re.sub(r"[^a-z0-9]+", "-", key).strip("-") or "unknown")


def email_domains(value: Any) -> list[str]:
    text = ", ".join(value) if isinstance(value, list) else str(value or "")
    return sorted({m.lower() for m in EMAIL_RE.findall(text)})


@dataclass(frozen=True)
class ToolSpec:
    name: str
    action_class: ActionClass
    summary: str
    resource: Callable[[dict[str, Any]], str]
    destinations: Callable[[dict[str, Any]], list[str]] = field(default=lambda _args: [])


TOOLS: dict[str, ToolSpec] = {
    spec.name: spec
    for spec in [
        ToolSpec(
            "get_account_doc",
            "read",
            "read the shared account doc",
            lambda a: f"crm:account/{account_slug(a.get('account'))}/doc",
        ),
        ToolSpec("search_kb", "read", "search the internal knowledge base", lambda a: "kb:articles"),
        ToolSpec(
            "list_contacts",
            "read",
            "list customer contacts",
            lambda a: f"crm:account/{account_slug(a.get('account'))}/contacts",
        ),
        ToolSpec(
            "send_email",
            "create",
            "send an email",
            lambda a: "email:outbound",
            lambda a: email_domains(a.get("to")),
        ),
        ToolSpec(
            "update_crm_note",
            "update",
            "update the CRM notes",
            lambda a: f"crm:account/{account_slug(a.get('account'))}/notes",
        ),
        ToolSpec("create_task", "create", "create a follow-up task", lambda a: "tasks:cs"),
        ToolSpec(
            "delete_doc",
            "delete",
            "delete an account doc",
            lambda a: f"crm:account/{account_slug(a.get('account'))}/doc",
        ),
    ]
}


@dataclass(frozen=True)
class ToolCallInfo:
    """The facts about one attempted tool call that policy is evaluated against."""

    tool: str
    args: dict[str, Any]
    action_class: ActionClass
    resource: str
    destinations: list[str]
    agent: str = AGENT_ID

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "args": self.args,
            "action_class": self.action_class,
            "resource": self.resource,
            "destinations": self.destinations,
            "agent": self.agent,
        }


def describe(tool: str, args: dict[str, Any], agent: str = AGENT_ID) -> ToolCallInfo:
    spec = TOOLS.get(tool)
    if spec is None:
        # Unknown tools are treated as the most dangerous class so default deny catches them.
        server = THIRD_PARTY_TOOLS.get(tool)
        return ToolCallInfo(tool, args, "delete", f"unknown:{tool}", [server] if server else [], agent)
    return ToolCallInfo(tool, args, spec.action_class, spec.resource(args), spec.destinations(args), agent)


def is_unvetted(info: ToolCallInfo) -> bool:
    return info.resource.startswith("unknown:")


def outside_domains(info: ToolCallInfo) -> list[str]:
    return [d for d in info.destinations if d not in BUSINESS_DOMAINS]


def contact_count(info: ToolCallInfo) -> int:
    if info.tool == "send_email":
        text = str(info.args.get("body", ""))
    else:
        text = " ".join(str(v) for v in info.args.values())
    return len(EMAIL_RE.findall(text))


def is_exfiltration_attempt(info: ToolCallInfo) -> bool:
    if info.tool == "send_email":
        return bool(outside_domains(info))
    return info.tool in THIRD_PARTY_TOOLS and contact_count(info) > 0


def plain_language(info: ToolCallInfo) -> str:
    """One-line description of a tool call for a non-technical audience."""
    a = info.args
    if info.tool == "get_account_doc":
        return f"Read the shared account doc for {a.get('account', '?')}"
    if info.tool == "search_kb":
        return f"Searched the knowledge base for \u201c{a.get('query', '')}\u201d"
    if info.tool == "list_contacts":
        return f"Pulled the contact list for {a.get('account', '?')}"
    if info.tool == "send_email":
        n = contact_count(info)
        payload = f" containing {n} email addresses" if n else ""
        outside = outside_domains(info)
        where = f" an OUTSIDE address ({', '.join(outside)})" if outside else f" {a.get('to', '?')}"
        return f"Tried to send an email{payload} to{where}"
    if info.tool == "update_crm_note":
        return f"Tried to update CRM notes for {a.get('account', '?')}"
    if info.tool == "create_task":
        return f"Tried to create a task: {a.get('title', '')}"
    if info.tool == "delete_doc":
        return f"Tried to DELETE the account doc for {a.get('account', '?')}"
    if info.tool in THIRD_PARTY_TOOLS:
        n = contact_count(info)
        payload = f" {n} customer email addresses" if n else ""
        return f"Tried to send{payload} to a third-party MCP tool ({THIRD_PARTY_TOOLS[info.tool]})"
    return f"Called unknown tool {info.tool}"
