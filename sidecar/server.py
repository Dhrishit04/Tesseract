"""FastAPI sidecar: chat, skills, voice and Google account endpoints."""

import asyncio
import html
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import AsyncGenerator, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel
from starlette.middleware.trustedhost import TrustedHostMiddleware

from chat_store import derive_title, get_chat_store
from automation.orchestrator import get_automation_orchestrator
from llm.chat_loop import ChatLoopError, complete_chat, stream_chat
from llm.openrouter import DEFAULT_MODEL, OPENROUTER_BASE, OpenRouterClient
from settings_store import load_settings, save_settings, update_section
from skills.registry import get_registry
from voice import get_pipeline, get_stt_skill, get_tts_skill, get_wake_word_skill
from voice.wake_word import VOICE_DEPS_AVAILABLE

logger = logging.getLogger(__name__)

VERSION = "0.1.0"
NO_API_KEY_MESSAGE = "Add your OpenRouter API key in Settings to start chatting."
SIGN_IN_FAILED = "Google sign-in failed"
GOOGLE_NOT_INSTALLED = 'Google support is not installed. Run: pip install -e ".[google]"'
# shortcut: only the latest messages of a saved chat go to the model, so long chats stay under its
# context limit; upgrade to summarising older turns if answers start missing earlier context.
HISTORY_LIMIT = 40

# Vite dev server plus the origins Tauri v2 serves the packaged app from.
ALLOWED_ORIGINS = [
    "http://localhost:1420",
    "http://127.0.0.1:1420",
    "tauri://localhost",
    "http://tauri.localhost",
    "https://tauri.localhost",
]
ALLOWED_HOSTS = ["127.0.0.1", "localhost"]

_client: Optional[OpenRouterClient] = None
_registry = None


def _new_client(api_key: str) -> OpenRouterClient:
    # OPENROUTER_BASE_URL points the sidecar at a proxy or a local mock (used by the E2E test).
    return OpenRouterClient(api_key=api_key, base_url=os.environ.get("OPENROUTER_BASE_URL", OPENROUTER_BASE))


def get_client() -> OpenRouterClient:
    global _client
    if _client is None:
        _client = _new_client(load_settings().get("openrouter_api_key", ""))
    return _client


def get_skill_registry():
    """Get or initialize the skill registry."""
    global _registry
    if _registry is None:
        _registry = get_registry()
        _registry.load_skills()
    return _registry


def get_oauth_manager():
    """Return the Google OAuth manager, or None when the [google] extra is not installed."""
    try:
        from auth.google_oauth import get_google_oauth_manager
    except ImportError:
        return None
    return get_google_oauth_manager()


def _result(result) -> dict:
    """Serialize a SkillResult for an HTTP response."""
    return {"success": result.success, "data": result.data, "error": result.error}


# --- Startup ---------------------------------------------------------------

def _voice_skills(settings: Optional[dict] = None):
    """Return the wake word, STT and TTS skills configured from current settings."""
    settings = settings if settings is not None else load_settings()
    wake_word_skill = get_wake_word_skill()
    wake_word_skill.load_settings(settings)
    stt_skill = get_stt_skill()
    stt_skill.load_settings(settings)
    tts_skill = get_tts_skill()
    tts_skill.load_settings(settings)
    tts_skill.set_openrouter_client(get_client())
    return wake_word_skill, stt_skill, tts_skill


async def _voice_llm_handler(text: str) -> str:
    """Send a spoken request to the LLM (with tools) and return the full response."""
    client = get_client()
    if not client.api_key:
        return NO_API_KEY_MESSAGE
    try:
        return await complete_chat(client, get_skill_registry(), [*_system_message(), {"role": "user", "content": text}])
    except ChatLoopError as exc:
        return f"Error: {exc}"


async def _init_voice(settings: dict) -> None:
    if not VOICE_DEPS_AVAILABLE:
        logger.warning("Voice dependencies not available. Install with: pip install -e '.[voice]'")
        return
    wake_word_skill, stt_skill, tts_skill = _voice_skills(settings)
    await tts_skill.initialize()
    pipeline = get_pipeline()
    pipeline.set_components(wake_word_skill, stt_skill, tts_skill)
    pipeline.set_llm_handler(_voice_llm_handler)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = load_settings()
    get_skill_registry()
    oauth = get_oauth_manager()
    background = []
    if oauth:
        oauth.load_settings(settings)
        # Loading may refresh an expired token over the network; never hold up startup for it.
        background.append(asyncio.create_task(asyncio.to_thread(oauth.load_credentials)))
    await _init_voice(settings)
    yield
    orchestrator = get_automation_orchestrator()
    if orchestrator.web_active:
        await orchestrator.stop_web()


app = FastAPI(title="Tesseract Sidecar", version=VERSION, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def reject_foreign_origins(request: Request, call_next):
    """CORS hides replies from other sites but still lets them send simple POSTs; refuse those here."""
    origin = request.headers.get("origin")
    if request.method not in ("GET", "HEAD") and origin and origin not in ALLOWED_ORIGINS:
        return JSONResponse({"detail": "Origin not allowed"}, status_code=403)
    return await call_next(request)


# Added last so it runs first: a DNS-rebinding page reaches us under its own host name.
app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)


# --- Core ------------------------------------------------------------------

class HealthResponse(BaseModel):
    status: str
    version: str


class MessageRequest(BaseModel):
    message: str
    model: str = DEFAULT_MODEL
    history: list[dict] = []
    chat_id: Optional[str] = None


@app.get("/health", response_model=HealthResponse)
def health():
    return HealthResponse(status="ok", version=VERSION)


def _sse(event: dict | str) -> str:
    payload = event if isinstance(event, str) else json.dumps(event, default=str)
    return f"data: {payload}\n\n"


def _system_message() -> list[dict]:
    """A system message with the current local time and who the model is talking to (Settings > Profile)."""
    # The model has no clock; without this, "tomorrow at 3pm" lands on a guessed day and time zone.
    facts = [f"The current local date and time is {datetime.now().astimezone().isoformat(timespec='minutes')}."]
    profile = load_settings().get("profile") or {}
    if profile.get("name"):
        facts.append(f"The person you are talking to is called {profile['name']}.")
    if profile.get("address_as"):
        facts.append(f"They like to be addressed as \"{profile['address_as']}\".")
    if profile.get("about"):
        facts.append(f"What they have told you about themselves: {profile['about']}")
    return [{"role": "system", "content": " ".join(facts)}]


def _conversation(request: MessageRequest) -> list[dict]:
    return [*_system_message(), *request.history, {"role": "user", "content": request.message}]


class _TurnRecorder:
    """Collects what the assistant produced in one streamed turn, for saving."""

    def __init__(self):
        self.parts: list[str] = []
        self.tools: list[dict] = []

    def observe(self, event: dict) -> None:
        if event.get("content"):
            self.parts.append(event["content"])
        tool = event.get("tool")
        if not tool:
            return
        record = {"name": tool.get("name"), "status": tool.get("status"), "error": tool.get("error")}
        for existing in self.tools:
            if record["status"] != "running" and existing["name"] == record["name"] and existing["status"] == "running":
                existing.update(record)
                return
        self.tools.append(record)

    @property
    def text(self) -> str:
        return "".join(self.parts)


def _save_turn(chat_id: str, recorder: _TurnRecorder) -> None:
    store = get_chat_store()
    if store.get_chat(chat_id) is None:  # deleted while the answer was streaming
        return
    if recorder.text or recorder.tools:
        store.add_message(chat_id, "assistant", recorder.text, recorder.tools)


@app.post("/chat/stream")
async def chat_stream(request: MessageRequest):
    """Stream a chat response with skill tool calling, saving the turn when chat_id is given.

    Events: {"content"}, {"tool": {"name", "status"}}, {"error", "message"}, then [DONE].
    """
    client = get_client()
    registry = get_skill_registry()
    chat = get_chat_store().get_chat(request.chat_id) if request.chat_id else None

    async def generate() -> AsyncGenerator[str, None]:
        if request.chat_id and chat is None:
            yield _sse({"error": True, "code": "chat_not_found", "message": "This chat no longer exists."})
        elif not client.api_key:
            yield _sse({"error": True, "code": "no_api_key", "message": NO_API_KEY_MESSAGE})
        else:
            async for event in _stream_turn(client, registry, request, chat):
                yield event
        yield _sse("[DONE]")

    return StreamingResponse(generate(), media_type="text/event-stream")


async def _stream_turn(client, registry, request: MessageRequest, chat: Optional[dict]) -> AsyncGenerator[str, None]:
    model = request.model
    conversation = _conversation(request)
    if chat is not None:
        store = get_chat_store()
        history = store.history(chat["id"])[-HISTORY_LIMIT:]
        conversation = [*_system_message(), *history, {"role": "user", "content": request.message}]
        if chat["model"] and request.model == DEFAULT_MODEL:
            model = chat["model"]
        store.add_message(chat["id"], "user", request.message)
        if not chat["title"]:
            store.update_chat(chat["id"], title=derive_title(request.message))

    recorder = _TurnRecorder()
    try:
        async for event in stream_chat(client, registry, conversation, model):
            recorder.observe(event)
            yield _sse(event)
    except Exception as exc:  # keep the stream well-formed for the UI
        logger.exception("Chat stream failed")
        yield _sse({"error": True, "message": f"Chat failed: {exc}"})
    finally:
        if chat is not None:
            _save_turn(chat["id"], recorder)


@app.get("/models")
async def list_models():
    """List available OpenRouter models."""
    client = get_client()
    if not client.api_key:
        return {"models": []}
    return {"models": await client.list_models()}


@app.get("/settings")
def get_settings():
    """Get current settings from the config file."""
    return load_settings()


def _apply_settings(settings: dict) -> None:
    """Point every running component at freshly saved settings."""
    global _client
    _client = _new_client(settings.get("openrouter_api_key", ""))
    oauth = get_oauth_manager()
    if oauth:
        oauth.load_settings(settings)
    _voice_skills(settings)


@app.put("/settings")
def replace_settings(settings: dict):
    """Save settings from the UI (merging into existing sections) and apply them."""
    merged = load_settings()
    for key, value in settings.items():
        current = merged.get(key)
        merged[key] = {**current, **value} if isinstance(current, dict) and isinstance(value, dict) else value
    save_settings(merged)
    _apply_settings(merged)
    return {"success": True}


@app.post("/settings/reload")
def reload_settings():
    """Re-read the settings file after the desktop app wrote it."""
    _apply_settings(load_settings())
    return {"success": True}


# --- Saved chats -----------------------------------------------------------

CHAT_NOT_FOUND = {404: {"description": "Chat not found"}}


class ChatCreateRequest(BaseModel):
    model: str = DEFAULT_MODEL


class ChatUpdateRequest(BaseModel):
    title: Optional[str] = None
    model: Optional[str] = None


def _require_chat(chat: Optional[dict]) -> dict:
    if chat is None:
        raise HTTPException(status_code=404, detail="Chat not found")
    return chat


@app.get("/chats")
def list_chats(q: str = ""):
    """Saved chats, newest first, optionally filtered by title or message text."""
    return {"chats": get_chat_store().list_chats(q)}


@app.post("/chats")
def create_chat(request: Optional[ChatCreateRequest] = None):
    """Start an empty chat."""
    return get_chat_store().create_chat((request or ChatCreateRequest()).model)


@app.get("/chats/{chat_id}", responses=CHAT_NOT_FOUND)
def get_chat(chat_id: str):
    """One chat with its messages."""
    return _require_chat(get_chat_store().get_chat(chat_id))


@app.patch("/chats/{chat_id}", responses=CHAT_NOT_FOUND)
def update_chat(chat_id: str, request: ChatUpdateRequest):
    """Rename a chat or change its model."""
    return _require_chat(get_chat_store().update_chat(chat_id, title=request.title, model=request.model))


@app.delete("/chats/{chat_id}", responses=CHAT_NOT_FOUND)
def delete_chat(chat_id: str):
    """Delete a chat and its messages."""
    if not get_chat_store().delete_chat(chat_id):
        raise HTTPException(status_code=404, detail="Chat not found")
    return {"success": True}


# --- Skills ----------------------------------------------------------------

@app.get("/skills")
def list_skills():
    """List all available skills with their definitions."""
    skills = get_skill_registry().get_all_skills()
    return {
        "skills": [
            {"name": skill.name, "description": skill.description, "parameters": skill.parameters}
            for skill in skills.values()
        ]
    }


# --- Voice -----------------------------------------------------------------

@app.get("/voice/status")
def voice_status():
    """Get voice pipeline status."""
    settings = load_settings()
    wake_word_skill, stt_skill, tts_skill = _voice_skills(settings)
    return {
        "wake_word": wake_word_skill.get_status(),
        "stt": stt_skill.get_status(),
        "tts": tts_skill.get_status(),
        "pipeline": get_pipeline().get_status(),
        "enabled": settings.get("voice", {}).get("voice_enabled", False),
        "deps_available": VOICE_DEPS_AVAILABLE,
    }


@app.post("/voice/stt/start")
async def stt_start_recording():
    """Start recording for STT."""
    _, stt_skill, _ = _voice_skills()
    return _result(await stt_skill.start_recording())


@app.post("/voice/stt/stop")
async def stt_stop_recording():
    """Stop recording and transcribe."""
    _, stt_skill, _ = _voice_skills()
    return _result(await stt_skill.stop_recording())


@app.post("/voice/tts/stop")
async def tts_stop():
    """Stop current TTS playback."""
    _, _, tts_skill = _voice_skills()
    return _result(await tts_skill.stop())


@app.post("/voice/pipeline/start")
async def start_voice_pipeline():
    """Start the full voice pipeline (wake word, STT, LLM, TTS loop)."""
    if not load_settings().get("voice", {}).get("voice_enabled", False):
        return {"success": False, "error": "Voice is disabled in settings"}
    return _result(await get_pipeline().start())


@app.post("/voice/pipeline/stop")
async def stop_voice_pipeline():
    """Stop the voice pipeline."""
    return _result(await get_pipeline().stop())


# --- Google account --------------------------------------------------------

class GoogleAuthConfigRequest(BaseModel):
    client_id: str
    client_secret: str


@app.get("/google/auth/url")
def google_auth_url():
    """Get the Google OAuth authorization URL."""
    oauth = get_oauth_manager()
    if oauth is None:
        return {"success": False, "error": GOOGLE_NOT_INSTALLED}
    if not oauth.is_configured():
        return {"success": False, "error": "Google OAuth not configured. Add a client ID and secret in Settings."}
    try:
        return {"success": True, "auth_url": oauth.get_authorization_url()}
    except Exception as exc:
        logger.exception("Error generating Google auth URL")
        return {"success": False, "error": str(exc)}


def _oauth_result_page(title: str, detail: str, status_code: int = 200) -> HTMLResponse:
    """Small page shown in the browser tab Google redirects back to."""
    body = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>{html.escape(title)} | Tesseract</title>
<style>body{{font:16px/1.5 system-ui,sans-serif;background:#f4f2ee;color:#1d1c1a;margin:0;
display:grid;place-items:center;min-height:100vh}}main{{max-width:28rem;padding:2rem}}
h1{{font-size:1.5rem;margin:0 0 .5rem}}p{{color:#5b5852}}</style></head>
<body><main><h1>{html.escape(title)}</h1><p>{html.escape(detail)}</p></main></body></html>"""
    return HTMLResponse(body, status_code=status_code)


@app.get("/oauth/callback", response_class=HTMLResponse)
def oauth_callback(request: Request):
    """Handle Google's redirect after the user grants or denies access."""
    error = request.query_params.get("error")
    if error:
        return _oauth_result_page("Google sign-in was cancelled", f"Google returned: {error}. You can close this tab.")

    code = request.query_params.get("code")
    if not code:
        return _oauth_result_page(SIGN_IN_FAILED, "The response did not include an authorization code.", 400)

    oauth = get_oauth_manager()
    if oauth is None:
        return _oauth_result_page(SIGN_IN_FAILED, GOOGLE_NOT_INSTALLED, 500)
    status = oauth.handle_callback(code, request.query_params.get("state"))
    if status.connected:
        account = status.email or "your Google account"
        return _oauth_result_page("Google account connected", f"Tesseract can now use {account}. You can close this tab.")
    return _oauth_result_page(SIGN_IN_FAILED, status.error or "Unknown error", 400)


@app.get("/google/auth/status")
def google_auth_status():
    """Get Google OAuth connection status."""
    oauth = get_oauth_manager()
    if oauth is None:
        return {
            "success": True, "connected": False, "configured": False, "email": None,
            "scopes": [], "expires_at": None, "error": GOOGLE_NOT_INSTALLED,
        }
    status = oauth.get_status()
    return {
        "success": True,
        "connected": status.connected,
        "configured": oauth.is_configured(),
        "email": status.email,
        "scopes": status.scopes,
        "expires_at": status.expires_at,
        "error": status.error,
    }


@app.post("/google/auth/configure")
def google_auth_configure(request: GoogleAuthConfigRequest):
    """Save Google OAuth client credentials."""
    oauth = get_oauth_manager()
    if oauth is None:
        return {"success": False, "error": GOOGLE_NOT_INSTALLED}
    oauth.configure(request.client_id, request.client_secret)
    update_section("google", {"client_id": request.client_id, "client_secret": request.client_secret})
    return {"success": True, "message": "Google OAuth configured"}


@app.post("/google/auth/disconnect")
async def google_auth_disconnect():
    """Revoke Google access and remove tokens."""
    oauth = get_oauth_manager()
    if oauth is None:
        return {"success": False, "connected": False, "error": GOOGLE_NOT_INSTALLED}
    status = await oauth.disconnect()
    return {"success": not status.connected, "connected": status.connected, "error": status.error}
