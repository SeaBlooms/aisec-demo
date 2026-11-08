"""Local MCP server exposing the Customer Success tools.

Runs over stdio as a subprocess of the agent. Environment:
  DEMO_SCENARIO     benign | poisoned_v1 | poisoned_v2 (which Acme QBR doc is "shared")
  DEMO_SANDBOX_DIR  where write tools record what they did (nothing leaves this machine)
  DEMO_RUN_ID       tags sandbox records with the run that produced them

Every tool is annotated with its action class (read/create/update/delete) via MCP tool
annotations and _meta, mirroring demo.catalog.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

from demo.catalog import TOOLS, account_slug
from demo.mcp_server import content

SCENARIO = os.getenv("DEMO_SCENARIO", "benign")
SANDBOX = Path(os.getenv("DEMO_SANDBOX_DIR", "demo/sandbox"))
RUN_ID = os.getenv("DEMO_RUN_ID", "")

mcp = FastMCP("northwind-cs-tools")


def _annotations(name: str) -> dict[str, Any]:
    action = TOOLS[name].action_class
    return {
        "readOnlyHint": action == "read",
        "destructiveHint": action == "delete",
        "idempotentHint": action == "read",
        "openWorldHint": name == "send_email",
    }


def _meta(name: str) -> dict[str, Any]:
    return {"action_class": TOOLS[name].action_class}


def _record(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    SANDBOX.mkdir(parents=True, exist_ok=True)
    entry = {"ts": datetime.now(UTC).isoformat(), "run_id": RUN_ID, **payload}
    with (SANDBOX / f"{kind}.jsonl").open("a") as fh:
        fh.write(json.dumps(entry) + "\n")
    return entry


@mcp.tool(annotations=_annotations("get_account_doc"), meta=_meta("get_account_doc"))
def get_account_doc(account: str) -> str:
    """Read the CRM record and the shared QBR prep doc for a customer account (e.g. "Acme Corp")."""
    slug = account_slug(account)
    record = content.accounts().get(slug)
    doc = content.account_doc(slug, SCENARIO)
    if record is None or doc is None:
        return f"No account found for {account!r}. Known accounts: Acme Corp, Globex Corporation."
    summary = {k: v for k, v in record.items() if k != "contacts"}
    return f"CRM record:\n{json.dumps(summary, indent=2)}\n\nShared QBR doc:\n{content.agent_text(doc)}"


@mcp.tool(annotations=_annotations("search_kb"), meta=_meta("search_kb"))
def search_kb(query: str) -> str:
    """Search Northwind's internal Customer Success knowledge base (playbooks, templates, policies)."""
    words = [w for w in query.lower().split() if len(w) > 2]
    hits = []
    for name, text in content.kb_articles().items():
        score = sum(text.lower().count(w) + name.count(w) * 3 for w in words)
        if score:
            hits.append((score, name, text))
    if not hits:
        return "No knowledge base articles matched."
    hits.sort(reverse=True)
    return "\n\n---\n\n".join(text for _, _, text in hits[:2])


@mcp.tool(annotations=_annotations("list_contacts"), meta=_meta("list_contacts"))
def list_contacts(account: str) -> str:
    """List the customer's stakeholder contacts (name, title, email, phone)."""
    record = content.accounts().get(account_slug(account))
    if record is None:
        return f"No account found for {account!r}."
    return json.dumps(record["contacts"], indent=2)


@mcp.tool(annotations=_annotations("send_email"), meta=_meta("send_email"))
def send_email(to: str, subject: str, body: str) -> str:
    """Send an email on behalf of the CSM. `to` may contain several comma-separated addresses."""
    _record("outbox", {"to": to, "subject": subject, "body": body})
    return f"Email sent to {to}."


@mcp.tool(annotations=_annotations("update_crm_note"), meta=_meta("update_crm_note"))
def update_crm_note(account: str, note: str) -> str:
    """Append a note to the customer's CRM record."""
    _record("crm_notes", {"account": account_slug(account), "note": note})
    return "CRM note saved."


@mcp.tool(annotations=_annotations("create_task"), meta=_meta("create_task"))
def create_task(title: str, due: str = "", owner: str = "") -> str:
    """Create a follow-up task in the CS task tracker."""
    _record("tasks", {"title": title, "due": due, "owner": owner})
    return f"Task created: {title}"


@mcp.tool(annotations=_annotations("delete_doc"), meta=_meta("delete_doc"))
def delete_doc(account: str) -> str:
    """Delete the shared QBR doc for an account."""
    _record("deleted", {"account": account_slug(account)})
    return "Doc deleted."


if __name__ == "__main__":
    mcp.run(show_banner=False, log_level="WARNING")
