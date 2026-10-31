"""Claude through the Anthropic Messages API, speaking the chat loop's Chat Completions shaped contract."""

import json
import logging
from typing import AsyncGenerator, Optional

import anthropic

from llm.chat_loop import collect_stream

logger = logging.getLogger(__name__)

MAX_TOKENS = 64000
FALLBACK_BETA = "server-side-fallback-2026-07-01"
FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}


def _error(message: str) -> dict:
    return {"error": True, "message": message}


def to_anthropic(messages: list[dict]) -> tuple[str, list[dict]]:
    """Split the system prompt off and rewrite the rest as Messages API turns."""
    system: list[str] = []
    turns: list[dict] = []
    for message in messages:
        role = message["role"]
        if role == "system":
            system.append(message["content"])
        elif role == "tool":
            result = {"type": "tool_result", "tool_use_id": message["tool_call_id"], "content": message["content"]}
            if message.get("images"):
                images = [{"type": "image", "source": {"type": "base64", **image}} for image in message["images"]]
                result["content"] = [{"type": "text", "text": message["content"]}, *images]
            try:
                if json.loads(message["content"]).get("success") is False:
                    result["is_error"] = True
            except (ValueError, AttributeError):
                pass
            # Parallel tool results must arrive together in one user turn.
            if turns and turns[-1]["role"] == "user" and turns[-1].get("_results"):
                turns[-1]["content"].append(result)
            else:
                turns.append({"role": "user", "content": [result], "_results": True})
        elif role == "assistant" and message.get("replay"):
            turns.append({"role": "assistant", "content": message["replay"]})
        elif role == "assistant" and message.get("tool_calls"):
            blocks = [{"type": "text", "text": message["content"]}] if message.get("content") else []
            blocks += [
                {"type": "tool_use", "id": call["id"], "name": call["function"]["name"], "input": json.loads(call["function"]["arguments"] or "{}")}
                for call in message["tool_calls"]
            ]
            turns.append({"role": "assistant", "content": blocks})
        else:
            turns.append({"role": role, "content": message["content"]})
    # A history window can open mid-exchange, and the Messages API rejects a first turn that isn't the owner's.
    while turns and (turns[0]["role"] != "user" or turns[0].get("_results")):
        turns.pop(0)
    return "\n\n".join(system), [{k: v for k, v in turn.items() if k != "_results"} for turn in turns]


class AnthropicClient:
    def __init__(self, api_key: str, base_url: Optional[str] = None, http_client=None):
        self.client = anthropic.AsyncAnthropic(api_key=api_key, base_url=base_url, http_client=http_client, max_retries=2)

    async def chat_stream(
        self,
        messages: list[dict],
        model: str,
        tools: Optional[list[dict]] = None,
    ) -> AsyncGenerator[dict, None]:
        """Stream a reply as {"content"}, {"tool_calls"}, {"replay"} or {"error"} chunks; never raises."""
        try:
            system, turns = to_anthropic(messages)
            params: dict = {"model": model, "max_tokens": MAX_TOKENS, "messages": turns}
            if system:
                params["system"] = system
            if tools:
                params["tools"] = [
                    {"name": t["function"]["name"], "description": t["function"].get("description", ""), "input_schema": t["function"]["parameters"]}
                    for t in tools
                ]
            if model in FALLBACK_MODELS:
                params.update(betas=[FALLBACK_BETA], extra_body={"fallbacks": "default"})
            async with self.client.beta.messages.stream(**params) as stream:
                async for event in stream:
                    if event.type == "text":
                        yield {"content": event.text}
                final = await stream.get_final_message()
        except anthropic.AuthenticationError:
            yield _error("Anthropic rejected the API key. Check it in Settings → Models & accounts.")
            return
        except anthropic.RateLimitError:
            yield _error("Anthropic is rate limiting requests. Try again in a minute.")
            return
        except anthropic.APIStatusError as exc:
            yield _error(f"Anthropic error {exc.status_code}: {exc.message}")
            return
        except anthropic.APIConnectionError as exc:
            yield _error(f"Could not reach Anthropic: {exc}")
            return
        except anthropic.APIError as exc:
            yield _error(f"Anthropic error: {exc}")
            return
        except Exception as exc:  # a dropped stream or bad stored history must not escape the generator
            logger.exception("Anthropic request failed")
            yield _error(f"Could not reach Anthropic: {exc}")
            return

        if final.stop_reason == "refusal":
            yield _error("Claude declined this request.")
        elif final.stop_reason == "max_tokens":
            yield _error("Claude's reply hit the length limit before finishing.")
        else:
            calls = [b for b in final.content if b.type == "tool_use"]
            if calls:
                yield {"tool_calls": [
                    {"index": i, "id": b.id, "type": "function", "function": {"name": b.name, "arguments": json.dumps(b.input)}}
                    for i, b in enumerate(calls)
                ]}
                yield {"replay": [b.model_dump(exclude_none=True) for b in final.content]}

    async def chat(self, messages: list[dict], model: str, tools: Optional[list[dict]] = None) -> dict:
        return await collect_stream(self.chat_stream(messages, model, tools))

    async def list_models(self) -> list[dict]:
        """Raises on a rejected key or a failed request, so /models can say what went wrong."""
        return [{"id": m.id, "name": m.display_name} async for m in self.client.models.list()]
