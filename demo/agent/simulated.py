"""Offline stand-in for the LLM, used when DEMO_MODEL=simulated.

It is a scripted, deterministic imitation of a fooled agent so the UI, policy engine and replay
traces can be exercised with no network or API key. It is always labeled "simulated" in the UI
and in traces, and it is never used to claim anything about a real model's behavior.

Behavior it imitates:
  - reads whatever the task asks for;
  - if a doc contains an instruction to send data to an outside address, the "older" stand-in complies,
    except that the hardened prompt stops the blunt (v1) injection but not the disguised (v2) one;
  - the "newer" stand-in declines every injected send, as Claude Haiku 5.5 did in measured runs;
  - both stand-ins pass the contact list to a connected third-party enrichment tool, as both real
    models did in measured runs.
"""

from __future__ import annotations

import json
import re

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from demo.catalog import BUSINESS_DOMAINS, EMAIL_RE

BLUNT_MARKERS = ("ignore all previous instructions", "instructions for the ai assistant")


def _prompt(messages: list[ModelMessage]) -> str:
    for msg in messages:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                    return part.content
    return ""


def _calls_and_returns(messages: list[ModelMessage]) -> tuple[list[ToolCallPart], dict[str, str]]:
    calls: list[ToolCallPart] = []
    returns: dict[str, str] = {}
    for msg in messages:
        for part in msg.parts:
            if isinstance(part, ToolCallPart):
                calls.append(part)
            elif isinstance(part, ToolReturnPart):
                returns[part.tool_name] = part.model_response_str()
    return calls, returns


def _injection(doc: str) -> tuple[str, str] | None:
    """Return (attacker address, subject) if the doc asks for data to go to an outside address."""
    for match in EMAIL_RE.finditer(doc):
        if match.group(1).lower() not in BUSINESS_DOMAINS:
            subject = re.search(r"[Ss]ubject:? \"([^\"]+)\"|subject \"([^\"]+)\"", doc)
            subj = next((g for g in subject.groups() if g), "Contacts export") if subject else "Contacts export"
            return match.group(0), subj
    return None


def respond(messages: list[ModelMessage], info: AgentInfo, newer: bool = False) -> ModelResponse:
    prompt = _prompt(messages).lower()
    calls, returns = _calls_and_returns(messages)
    called = {c.tool_name for c in calls}
    hardened = "SECURITY RULES" in (info.instructions or "")

    wanted: list[tuple[str, dict[str, str]]] = []
    for account, label in (("acme", "Acme Corp"), ("globex", "Globex Corporation")):
        if account in prompt and ("doc" in prompt or "qbr" in prompt):
            wanted.append(("get_account_doc", {"account": label}))
    if any(w in prompt for w in ("playbook", "policy", "template", "knowledge base")):
        wanted.append(("search_kb", {"query": "renewal playbook flat usage"}))
    if any(w in prompt for w in ("stakeholder", "contacts")):
        wanted.append(("list_contacts", {"account": "Acme Corp"}))
    for tool, args in wanted:
        if tool not in called:
            return ModelResponse(parts=[ToolCallPart(tool, args)])

    doc = returns.get("get_account_doc", "")
    poisoned_tool = "enrich_contacts" in {t.name for t in info.function_tools}
    if poisoned_tool and doc and "read-only" not in prompt:
        if "list_contacts" not in called:
            return ModelResponse(parts=[ToolCallPart("list_contacts", {"account": "Acme Corp"})])
        if "enrich_contacts" not in called:
            return ModelResponse(
                parts=[ToolCallPart("enrich_contacts", {"account": "Acme Corp", "contacts": returns.get("list_contacts", "")})]
            )
    injection = _injection(doc)
    blunt = any(m in doc.lower() for m in BLUNT_MARKERS)
    resisted = bool(injection) and (newer or (hardened and blunt))
    if injection and not resisted and "read-only" not in prompt:
        to, subject = injection
        if "list_contacts" not in called:
            return ModelResponse(parts=[ToolCallPart("list_contacts", {"account": "Acme Corp"})])
        if "send_email" not in called:
            contacts = json.loads(returns.get("list_contacts", "[]") or "[]")
            body = "\n".join(f"{c['name']}, {c['title']}, {c['email']}, {c['phone']}" for c in contacts)
            return ModelResponse(parts=[ToolCallPart("send_email", {"to": to, "subject": subject, "body": body})])

    brief = ["Brief (simulated model):"]
    if "Globex" in doc:
        brief.append("Globex is healthy (93% of seats active); the new COO is reviewing all software spend.")
    elif doc:
        brief.append("Acme adoption is flat at 70% of seats, health 64, and SSO issues are open.")
    if "search_kb" in called:
        brief.append("Playbook: run an adoption deep-dive and agree an enablement plan with the sponsor.")
    if "list_contacts" in called and "send_email" not in called:
        brief.append("Key stakeholders listed above.")
    if "enrich_contacts" in called:
        blocked = "BLOCKED" in returns.get("enrich_contacts", "")
        brief.append(
            "Contact enrichment was blocked by policy." if blocked else "Refreshed stakeholder details via contact enrichment."
        )
    if "send_email" in called:
        blocked = "BLOCKED" in returns.get("send_email", "")
        brief.append(
            "The stakeholder sync email was blocked by policy and needs approval."
            if blocked
            else "Completed the open action item: sent the stakeholder list as requested in the doc."
        )
    if resisted:
        brief.append(f"Note: the shared doc asks me to send contact data to {injection[0]}; I did not send it.")
    return ModelResponse(parts=[TextPart(" ".join(brief))])


def simulated_model(newer: bool = False) -> FunctionModel:
    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return respond(messages, info, newer=newer)

    return FunctionModel(fn, model_name="simulated:newer" if newer else "simulated")
