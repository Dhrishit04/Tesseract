"""OpenRouter LLM client with streaming support."""

import json
from typing import AsyncGenerator, Optional

import httpx

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "anthropic/claude-3.5-sonnet"


def _error(message: str) -> dict:
    return {"error": True, "message": message}


class OpenRouterClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = OPENROUTER_BASE,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.transport = transport
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://tesseract.ai",
            "X-Title": "Tesseract AI Agent",
        }

    def _payload(self, messages: list[dict], model: str, tools: Optional[list[dict]], stream: bool) -> dict:
        payload = {"model": model, "messages": messages, "stream": stream}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        return payload

    async def chat_stream(
        self,
        messages: list[dict],
        model: str = DEFAULT_MODEL,
        tools: Optional[list[dict]] = None,
    ) -> AsyncGenerator[dict, None]:
        """Stream a chat completion.

        Yields dicts: {"content": str}, {"tool_calls": [...]} or {"error": True, "message": str}.
        """
        payload = self._payload(messages, model, tools, stream=True)
        try:
            async with httpx.AsyncClient(timeout=60.0, transport=self.transport) as client:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/chat/completions",
                    headers=self.headers,
                    json=payload,
                ) as response:
                    if response.status_code != 200:
                        body = (await response.aread()).decode(errors="replace")
                        yield _error(f"OpenRouter error {response.status_code}: {body}")
                        return

                    async for line in response.aiter_lines():
                        chunk = self._parse_stream_line(line)
                        if chunk is None:
                            continue
                        if chunk == "done":
                            break
                        yield chunk
        except httpx.HTTPError as exc:
            yield _error(f"Could not reach OpenRouter: {exc}")

    @staticmethod
    def _parse_stream_line(line: str) -> dict | str | None:
        """Turn one SSE line into a chunk dict, "done", or None to skip."""
        if not line.startswith("data: "):
            return None
        data = line[6:].strip()
        if data == "[DONE]":
            return "done"
        try:
            parsed = json.loads(data)
        except json.JSONDecodeError:
            return None
        if parsed.get("error"):
            message = parsed["error"].get("message") if isinstance(parsed["error"], dict) else str(parsed["error"])
            return _error(f"OpenRouter error: {message}")
        choices = parsed.get("choices") or [{}]
        delta = choices[0].get("delta", {})
        chunk = {}
        if delta.get("content"):
            chunk["content"] = delta["content"]
        if delta.get("tool_calls"):
            chunk["tool_calls"] = delta["tool_calls"]
        return chunk or None

    async def list_models(self) -> list[dict]:
        """Fetch available models from OpenRouter."""
        try:
            async with httpx.AsyncClient(timeout=10.0, transport=self.transport) as client:
                response = await client.get(f"{self.base_url}/models", headers=self.headers)
        except httpx.HTTPError:
            return []
        if response.status_code == 200:
            return response.json().get("data", [])
        return []

    async def chat(
        self,
        messages: list[dict],
        model: str = DEFAULT_MODEL,
        tools: Optional[list[dict]] = None,
    ) -> dict:
        """Non-streaming chat completion."""
        payload = self._payload(messages, model, tools, stream=False)
        try:
            async with httpx.AsyncClient(timeout=60.0, transport=self.transport) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=self.headers,
                    json=payload,
                )
        except httpx.HTTPError as exc:
            return _error(f"Could not reach OpenRouter: {exc}")
        if response.status_code != 200:
            return _error(f"OpenRouter error {response.status_code}: {response.text}")
        return response.json()
