"""A stand-in for the OpenRouter API, used by the end-to-end test and for local UI work.

Run: python -m tests.e2e.mock_openrouter --port 8799
Then start the sidecar with OPENROUTER_BASE_URL=http://127.0.0.1:8799/v1.

Behaviour: a prompt mentioning "files" triggers one filesystem.list_directory tool
call; any other prompt is answered with a short markdown reply that quotes it.
"""

import argparse
import json

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

app = FastAPI()

MODELS = [
    {"id": "mock/fast", "name": "Mock Fast"},
    {"id": "anthropic/claude-3.5-sonnet", "name": "Claude 3.5 Sonnet"},
]


def _sse(chunks: list[dict]):
    for chunk in chunks:
        yield f"data: {json.dumps({'choices': [{'delta': chunk}]})}\n\n"
    yield "data: [DONE]\n\n"


def _reply(messages: list[dict]) -> list[dict]:
    last = messages[-1]
    if last["role"] == "tool":
        listing = json.loads(last["content"]).get("data") or {}
        names = [entry.get("name", "?") for entry in listing.get("entries", [])][:8]
        text = "Your working folder contains:\n\n" + "\n".join(f"- `{name}`" for name in names)
        return [{"content": part + " "} for part in text.split(" ")]

    prompt = str(last.get("content", ""))
    if "files" in prompt.lower():
        return [{"tool_calls": [{
            "index": 0, "id": "call_1", "type": "function",
            "function": {"name": "filesystem__list_directory", "arguments": json.dumps({"path": "."})},
        }]}]
    text = f"**Mock reply.** You asked: {prompt}"
    return [{"content": part + " "} for part in text.split(" ")]


@app.get("/v1/models")
def models():
    return {"data": MODELS}


@app.post("/v1/chat/completions")
async def completions(request: Request):
    body = await request.json()
    chunks = _reply(body["messages"])
    if body.get("stream"):
        return StreamingResponse(_sse(chunks), media_type="text/event-stream")
    content = "".join(chunk.get("content", "") for chunk in chunks).strip()
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8799)
    uvicorn.run(app, host="127.0.0.1", port=parser.parse_args().port, log_level="warning")
