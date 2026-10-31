"""FastAPI sidecar: chat, skills, voice and Google account endpoints."""

import asyncio
import html
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator, Literal, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from chat_store import derive_title, get_chat_store
from auth.chatgpt import CALLBACK_PATH, get_chatgpt_account
from automation.orchestrator import get_automation_orchestrator
from llm.approval_reply import classify_reply
from llm.chat_loop import ChatLoopError, complete_chat, stream_chat
from llm.openrouter import DEFAULT_MODEL, OpenRouterClient
from llm.providers import (
    KEY_PROVIDERS, PROVIDER_NAMES, connected_providers, get_provider_client, model_ref, parse_model,
    reset_provider_clients,
)
from permissions import Decision, get_broker, load_trusted, revoke
from file_access import LEVELS, access_level, known_folders
from secret_store import SecretStoreError, delete_secret, get_secret, migrate_plaintext_secrets, set_secret
from settings_store import load_settings, save_settings, update_section
from skills.registry import get_registry
from voice import get_pipeline, get_stt_skill, get_tts_skill, get_wake_word_skill
from voice.wake_word import VOICE_DEPS_AVAILABLE

logger = logging.getLogger(__name__)


class _HideQueryStrings(logging.Filter):
    """Uvicorn's access log prints each full request path, and sign-in callbacks carry codes in theirs."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and len(record.args) == 5 and isinstance(record.args[2], str):
            record.args = (*record.args[:2], record.args[2].split("?")[0], *record.args[3:])
        return True


logging.getLogger("uvicorn.access").addFilter(_HideQueryStrings())

VERSION = "0.1.0"
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

_registry = None


def client_for(model: str):
    """The client serving a model reference, and the provider's own id for that model."""
    provider, model_id = parse_model(model)
    return get_provider_client(provider), model_id


def _no_connection_message(model: str) -> str:
    return f"Connect {PROVIDER_NAMES[parse_model(model)[0]]} in Settings → Models & accounts to use this model."


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
    tts_skill.set_openrouter_client(get_provider_client("openrouter") or OpenRouterClient(""))
    return wake_word_skill, stt_skill, tts_skill


async def _voice_llm_handler(text: str) -> str:
    """Send a spoken request to the LLM (with tools) and return the full response."""
    model = load_settings().get("default_model") or DEFAULT_MODEL
    client, model_id = client_for(model)
    if client is None:
        return _no_connection_message(model)
    try:
        return await complete_chat(client, get_skill_registry(), [*_system_message(), {"role": "user", "content": text}], model_id)
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
    migrate_plaintext_secrets()
    settings = load_settings()
    get_skill_registry()
    oauth = get_oauth_manager()
    background = []
    if oauth:
        oauth.load_settings(settings)
        oauth.migrate_legacy_tokens()
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
    # Without real paths the model guesses them from the owner's name, and misses folders on other drives.
    folders = "; ".join(f"{name}: {path}" for name, path in known_folders().items())
    facts.append(f"Their home folder is {Path.home()} ({folders}). Relative paths start from {Path.cwd()}. "
                 "Use these exact paths and never guess a path from their name. "
                 f"Your file tools can reach {LEVELS[access_level()]}, but never ~/.tesseract. "
                 "If a location is refused, tell them it can be changed in Settings > Permissions.")
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


def _split_turn_at_reply(chat_id: str, recorder: _TurnRecorder, reply: str) -> _TurnRecorder:
    """Save what came before a typed approval reply, then the reply, so the chat reads as it happened."""
    _save_turn(chat_id, recorder)
    store = get_chat_store()
    if store.get_chat(chat_id) is not None:
        store.add_message(chat_id, "user", reply)
    return _TurnRecorder()


@app.post("/chat/stream")
async def chat_stream(request: MessageRequest):
    """Stream a chat response with skill tool calling, saving the turn when chat_id is given.

    Events: {"content"}, {"tool": {"name", "status"}}, {"approval"}, {"approval_resolved"},
    {"error", "message"}, then [DONE].
    """
    registry = get_skill_registry()
    chat = get_chat_store().get_chat(request.chat_id) if request.chat_id else None

    async def generate() -> AsyncGenerator[str, None]:
        if request.chat_id and chat is None:
            yield _sse({"error": True, "code": "chat_not_found", "message": "This chat no longer exists."})
        else:
            async for event in _stream_turn(registry, request, chat):
                yield event
        yield _sse("[DONE]")

    return StreamingResponse(generate(), media_type="text/event-stream")


async def _stream_turn(registry, request: MessageRequest, chat: Optional[dict]) -> AsyncGenerator[str, None]:
    model = request.model
    if chat is not None and chat["model"] and request.model == DEFAULT_MODEL:
        model = chat["model"]
    client, model_id = client_for(model)
    if client is None:
        yield _sse({"error": True, "code": "no_api_key", "message": _no_connection_message(model)})
        return
    conversation = _conversation(request)
    if chat is not None:
        store = get_chat_store()
        history = store.history(chat["id"])[-HISTORY_LIMIT:]
        conversation = [*_system_message(), *history, {"role": "user", "content": request.message}]
        store.add_message(chat["id"], "user", request.message)
        if not chat["title"]:
            store.update_chat(chat["id"], title=derive_title(request.message))

    recorder = _TurnRecorder()
    try:
        chat_id = chat["id"] if chat else None
        async for event in stream_chat(client, registry, conversation, model_id, approvals=get_broker(), chat_id=chat_id, model_ref=model):
            resolved = event.get("approval_resolved")
            if chat_id and resolved and resolved["via"] == "reply":
                recorder = _split_turn_at_reply(chat_id, recorder, resolved["reply"])
            recorder.observe(event)
            yield _sse(event)
    except Exception as exc:  # keep the stream well-formed for the UI
        logger.exception("Chat stream failed")
        yield _sse({"error": True, "message": f"Chat failed: {exc}"})
    finally:
        if chat is not None:
            _save_turn(chat["id"], recorder)


def _account(provider: str) -> dict:
    key = get_secret(KEY_PROVIDERS[provider][0])
    return {"connected": bool(key), "key_hint": "…" + key[-4:] if key else None}


@app.get("/accounts")
def list_accounts():
    """Which accounts are connected, with only the last four characters of any key."""
    return {"chatgpt": get_chatgpt_account().status(), **{provider: _account(provider) for provider in KEY_PROVIDERS}}


class SignInBody(BaseModel):
    new_account: bool = False


@app.post("/accounts/chatgpt/sign-in")
async def chatgpt_sign_in(request: Request, body: Optional[SignInBody] = None):
    """Start Continue with ChatGPT; OpenAI only accepts a 127.0.0.1 callback, whatever host the UI used."""
    # async so start_sign_in runs on the event loop, like finish_sign_in, and never on a worker thread.
    redirect_uri = f"http://127.0.0.1:{request.url.port}{CALLBACK_PATH}"
    try:
        return {"auth_url": get_chatgpt_account().start_sign_in(redirect_uri, new_account=bool(body and body.new_account))}
    except SecretStoreError as error:
        raise HTTPException(status_code=503, detail=str(error))


@app.get(CALLBACK_PATH, response_class=HTMLResponse)
async def chatgpt_callback(request: Request):
    """Handle OpenAI's redirect after Continue with ChatGPT; the page never repeats what the URL carried."""
    ok, message = await get_chatgpt_account().finish_sign_in(request.query_params)
    if not ok:
        return _oauth_result_page("ChatGPT sign-in failed", message, 400)
    reset_provider_clients()
    return _oauth_result_page("ChatGPT connected", message)


@app.post("/accounts/chatgpt/sign-out")
async def chatgpt_sign_out():
    try:
        revoked = await get_chatgpt_account().sign_out()
    except SecretStoreError as error:
        raise HTTPException(status_code=503, detail=str(error))
    reset_provider_clients()
    return {"revoked": revoked}


class KeyBody(BaseModel):
    api_key: str = Field(min_length=1)


def _key_provider(provider: str) -> tuple[str, str]:
    if provider not in KEY_PROVIDERS:
        raise HTTPException(status_code=404, detail="Unknown account.")
    return KEY_PROVIDERS[provider]


@app.put("/accounts/{provider}/key")
def save_account_key(provider: str, body: KeyBody):
    secret_name, prefix = _key_provider(provider)
    key = body.api_key.strip()
    if not key.startswith(prefix):
        raise HTTPException(
            status_code=422,
            detail=f"{PROVIDER_NAMES[provider]} keys start with {prefix}. Check that you copied the whole key.",
        )
    try:
        set_secret(secret_name, key)
    except SecretStoreError as error:
        raise HTTPException(status_code=503, detail=str(error))
    reset_provider_clients()
    return _account(provider)


@app.delete("/accounts/{provider}/key")
def remove_account_key(provider: str):
    secret_name, _ = _key_provider(provider)
    try:
        delete_secret(secret_name)
    except SecretStoreError as error:
        raise HTTPException(status_code=503, detail=str(error))
    reset_provider_clients()
    return _account(provider)


@app.get("/models")
async def list_models():
    """List the models of every connected provider; the UI groups them by provider."""
    providers = connected_providers()
    results = await asyncio.gather(*(get_provider_client(p).list_models() for p in providers), return_exceptions=True)
    models, errors = [], {}
    for provider, result in zip(providers, results):
        if isinstance(result, BaseException):
            logger.warning("Could not list %s models: %s", provider, type(result).__name__)
            errors[provider] = f"Could not load {PROVIDER_NAMES[provider]} models. Check the account in Settings → Models & accounts."
            continue
        models += [{"id": model_ref(provider, m["id"]), "name": m.get("name") or m["id"], "provider": provider} for m in result]
    return {"models": models, "errors": errors}


@app.get("/settings")
def get_settings():
    """Get current settings from the config file."""
    return load_settings()


def _apply_settings(settings: dict) -> None:
    """Point every running component at freshly saved settings."""
    migrate_plaintext_secrets()
    reset_provider_clients()
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


# --- Approvals -------------------------------------------------------------

APPROVAL_ENDED = "This request has already ended."


class ApprovalAnswer(BaseModel):
    decision: Literal["allow", "always", "deny"]


@app.post("/chat/approvals/{approval_id}", responses={404: {"description": APPROVAL_ENDED}})
async def answer_approval(approval_id: str, answer: ApprovalAnswer):
    """Answer an approval card with one of its buttons (async: the broker lives on the event loop)."""
    if not get_broker().resolve(approval_id, Decision(answer.decision, "button")):
        raise HTTPException(status_code=404, detail=APPROVAL_ENDED)
    return {"success": True}


class ApprovalReply(BaseModel):
    text: str = Field(min_length=1)


@app.post("/chat/approvals/{approval_id}/reply", responses={404: {"description": APPROVAL_ENDED}})
async def reply_to_approval(approval_id: str, reply: ApprovalReply):
    """Answer an approval card in the owner's own words."""
    broker = get_broker()
    request = broker.get(approval_id)
    if request is None:
        raise HTTPException(status_code=404, detail=APPROVAL_ENDED)
    client, model_id = client_for(request.model)
    if client is None:
        raise HTTPException(status_code=503, detail=_no_connection_message(request.model))
    verdict = await classify_reply(client, model_id, request, reply.text)
    if not broker.resolve(approval_id, Decision(verdict, "reply", reply.text)):  # ended while classifying
        raise HTTPException(status_code=404, detail=APPROVAL_ENDED)
    return {"decision": verdict}


@app.get("/permissions/trusted")
def list_trusted():
    """Tools the owner chose "Always allow this" for, by their plain title."""
    registry = get_skill_registry()
    rows = []
    for tool in load_trusted():
        skill = registry.get_skill(tool)
        rows.append({"tool": tool, "title": (skill.approval_title if skill else "") or tool})
    return {"trusted": sorted(rows, key=lambda row: row["title"])}


@app.delete("/permissions/trusted/{tool}", responses={404: {"description": "Not always allowed"}})
def revoke_trusted(tool: str):
    """Ask before this tool runs again."""
    if not revoke(tool):
        raise HTTPException(status_code=404, detail="This action is not always allowed.")
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
    client_secret: str = ""


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
    try:
        oauth.configure(request.client_id, request.client_secret)
    except SecretStoreError as exc:
        return {"success": False, "error": str(exc)}
    update_section("google", {"client_id": request.client_id})
    return {"success": True, "message": "Google OAuth configured"}


@app.post("/google/auth/disconnect")
async def google_auth_disconnect():
    """Revoke Google access and remove tokens."""
    oauth = get_oauth_manager()
    if oauth is None:
        return {"success": False, "connected": False, "error": GOOGLE_NOT_INSTALLED}
    status = await oauth.disconnect()
    return {"success": not status.connected, "connected": status.connected, "error": status.error}
