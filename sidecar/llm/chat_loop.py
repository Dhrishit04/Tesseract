"""Tool-calling chat loop shared by the streaming, non-streaming and voice paths."""

import json
import logging
from contextlib import aclosing
from typing import Any, AsyncGenerator

from llm.openrouter import DEFAULT_MODEL, OpenRouterClient
from permissions import ApprovalBroker, describe_approval, load_trusted, trust
from skills.skill_base import SkillResult

logger = logging.getLogger(__name__)

MAX_TOOL_TURNS = 10

# What the model is told when a change tool did not run.
DECLINED = "The owner did not allow this action, so nothing was done. Do not try it again unless they ask."
DECLINED_WITH_REPLY = "The owner did not allow this action and said: \"{reply}\". Nothing was done; follow what they said."
TIMED_OUT = "The owner did not answer within 10 minutes, so nothing was done."
NEEDS_CHAT = ("This action needs the owner's approval, which can only be given in the Tesseract chat window. "
              "Nothing was done; ask them to request it there.")


def _merge_tool_call_deltas(pending: list[dict], deltas: list[dict]) -> None:
    """Fold streamed tool-call fragments into complete calls, keyed by index."""
    for fragment in deltas:
        index = fragment.get("index", 0)
        while len(pending) <= index:
            pending.append({"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
        call = pending[index]
        if fragment.get("id"):
            call["id"] = fragment["id"]
        if fragment.get("type"):
            call["type"] = fragment["type"]
        function = fragment.get("function") or {}
        if function.get("name"):
            call["function"]["name"] += function["name"]
        if function.get("arguments"):
            call["function"]["arguments"] += function["arguments"]


def _parse_arguments(raw: str | None) -> dict | None:
    """Tool arguments as a dict, or None when the model sent something that is not a JSON object."""
    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _for_model(data: Any) -> Any:
    """Drop raw screenshot bytes: megabytes of base64 would overflow the model's context."""
    if isinstance(data, dict) and "image_base64" in data:
        return {**data, "image_base64": "[screenshot shown to the user in the activity log]"}
    return data


def _tool_message(call: dict, result: SkillResult) -> dict:
    return {
        "role": "tool",
        "tool_call_id": call.get("id", ""),
        "name": call.get("function", {}).get("name", ""),
        "content": json.dumps({"success": result.success, "data": _for_model(result.data), "error": result.error}, default=str),
    }


def _gated_skill(registry, call: dict):
    """The skill and arguments of a call that must be approved first, or None when it may just run."""
    skill = registry.get_skill(registry.resolve_skill_name(call.get("function", {}).get("name", "")))
    arguments = _parse_arguments(call.get("function", {}).get("arguments"))
    # Unknown tools and bad arguments fail in _execute_tool_call without running anything.
    if skill is None or arguments is None or skill.kind == "read" or skill.name in load_trusted():
        return None
    return skill, arguments


def _refusal(resolved: dict) -> str | None:
    """Tool error for the model when an approval_resolved event means "don't run"; None to run."""
    if resolved["decision"] in ("allow", "always"):
        return None
    if resolved["via"] == "timeout":
        return TIMED_OUT
    if resolved["decision"] == "other" and resolved.get("reply"):
        return DECLINED_WITH_REPLY.format(reply=resolved["reply"])
    return DECLINED


async def _ask(approvals: ApprovalBroker, chat_id, model: str, skill, arguments: dict) -> AsyncGenerator[dict, None]:
    """Show an approval card and wait for the owner; the last event is approval_resolved."""
    title, summary, details = describe_approval(skill, arguments)
    request = approvals.open(chat_id=chat_id, tool=skill.name, title=title, summary=summary,
                             details=details, irreversible=skill.irreversible, model=model)
    try:
        yield {"approval": request.public()}
        decision = await approvals.wait(request.id)
    finally:
        approvals.discard(request.id)  # the stream may close before wait() runs
    if decision.verdict == "always":
        trust(skill.name)
    resolved = {"id": request.id, "decision": decision.verdict, "via": decision.via}
    if decision.via == "reply":
        resolved["reply"] = decision.reply
    yield {"approval_resolved": resolved}


async def _execute_tool_call(registry, call: dict) -> tuple[dict, dict]:
    """Run one tool call. Returns the tool message for the LLM and a UI event."""
    function = call.get("function", {})
    skill_name = registry.resolve_skill_name(function.get("name", ""))
    arguments = _parse_arguments(function.get("arguments"))
    if arguments is None:
        # Never run a tool on guessed arguments; let the model correct itself.
        result = SkillResult.failure("Tool arguments were not valid JSON; nothing was run. Send the call again with a JSON object.")
    else:
        result = await registry.execute_skill(skill_name, **arguments)
    message = _tool_message(call, result)
    event = {
        "name": skill_name,
        "status": "done" if result.success else "error",
        "error": result.error,
    }
    image = result.data.get("image_base64") if isinstance(result.data, dict) else None
    if image:
        event["preview"] = f"data:image/png;base64,{image}"
    return message, event


async def _stream_turn(client, conversation, model, tools, pending: list[dict]) -> AsyncGenerator[dict, None]:
    """Stream one model turn, relaying text and collecting tool-call fragments into `pending`."""
    async for chunk in client.chat_stream(conversation, model=model, tools=tools):
        if chunk.get("error"):
            yield chunk
            return
        if chunk.get("content"):
            yield {"content": chunk["content"]}
        if chunk.get("tool_calls"):
            _merge_tool_call_deltas(pending, chunk["tool_calls"])


async def stream_chat(
    client: OpenRouterClient,
    registry,
    messages: list[dict],
    model: str = DEFAULT_MODEL,
    *,
    approvals: ApprovalBroker | None = None,
    chat_id: str | None = None,
) -> AsyncGenerator[dict, None]:
    """Yield UI events: {"content"}, {"tool"}, {"approval"}, {"approval_resolved"} and {"error", "message"}.

    Change tools the owner has not trusted wait for an answer through `approvals`; without a
    broker they are refused.
    """
    tools = registry.get_tool_definitions()
    conversation = list(messages)

    for _ in range(MAX_TOOL_TURNS):
        pending: list[dict] = []
        failed = False
        async for event in _stream_turn(client, conversation, model, tools, pending):
            failed = failed or bool(event.get("error"))
            yield event
        if failed:
            return

        calls = [call for call in pending if call["function"]["name"]]
        if not calls:
            return

        conversation.append({"role": "assistant", "content": None, "tool_calls": calls})
        for call in calls:
            skill_name = registry.resolve_skill_name(call["function"]["name"])
            gated = _gated_skill(registry, call)
            if gated:
                refusal = NEEDS_CHAT
                if approvals is not None:
                    async with aclosing(_ask(approvals, chat_id, model, *gated)) as asking:
                        async for event in asking:
                            yield event
                    refusal = _refusal(event["approval_resolved"])
                if refusal:
                    conversation.append(_tool_message(call, SkillResult.failure(refusal)))
                    yield {"tool": {"name": skill_name, "status": "declined"}}
                    continue
            yield {"tool": {"name": skill_name, "status": "running"}}
            message, event = await _execute_tool_call(registry, call)
            conversation.append(message)
            yield {"tool": event}

    yield {"error": True, "message": f"Stopped after {MAX_TOOL_TURNS} tool-calling turns."}


class ChatLoopError(RuntimeError):
    """Raised when a non-streaming chat cannot produce an answer."""


async def complete_chat(
    client: OpenRouterClient,
    registry,
    messages: list[dict],
    model: str = DEFAULT_MODEL,
) -> str:
    """Run the tool-calling loop without streaming and return the final text."""
    tools = registry.get_tool_definitions()
    conversation = list(messages)

    for _ in range(MAX_TOOL_TURNS):
        result = await client.chat(conversation, model=model, tools=tools)
        if result.get("error"):
            raise ChatLoopError(result.get("message", "Unknown OpenRouter error"))

        assistant_message: dict[str, Any] = result["choices"][0]["message"]
        calls = assistant_message.get("tool_calls") or []
        if not calls:
            return assistant_message.get("content") or ""

        conversation.append(assistant_message)
        for call in calls:
            if _gated_skill(registry, call):
                conversation.append(_tool_message(call, SkillResult.failure(NEEDS_CHAT)))
                continue
            message, _ = await _execute_tool_call(registry, call)
            conversation.append(message)

    raise ChatLoopError(f"Tool-calling loop exceeded {MAX_TOOL_TURNS} turns")
