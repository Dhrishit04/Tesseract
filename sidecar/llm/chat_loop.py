"""Tool-calling chat loop shared by the streaming, non-streaming and voice paths."""

import json
import logging
from typing import Any, AsyncGenerator

from llm.openrouter import DEFAULT_MODEL, OpenRouterClient

logger = logging.getLogger(__name__)

MAX_TOOL_TURNS = 10


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


def _parse_arguments(raw: str | None) -> dict:
    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


async def _execute_tool_call(registry, call: dict) -> tuple[dict, dict]:
    """Run one tool call. Returns the tool message for the LLM and a UI event."""
    function = call.get("function", {})
    skill_name = registry.resolve_skill_name(function.get("name", ""))
    result = await registry.execute_skill(skill_name, **_parse_arguments(function.get("arguments")))
    message = {
        "role": "tool",
        "tool_call_id": call.get("id", ""),
        "name": function.get("name", ""),
        "content": json.dumps({"success": result.success, "data": result.data, "error": result.error}, default=str),
    }
    event = {
        "name": skill_name,
        "status": "done" if result.success else "error",
        "error": result.error,
    }
    return message, event


async def stream_chat(
    client: OpenRouterClient,
    registry,
    messages: list[dict],
    model: str = DEFAULT_MODEL,
) -> AsyncGenerator[dict, None]:
    """Yield UI events: {"content"}, {"tool"} and {"error", "message"}."""
    tools = registry.get_tool_definitions()
    conversation = list(messages)

    for _ in range(MAX_TOOL_TURNS):
        pending: list[dict] = []
        async for chunk in client.chat_stream(conversation, model=model, tools=tools):
            if chunk.get("error"):
                yield chunk
                return
            if chunk.get("content"):
                yield {"content": chunk["content"]}
            if chunk.get("tool_calls"):
                _merge_tool_call_deltas(pending, chunk["tool_calls"])

        calls = [call for call in pending if call["function"]["name"]]
        if not calls:
            return

        conversation.append({"role": "assistant", "content": None, "tool_calls": calls})
        for call in calls:
            skill_name = registry.resolve_skill_name(call["function"]["name"])
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
            message, _ = await _execute_tool_call(registry, call)
            conversation.append(message)

    raise ChatLoopError(f"Tool-calling loop exceeded {MAX_TOOL_TURNS} turns")
