"""FastAPI sidecar: chat, skills, voice, automation and Google account endpoints."""

import asyncio
import html
import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

from automation.office import get_office_automation
from chat_store import derive_title, get_chat_store
from automation.orchestrator import get_automation_orchestrator
from automation.policy import load_automation_policy
from llm.chat_loop import ChatLoopError, complete_chat, stream_chat
from llm.openrouter import DEFAULT_MODEL, OPENROUTER_BASE, OpenRouterClient
from settings_store import load_settings, save_settings, settings_path, update_section
from skills.registry import get_registry
from voice import get_pipeline, get_stt_skill, get_tts_skill, get_wake_word_skill
from voice.wake_word import VOICE_DEPS_AVAILABLE

logger = logging.getLogger(__name__)

VERSION = "0.1.0"
NO_API_KEY_MESSAGE = "Add your OpenRouter API key in Settings to start chatting."
AUTOMATION_DISABLED = "Automation is disabled in settings"
WEB_NOT_STARTED = "Web automation not started. Call /automation/web/start first."
DESKTOP_NOT_STARTED = "Desktop automation not started. Call /automation/desktop/start first."
SIGN_IN_FAILED = "Google sign-in failed"
GOOGLE_NOT_INSTALLED = 'Google support is not installed. Run: pip install -e ".[google]"'

# Vite dev server plus the origins Tauri v2 serves the packaged app from.
ALLOWED_ORIGINS = [
    "http://localhost:1420",
    "http://127.0.0.1:1420",
    "tauri://localhost",
    "http://tauri.localhost",
    "https://tauri.localhost",
]

_client: Optional[OpenRouterClient] = None
_registry = None

# Kept for callers that imported this helper from server.
get_settings_path = settings_path


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
        return await complete_chat(client, get_skill_registry(), [{"role": "user", "content": text}])
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


# --- Core ------------------------------------------------------------------

class HealthResponse(BaseModel):
    status: str
    version: str


class MessageRequest(BaseModel):
    message: str
    model: str = DEFAULT_MODEL
    history: list[dict] = []
    chat_id: Optional[str] = None


class SkillExecuteRequest(BaseModel):
    skill_name: str
    parameters: dict = {}


@app.get("/health", response_model=HealthResponse)
def health():
    return HealthResponse(status="ok", version=VERSION)


def _sse(event: dict | str) -> str:
    payload = event if isinstance(event, str) else json.dumps(event, default=str)
    return f"data: {payload}\n\n"


def _profile_message() -> list[dict]:
    """A system message telling the model who it is talking to, from Settings > Profile."""
    profile = load_settings().get("profile") or {}
    facts = []
    if profile.get("name"):
        facts.append(f"The person you are talking to is called {profile['name']}.")
    if profile.get("address_as"):
        facts.append(f"They like to be addressed as \"{profile['address_as']}\".")
    if profile.get("about"):
        facts.append(f"What they have told you about themselves: {profile['about']}")
    return [{"role": "system", "content": " ".join(facts)}] if facts else []


def _conversation(request: MessageRequest) -> list[dict]:
    return [*_profile_message(), *request.history, {"role": "user", "content": request.message}]


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
        conversation = [*_profile_message(), *store.history(chat["id"]), {"role": "user", "content": request.message}]
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


@app.post("/chat", responses={502: {"description": "OpenRouter request failed"}})
async def chat(request: MessageRequest):
    """Non-streaming chat (fallback) with skill tools."""
    client = get_client()
    if not client.api_key:
        return {"response": NO_API_KEY_MESSAGE}
    try:
        text = await complete_chat(client, get_skill_registry(), _conversation(request), request.model)
    except ChatLoopError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"response": text}


@app.get("/models")
async def list_models():
    """List available OpenRouter models."""
    client = get_client()
    if not client.api_key:
        return {"models": []}
    return {"models": await client.list_models()}


@app.post("/configure")
def configure(data: dict):
    """Set API key and reconfigure the client."""
    global _client
    api_key = data.get("api_key", "")
    _client = _new_client(api_key)
    settings = load_settings()
    settings["openrouter_api_key"] = api_key
    save_settings(settings)
    return {"status": "ok"}


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


@app.get("/skills/tools")
def get_skill_tools():
    """Get all skills as OpenAI-compatible tool definitions."""
    return {"tools": get_skill_registry().get_tool_definitions()}


@app.post("/skills/execute")
async def execute_skill(request: SkillExecuteRequest):
    """Execute a skill by name (dotted or API-safe form) with parameters."""
    registry = get_skill_registry()
    result = await registry.execute_skill(registry.resolve_skill_name(request.skill_name), **request.parameters)
    return _result(result)


@app.post("/skills/reload")
def reload_skills():
    """Reload all skills (useful for development)."""
    return {"status": "reloaded", "skills": list(get_skill_registry().reload().keys())}


# --- Voice -----------------------------------------------------------------

class VoiceSettingsRequest(BaseModel):
    settings: dict


class SpeakRequest(BaseModel):
    text: str = ""


class TranscribeRequest(BaseModel):
    audio_path: Optional[str] = None


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


@app.post("/voice/wake-word/start")
async def start_wake_word():
    """Start wake word detection."""
    settings = load_settings()
    if not settings.get("voice", {}).get("wake_word_enabled", False):
        return {"success": False, "error": "Wake word detection is disabled in settings"}
    wake_word_skill, _, _ = _voice_skills(settings)
    return _result(await wake_word_skill.start_listening())


@app.post("/voice/wake-word/stop")
async def stop_wake_word():
    """Stop wake word detection."""
    return _result(await get_wake_word_skill().stop_listening())


@app.post("/voice/stt/transcribe")
async def stt_transcribe(request: TranscribeRequest):
    """Transcribe an audio file, or record from the microphone when no path is given."""
    _, stt_skill, _ = _voice_skills()
    if request.audio_path:
        return _result(await stt_skill.transcribe(audio_path=request.audio_path))
    return _result(await stt_skill.transcribe())


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


@app.post("/voice/tts/speak")
async def tts_speak(request: SpeakRequest):
    """Speak text using TTS."""
    if not request.text:
        return {"success": False, "error": "Text is required"}
    _, _, tts_skill = _voice_skills()
    return _result(await tts_skill.speak(request.text))


@app.post("/voice/tts/stop")
async def tts_stop():
    """Stop current TTS playback."""
    _, _, tts_skill = _voice_skills()
    return _result(await tts_skill.stop())


@app.post("/voice/settings")
async def update_voice_settings(request: VoiceSettingsRequest):
    """Update voice settings and reconfigure voice components."""
    settings = update_section("voice", request.settings)
    _, _, tts_skill = _voice_skills(settings)
    await tts_skill.initialize()
    return {"success": True, "settings": settings["voice"]}


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


@app.get("/voice/pipeline/status")
def voice_pipeline_status():
    """Get voice pipeline orchestrator status."""
    return get_pipeline().get_status()


# --- Office automation -----------------------------------------------------

class OfficeReadDocumentRequest(BaseModel):
    path: str


class OfficeEditDocumentRequest(BaseModel):
    path: str
    edits: list[dict] = []


class OfficeCreatePivotRequest(BaseModel):
    csv_path: str
    config: dict = {}


class OfficeReadExcelRequest(BaseModel):
    path: str


class OfficeCreatePresentationRequest(BaseModel):
    template: Optional[str] = None
    slides: list[dict] = []
    output_path: str


def _office_call(description: str, operation) -> dict:
    """Run an Office operation if automation is enabled, wrapping errors."""
    automation = load_settings().get("automation", {})
    if not automation.get("automation_enabled", False):
        return {"success": False, "error": AUTOMATION_DISABLED}
    try:
        engine = get_office_automation(bool(automation.get("office_use_com", False)), automation.get("office_root"))
        return {"success": True, "data": operation(engine)}
    except Exception as exc:
        logger.exception("Office automation failed: %s", description)
        return {"success": False, "error": str(exc)}


@app.post("/automation/office/read-doc")
def office_read_doc(request: OfficeReadDocumentRequest):
    """Read a Word document."""
    return _office_call("read document", lambda engine: engine.read_doc(request.path))


@app.post("/automation/office/edit-doc")
def office_edit_doc(request: OfficeEditDocumentRequest):
    """Apply edits to a Word document."""
    return _office_call("edit document", lambda engine: engine.edit_doc(request.path, request.edits))


@app.post("/automation/office/create-pivot")
def office_create_pivot(request: OfficeCreatePivotRequest):
    """Create an Excel pivot workbook from CSV data."""
    return _office_call("create pivot", lambda engine: engine.create_pivot(request.csv_path, request.config))


@app.post("/automation/office/read-excel")
def office_read_excel(request: OfficeReadExcelRequest):
    """Read Excel workbook worksheets and rows."""
    return _office_call("read workbook", lambda engine: engine.read_excel(request.path))


@app.post("/automation/office/create-presentation")
def office_create_presentation(request: OfficeCreatePresentationRequest):
    """Create a PowerPoint presentation."""
    return _office_call(
        "create presentation",
        lambda engine: engine.create_presentation(request.template, request.slides, request.output_path),
    )


# --- Web automation --------------------------------------------------------

class WebStartRequest(BaseModel):
    url: Optional[str] = None
    wait_until: str = "networkidle"
    headless: Optional[bool] = None


class WebNavigateRequest(BaseModel):
    url: str
    wait_until: str = "networkidle"
    headless: bool = True


class WebActionRequest(BaseModel):
    selector: str


class WebTypeRequest(BaseModel):
    selector: str
    text: str


class WebExtractRequest(BaseModel):
    selector: str
    attribute: str = "textContent"


class WebScreenshotRequest(BaseModel):
    full_page: bool = False


class WebWaitRequest(BaseModel):
    selector: str
    state: str = "visible"


async def _web_action(action: str, **kwargs: Any) -> dict:
    orchestrator = get_automation_orchestrator()
    if not orchestrator.web_automation:
        return {"success": False, "error": WEB_NOT_STARTED}
    try:
        return await orchestrator.execute_web_action(action, **kwargs)
    except Exception as exc:
        logger.exception("Web automation action failed: %s", action)
        return {"success": False, "error": str(exc)}


@app.post("/automation/web/start")
async def start_web_automation(request: Optional[WebStartRequest] = None):
    """Start web automation browser session."""
    automation = load_settings().get("automation", {})
    if not automation.get("automation_enabled", False):
        return {"success": False, "error": AUTOMATION_DISABLED}

    headless = automation.get("headless_default", True)
    if request and request.headless is not None:
        headless = request.headless
    timeout = automation.get("browser_timeout", 30000)
    try:
        await get_automation_orchestrator().start_web(headless=headless, timeout=timeout)
    except Exception as exc:
        logger.exception("Error starting web automation")
        return {"success": False, "error": str(exc)}
    return {"success": True, "data": {"mode": "web", "headless": headless, "timeout": timeout}}


@app.post("/automation/web/stop")
async def stop_web_automation():
    """Stop web automation browser session."""
    try:
        await get_automation_orchestrator().stop_web()
    except Exception as exc:
        logger.exception("Error stopping web automation")
        return {"success": False, "error": str(exc)}
    return {"success": True, "data": {"status": "stopped"}}


@app.get("/automation/web/status")
def web_automation_status():
    """Get web automation status."""
    return get_automation_orchestrator().get_status()


@app.post("/automation/web/navigate")
async def web_navigate(request: WebNavigateRequest):
    """Navigate to a URL."""
    return await _web_action("navigate", url=request.url, wait_until=request.wait_until)


@app.post("/automation/web/click")
async def web_click(request: WebActionRequest):
    """Click an element."""
    return await _web_action("click", selector=request.selector)


@app.post("/automation/web/type")
async def web_type(request: WebTypeRequest):
    """Type into an element."""
    return await _web_action("type", selector=request.selector, text=request.text)


@app.post("/automation/web/extract")
async def web_extract(request: WebExtractRequest):
    """Extract content from elements."""
    return await _web_action("extract", selector=request.selector, attribute=request.attribute)


@app.post("/automation/web/screenshot")
async def web_screenshot(request: WebScreenshotRequest):
    """Take a screenshot (base64 PNG)."""
    return await _web_action("screenshot", full_page=request.full_page)


@app.get("/automation/web/screenshot")
async def web_screenshot_get(full_page: bool = False):
    """Take a screenshot (base64 PNG)."""
    return await _web_action("screenshot", full_page=full_page)


@app.post("/automation/web/wait")
async def web_wait(request: WebWaitRequest):
    """Wait for element."""
    return await _web_action("wait_for", selector=request.selector, state=request.state)


@app.post("/automation/web/content")
async def web_get_content():
    """Get page content for LLM analysis."""
    return await _web_action("get_page_content")


@app.post("/automation/settings")
def update_automation_settings(request: dict):
    """Update automation settings."""
    settings = update_section("automation", request.get("settings", {}))
    return {"success": True, "settings": settings["automation"]}


# --- Desktop automation ----------------------------------------------------

class DesktopFindWindowRequest(BaseModel):
    title: str = ""
    class_name: str = ""
    process_name: str = ""


class DesktopFocusWindowRequest(BaseModel):
    handle: Optional[int] = None


class DesktopGetElementsRequest(BaseModel):
    max_depth: int = 3
    include_children: bool = True


class DesktopElementRequest(BaseModel):
    name: str = ""
    control_type: str = ""
    automation_id: str = ""


class DesktopClickRequest(DesktopElementRequest):
    coordinates: Optional[list[int]] = None


class DesktopTypeRequest(DesktopClickRequest):
    text: str


class DesktopScreenshotRequest(BaseModel):
    region: Optional[list[int]] = None
    full_screen: bool = False


class DesktopOCRRequest(BaseModel):
    region: Optional[list[int]] = None
    image_base64: str = ""


class DesktopResizeWindowRequest(BaseModel):
    width: int
    height: int
    handle: Optional[int] = None


def _element_kwargs(request: DesktopElementRequest) -> dict:
    return {
        "name": request.name or None,
        "control_type": request.control_type or None,
        "automation_id": request.automation_id or None,
    }


def _as_tuple(values: Optional[list[int]]) -> Optional[tuple]:
    return tuple(values) if values else None


def _desktop_action(action: str, **kwargs: Any) -> dict:
    orchestrator = get_automation_orchestrator()
    if not orchestrator.desktop_active:
        return {"success": False, "error": DESKTOP_NOT_STARTED}
    try:
        return orchestrator.execute_desktop_action(action, **kwargs)
    except Exception as exc:
        logger.exception("Desktop automation action failed: %s", action)
        return {"success": False, "error": str(exc)}


@app.post("/automation/desktop/start")
def start_desktop_automation():
    """Start desktop automation session."""
    automation = load_settings().get("automation", {})
    if not automation.get("automation_enabled", False):
        return {"success": False, "error": AUTOMATION_DISABLED}
    ocr_enabled = automation.get("ocr_enabled", False)
    try:
        get_automation_orchestrator().start_desktop(ocr_enabled=ocr_enabled)
    except Exception as exc:
        logger.exception("Error starting desktop automation")
        return {"success": False, "error": str(exc)}
    return {"success": True, "data": {"mode": "desktop", "ocr_enabled": ocr_enabled}}


@app.post("/automation/desktop/stop")
def stop_desktop_automation():
    """Stop desktop automation session."""
    try:
        get_automation_orchestrator().stop_desktop()
    except Exception as exc:
        logger.exception("Error stopping desktop automation")
        return {"success": False, "error": str(exc)}
    return {"success": True, "data": {"status": "stopped"}}


@app.get("/automation/desktop/status")
def desktop_automation_status():
    """Get desktop automation status."""
    return get_automation_orchestrator().get_status()


@app.post("/automation/desktop/find-window")
def desktop_find_window(request: DesktopFindWindowRequest):
    """Find and activate a window."""
    return _desktop_action(
        "find_window", title=request.title, class_name=request.class_name, process_name=request.process_name
    )


@app.post("/automation/desktop/focus-window")
def desktop_focus_window(request: DesktopFocusWindowRequest):
    """Focus a window by handle."""
    return _desktop_action("focus_window", handle=request.handle)


@app.post("/automation/desktop/elements")
def desktop_get_elements(request: DesktopGetElementsRequest):
    """Get the accessibility tree of the current window."""
    return _desktop_action("get_ui_elements", max_depth=request.max_depth, include_children=request.include_children)


@app.get("/automation/desktop/elements")
def desktop_get_elements_get(max_depth: int = 3, include_children: bool = True):
    """Get the accessibility tree of the current window."""
    return _desktop_action("get_ui_elements", max_depth=max_depth, include_children=include_children)


@app.post("/automation/desktop/click")
def desktop_click(request: DesktopClickRequest):
    """Click a UI element (accessibility, then OCR fallback)."""
    return _desktop_action("click", **_element_kwargs(request), coordinates=_as_tuple(request.coordinates))


@app.post("/automation/desktop/type")
def desktop_type(request: DesktopTypeRequest):
    """Type text into a UI element."""
    return _desktop_action(
        "type", text=request.text, **_element_kwargs(request), coordinates=_as_tuple(request.coordinates)
    )


@app.post("/automation/desktop/get-text")
def desktop_get_text(request: DesktopElementRequest):
    """Get text from a UI element (accessibility, then OCR fallback)."""
    return _desktop_action("get_text", **_element_kwargs(request))


@app.post("/automation/desktop/screenshot")
def desktop_screenshot(request: DesktopScreenshotRequest):
    """Take a screenshot."""
    return _desktop_action("screenshot", region=_as_tuple(request.region), full_screen=request.full_screen)


@app.post("/automation/desktop/ocr")
def desktop_ocr(request: DesktopOCRRequest):
    """Extract text from a screen region using OCR."""
    return _desktop_action("ocr", region=_as_tuple(request.region), image_base64=request.image_base64 or None)


@app.post("/automation/desktop/resize-window")
def desktop_resize_window(request: DesktopResizeWindowRequest):
    """Resize a window."""
    return _desktop_action("resize_window", handle=request.handle, width=request.width, height=request.height)


@app.get("/automation/desktop/list-windows")
def desktop_list_windows():
    """List all visible windows."""
    if get_automation_orchestrator().desktop_active:
        return _desktop_action("list_windows")
    if not load_automation_policy()["automation_enabled"]:
        return {"success": False, "error": AUTOMATION_DISABLED}
    from automation.desktop import get_desktop_automation

    return get_desktop_automation().list_windows()


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
