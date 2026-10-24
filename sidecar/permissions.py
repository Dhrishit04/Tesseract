"""What Tesseract may do without asking, and the approvals it is waiting on.

The trust list lives in ~/.tesseract/permissions.json, a file only the sidecar writes, so a
Settings save from the app (which writes the whole settings.json) can never overwrite it.
"""

import asyncio
import json
import logging
import os
import uuid
from dataclasses import dataclass
from typing import Literal, Optional

from settings_store import write_json_atomic
from skills.skill_base import SkillBase

logger = logging.getLogger(__name__)

APPROVAL_TIMEOUT_SECONDS = 600
DETAIL_LIMIT = 4000  # characters kept per string shown on the approval card

Verdict = Literal["allow", "always", "deny", "other"]


@dataclass(frozen=True)
class Decision:
    verdict: Verdict
    via: Literal["button", "reply", "timeout"]
    reply: Optional[str] = None


@dataclass
class ApprovalRequest:
    id: str
    chat_id: Optional[str]
    tool: str
    title: str
    summary: str
    details: dict
    irreversible: bool
    model: str

    def public(self) -> dict:
        """What the approval card shows."""
        return {key: getattr(self, key) for key in ("id", "tool", "title", "summary", "details", "irreversible")}


def _shorten(value):
    if isinstance(value, str):
        return value if len(value) <= DETAIL_LIMIT else value[:DETAIL_LIMIT] + "…"
    if isinstance(value, dict):
        return {key: _shorten(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_shorten(item) for item in value]
    return value


def describe_approval(skill: SkillBase, arguments: dict) -> tuple[str, str, dict]:
    """Title, one-line summary and the (shortened) arguments for an approval card."""
    return skill.approval_title or skill.name, skill.approval_summary(arguments), _shorten(arguments)


# --- Trust list ------------------------------------------------------------

def permissions_path() -> str:
    """Path of the trust file (resolved per call so tests can redirect HOME)."""
    return os.path.join(os.path.expanduser("~"), ".tesseract", "permissions.json")


def load_trusted() -> set[str]:
    """Tools the owner chose "Always allow" for; a missing or unreadable file trusts nothing."""
    try:
        with open(permissions_path(), "r", encoding="utf-8") as trust_file:
            tools = json.load(trust_file).get("always_allow", [])
    except FileNotFoundError:
        return set()
    except (OSError, ValueError, AttributeError):
        logger.exception("Could not read the permissions file; trusting nothing")
        return set()
    return {tool for tool in tools if isinstance(tool, str)} if isinstance(tools, list) else set()


def _save_trusted(tools: set[str]) -> None:
    write_json_atomic(permissions_path(), {"always_allow": sorted(tools)})


def trust(tool: str) -> None:
    _save_trusted(load_trusted() | {tool})


def revoke(tool: str) -> bool:
    tools = load_trusted()
    if tool not in tools:
        return False
    _save_trusted(tools - {tool})
    return True


# --- Pending approvals -----------------------------------------------------

class ApprovalBroker:
    """Approvals waiting for the owner, each answered at most once. Use only on the event loop."""

    def __init__(self):
        self._pending: dict[str, tuple[ApprovalRequest, asyncio.Future]] = {}

    def open(self, *, chat_id: Optional[str], tool: str, title: str, summary: str, details: dict,
             irreversible: bool, model: str) -> ApprovalRequest:
        request = ApprovalRequest(uuid.uuid4().hex, chat_id, tool, title, summary, details, irreversible, model)
        self._pending[request.id] = (request, asyncio.get_running_loop().create_future())
        return request

    async def wait(self, approval_id: str, timeout: Optional[float] = None) -> Decision:
        """The owner's decision; no answer in time counts as deny. The entry is gone afterwards."""
        _, future = self._pending[approval_id]
        try:
            return await asyncio.wait_for(asyncio.shield(future), APPROVAL_TIMEOUT_SECONDS if timeout is None else timeout)
        except asyncio.TimeoutError:
            return Decision("deny", "timeout")
        finally:
            self._pending.pop(approval_id, None)

    def resolve(self, approval_id: str, decision: Decision) -> bool:
        """Answer a pending approval; False when it is unknown, already answered or over."""
        entry = self._pending.get(approval_id)
        if entry is None or entry[1].done():
            return False
        entry[1].set_result(decision)
        return True

    def discard(self, approval_id: str) -> None:
        """Forget an approval whose chat turn ended before anyone waited on it."""
        self._pending.pop(approval_id, None)

    def get(self, approval_id: str) -> Optional[ApprovalRequest]:
        entry = self._pending.get(approval_id)
        return entry[0] if entry else None


_broker: Optional[ApprovalBroker] = None


def get_broker() -> ApprovalBroker:
    global _broker
    if _broker is None:
        _broker = ApprovalBroker()
    return _broker


def reset_broker() -> None:
    global _broker
    _broker = None
