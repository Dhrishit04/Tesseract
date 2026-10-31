"""ChatGPT models through the Responses API with a Sign in with ChatGPT token, speaking the chat loop's contract."""

import json
import logging
from typing import AsyncGenerator, Optional

import httpx

from auth.chatgpt import ChatGPTAuthError
from llm.chat_loop import collect_stream
from llm.images import SCREENSHOT_NOTE, after_tool_results

logger = logging.getLogger(__name__)

TOOL_NAMESPACE = "tesseract"
USAGE_URL = "https://chatgpt.com/settings/usage"


def _error(message: str) -> dict:
    return {"error": True, "message": message}


def _image_parts(images: list[dict]) -> list[dict]:
    urls = [f"data:{image['media_type']};base64,{image['data']}" for image in images]
    return [{"type": "input_text", "text": SCREENSHOT_NOTE}, *({"type": "input_image", "image_url": url} for url in urls)]


def to_responses(messages: list[dict]) -> tuple[str, list[dict]]:
    """Split the system prompt off as instructions and rewrite the rest as Responses input items."""
    system: list[str] = []
    items: list[dict] = []
    for message in after_tool_results(messages, _image_parts):
        role = message["role"]
        if role == "system":
            system.append(message["content"])
        elif role == "tool":
            items.append({"type": "function_call_output", "call_id": message["tool_call_id"], "output": message["content"]})
        elif role == "assistant" and message.get("replay"):
            items.extend(message["replay"])
        elif role == "assistant" and message.get("tool_calls"):
            if message.get("content"):
                items.append({"role": "assistant", "content": message["content"]})
            items += [
                {"type": "function_call", "call_id": c["id"], "name": c["function"]["name"], "arguments": c["function"]["arguments"] or "{}"}
                for c in message["tool_calls"]
            ]
        else:
            items.append({"role": role, "content": message["content"]})
    return "\n\n".join(system), items


def _replay(calls: list[dict]) -> list[dict]:
    # shortcut: reasoning items aren't replayed because store:false leaves their ids unreferenceable; upgrade if tool rounds lose context.
    return [{k: v for k, v in call.items() if k not in ("id", "status")} for call in calls]


def _describe(status: Optional[int], error: dict) -> str:
    """Turn an HTTP status plus an error object into a message for the owner."""
    code = error.get("code") or ""
    message = error.get("message")
    if code == "subscription_sharing_usage_limit_exceeded":
        return f"You've reached your ChatGPT plan's usage limit for now. Manage usage at {USAGE_URL}."
    if code == "subscription_sharing_unsupported_capability":
        return f"ChatGPT doesn't support part of this request ({error.get('param')})."
    if code == "subscription_sharing_user_not_eligible":
        return "Your ChatGPT plan can't be used in Tesseract."
    if status == 401:
        return "ChatGPT didn't accept the sign-in. Continue with ChatGPT in Settings to sign in again."
    if status == 503 or code in ("subscription_sharing_usage_unavailable", "subscription_sharing_user_unavailable"):
        return "ChatGPT is temporarily unavailable. Try again in a moment."
    return f"ChatGPT error {status or code}: {message}"


def _body_error(text: str) -> dict:
    try:
        body = json.loads(text)
    except ValueError:
        return {"message": text}
    if isinstance(body.get("error"), dict):
        return body["error"]
    return {"message": body.get("detail") or text}


class ChatGPTClient:
    def __init__(self, account, base_url: str = "https://api.openai.com/v1", transport: Optional[httpx.AsyncBaseTransport] = None):
        self.account = account
        self.base_url = base_url
        self.transport = transport

    async def _headers(self) -> dict:
        return {"Authorization": f"Bearer {await self.account.access_token()}", "Content-Type": "application/json"}

    async def chat_stream(
        self,
        messages: list[dict],
        model: str,
        tools: Optional[list[dict]] = None,
    ) -> AsyncGenerator[dict, None]:
        """Stream a reply as {"content"}, {"tool_calls"}, {"replay"} or {"error"} chunks; never raises."""
        calls: list[dict] = []
        try:
            headers = await self._headers()
            instructions, items = to_responses(messages)
            payload: dict = {"model": model, "input": items, "store": False, "stream": True}
            if instructions:
                payload["instructions"] = instructions
            if tools:
                # Preview limitations: tools must be grouped in a namespace, and strict mode isn't available.
                payload["tools"] = [{
                    "type": "namespace",
                    "name": TOOL_NAMESPACE,
                    "description": "Tools that act on the owner's computer, files and accounts.",
                    "tools": [{"type": "function", **t["function"], "strict": False} for t in tools],
                }]
            async with httpx.AsyncClient(timeout=60.0, transport=self.transport) as client:
                async with client.stream("POST", f"{self.base_url}/responses", headers=headers, json=payload) as response:
                    if response.status_code != 200:
                        body = (await response.aread()).decode(errors="replace")
                        yield _error(_describe(response.status_code, _body_error(body)))
                        return
                    async for line in response.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        try:
                            event = json.loads(line[6:])
                        except ValueError:
                            continue
                        kind = event.get("type")
                        if kind == "response.output_text.delta":
                            yield {"content": event["delta"]}
                        elif kind == "response.output_item.done" and event["item"].get("type") == "function_call":
                            calls.append(event["item"])
                        elif kind == "response.completed":
                            if calls:
                                yield {"tool_calls": [
                                    {"index": i, "id": c["call_id"], "type": "function", "function": {"name": c["name"], "arguments": c["arguments"]}}
                                    for i, c in enumerate(calls)
                                ]}
                                yield {"replay": _replay(calls)}
                            return
                        elif kind == "response.incomplete":
                            reason = (event.get("response", {}).get("incomplete_details") or {}).get("reason", "unknown")
                            yield _error(f"ChatGPT stopped before finishing ({reason}).")
                            return
                        elif kind == "response.failed":
                            yield _error(_describe(None, event.get("response", {}).get("error") or {}))
                            return
                        elif kind == "error":
                            yield _error(_describe(None, event.get("error") if isinstance(event.get("error"), dict) else event))
                            return
            yield _error("The ChatGPT reply was interrupted before it finished.")
        except ChatGPTAuthError as exc:
            yield _error(str(exc))
        except httpx.HTTPError as exc:
            yield _error(f"Could not reach ChatGPT: {exc}")
        except Exception as exc:  # bad stored history or an odd event must not escape the generator
            logger.exception("ChatGPT request failed")
            yield _error(f"Could not reach ChatGPT: {exc}")

    async def chat(self, messages: list[dict], model: str, tools: Optional[list[dict]] = None) -> dict:
        return await collect_stream(self.chat_stream(messages, model, tools))

    async def list_models(self) -> list[dict]:
        """Raises on a dead session or a failed request, so /models can say what went wrong."""
        async with httpx.AsyncClient(timeout=60.0, transport=self.transport) as client:
            response = await client.get(f"{self.base_url}/models", headers=await self._headers())
        response.raise_for_status()
        return [{"id": m["slug"], "name": m["display_name"]} for m in response.json()["models"] if m.get("visibility") == "list"]
