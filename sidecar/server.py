import json
import logging
import os
from typing import Optional, AsyncGenerator

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, RedirectResponse
from pydantic import BaseModel

from llm.chat_loop import ChatLoopError, complete_chat, stream_chat
from llm.openrouter import OpenRouterClient, DEFAULT_MODEL
from skills.registry import get_registry
from skills.skill_base import SkillResult
from voice import get_wake_word_skill, get_stt_skill, get_tts_skill, get_pipeline
from voice.wake_word import VOICE_DEPS_AVAILABLE
from auth.google_oauth import get_google_oauth_manager
from automation.orchestrator import AutomationOrchestrator, get_automation_orchestrator, AutomationMode
from automation.browser import WebAutomation
from automation.office import get_office_automation
from automation.policy import load_automation_policy

logger = logging.getLogger(__name__)

app = FastAPI(title="Tesseract Sidecar", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:1420", "http://127.0.0.1:1420"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
_client: Optional[OpenRouterClient] = None
_registry = None


def get_settings_path() -> str:
    """Get the path to the settings file."""
    home = os.path.expanduser("~")
    return os.path.join(home, ".tesseract", "settings.json")


def load_settings() -> dict:
    """Load settings from the config file."""
    path = get_settings_path()
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_settings(settings: dict) -> None:
    """Persist settings while keeping the file valid if interrupted."""
    path = get_settings_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary_path = f"{path}.tmp"
    with open(temporary_path, "w", encoding="utf-8") as file:
        json.dump(settings, file, indent=2)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporary_path, path)


def get_client() -> OpenRouterClient:
    global _client
    if _client is None:
        # Try to load API key from settings
        settings = load_settings()
        api_key = settings.get("openrouter_api_key", "")
        _client = OpenRouterClient(api_key=api_key)
    return _client


def get_skill_registry():
    """Get or initialize the skill registry."""
    global _registry
    if _registry is None:
        _registry = get_registry()
        _registry.load_skills()
    return _registry


class HealthResponse(BaseModel):
    status: str
    version: str


class MessageRequest(BaseModel):
    message: str
    model: str = DEFAULT_MODEL
    history: list[dict] = []


class SkillExecuteRequest(BaseModel):
    skill_name: str
    parameters: dict = {}


@app.on_event("startup")
async def startup_event():
    """Initialize skill registry and Google OAuth on startup."""
    get_skill_registry()

    # Initialize Google OAuth manager with settings
    settings = load_settings()
    oauth = get_google_oauth_manager()
    oauth.load_settings(settings)
    # Try to load saved credentials
    await oauth.load_credentials()


@app.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse(status="ok", version="0.1.0")


NO_API_KEY_MESSAGE = "Add your OpenRouter API key in Settings to start chatting."


def _sse(event: dict | str) -> str:
    payload = event if isinstance(event, str) else json.dumps(event, default=str)
    return f"data: {payload}\n\n"


def _conversation(request: MessageRequest) -> list[dict]:
    return [*request.history, {"role": "user", "content": request.message}]


@app.post("/chat/stream")
async def chat_stream(request: MessageRequest):
    """Stream a chat response from OpenRouter with skill tool calling."""
    client = get_client()
    registry = get_skill_registry()

    async def generate() -> AsyncGenerator[str, None]:
        if not client.api_key:
            yield _sse({"error": True, "code": "no_api_key", "message": NO_API_KEY_MESSAGE})
        else:
            try:
                async for event in stream_chat(client, registry, _conversation(request), request.model):
                    yield _sse(event)
            except Exception as exc:  # keep the stream well-formed for the UI
                logger.exception("Chat stream failed")
                yield _sse({"error": True, "message": f"Chat failed: {exc}"})
        yield _sse("[DONE]")

    return StreamingResponse(generate(), media_type="text/event-stream")


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
    models = await client.list_models()
    return {"models": models}


@app.post("/configure")
async def configure(data: dict):
    """Set API key and reconfigure the client."""
    global _client
    api_key = data.get("api_key", "")
    _client = OpenRouterClient(api_key=api_key)
    settings = load_settings()
    settings["openrouter_api_key"] = api_key
    save_settings(settings)
    return {"status": "ok"}


@app.get("/settings")
async def get_settings():
    """Get current settings from the config file."""
    return load_settings()


# --- Skill System Endpoints ---

@app.get("/skills")
async def list_skills():
    """List all available skills with their definitions."""
    registry = get_skill_registry()
    skills = registry.get_all_skills()
    return {
        "skills": [
            {
                "name": skill.name,
                "description": skill.description,
                "parameters": skill.parameters,
            }
            for skill in skills.values()
        ]
    }


@app.get("/skills/tools")
async def get_skill_tools():
    """Get all skills as OpenAI-compatible tool definitions."""
    registry = get_skill_registry()
    return {"tools": registry.get_tool_definitions()}


@app.post("/skills/execute")
async def execute_skill(request: SkillExecuteRequest):
    """Execute a skill by name with parameters."""
    registry = get_skill_registry()
    result = await registry.execute_skill(request.skill_name, **request.parameters)

    if isinstance(result, dict) and "success" in result:
        return result
    # Handle SkillResult dataclass
    return {
        "success": result.success,
        "data": result.data,
        "error": result.error,
    }


@app.post("/skills/reload")
async def reload_skills():
    """Reload all skills (useful for development)."""
    registry = get_skill_registry()
    skills = registry.reload()
    return {
        "status": "reloaded",
        "skills": list(skills.keys()),
    }


# --- Voice Pipeline Endpoints ---

class VoiceSettingsRequest(BaseModel):
    """Voice settings update request."""
    settings: dict


@app.on_event("startup")
async def init_voice_pipeline():
    """Initialize voice pipeline on startup."""
    settings = load_settings()
    voice_settings = settings.get("voice", {})

    if not VOICE_DEPS_AVAILABLE:
        logger.warning("Voice dependencies not available. Voice features disabled. Install with: pip install -e '.[voice]'")
        return

    # Initialize wake word skill
    wake_word_skill = get_wake_word_skill()
    wake_word_skill.load_settings(settings)

    # Initialize STT skill
    stt_skill = get_stt_skill()
    stt_skill.load_settings(settings)

    # Initialize TTS skill
    tts_skill = get_tts_skill()
    tts_skill.load_settings(settings)
    # Set OpenRouter client for TTS fallback
    client = get_client()
    tts_skill.set_openrouter_client(client)
    await tts_skill.initialize()

    # Initialize voice pipeline orchestrator
    pipeline = get_pipeline()
    pipeline.set_components(wake_word_skill, stt_skill, tts_skill)

    # Create LLM handler for the pipeline
    async def llm_handler(text: str) -> str:
        """Send text to the LLM (with tools) and return the full response."""
        client = get_client()
        if not client.api_key:
            return NO_API_KEY_MESSAGE
        try:
            return await complete_chat(client, get_skill_registry(), [{"role": "user", "content": text}])
        except ChatLoopError as exc:
            return f"Error: {exc}"

    pipeline.set_llm_handler(llm_handler)


@app.get("/voice/status")
async def voice_status():
    """Get voice pipeline status."""
    settings = load_settings()
    wake_word_skill = get_wake_word_skill()
    wake_word_skill.load_settings(settings)
    stt_skill = get_stt_skill()
    stt_skill.load_settings(settings)
    tts_skill = get_tts_skill()
    tts_skill.load_settings(settings)
    pipeline = get_pipeline()

    return {
        "wake_word": wake_word_skill.get_status(),
        "stt": stt_skill.get_status(),
        "tts": tts_skill.get_status(),
        "pipeline": pipeline.get_status(),
        "enabled": settings.get("voice", {}).get("voice_enabled", False),
    }


@app.post("/voice/wake-word/start")
async def start_wake_word():
    """Start wake word detection."""
    settings = load_settings()
    wake_word_skill = get_wake_word_skill()
    wake_word_skill.load_settings(settings)

    if not settings.get("voice", {}).get("wake_word_enabled", False):
        return {"success": False, "error": "Wake word detection is disabled in settings"}

    result = await wake_word_skill.start_listening()
    return {"success": result.success, "data": result.data, "error": result.error}


@app.post("/voice/wake-word/stop")
async def stop_wake_word():
    """Stop wake word detection."""
    wake_word_skill = get_wake_word_skill()
    result = await wake_word_skill.stop_listening()
    return {"success": result.success, "data": result.data, "error": result.error}


@app.post("/voice/stt/transcribe")
async def stt_transcribe(request: dict):
    """Transcribe audio file or start/stop recording."""
    audio_path = request.get("audio_path")
    settings = load_settings()
    stt_skill = get_stt_skill()
    stt_skill.load_settings(settings)

    if audio_path:
        result = await stt_skill.transcribe(audio_path=audio_path)
    else:
        result = await stt_skill.transcribe()

    return {"success": result.success, "data": result.data, "error": result.error}


@app.post("/voice/stt/start")
async def stt_start_recording():
    """Start recording for STT."""
    settings = load_settings()
    stt_skill = get_stt_skill()
    stt_skill.load_settings(settings)

    result = await stt_skill.start_recording()
    return {"success": result.success, "data": result.data, "error": result.error}


@app.post("/voice/stt/stop")
async def stt_stop_recording():
    """Stop recording and transcribe."""
    settings = load_settings()
    stt_skill = get_stt_skill()
    stt_skill.load_settings(settings)

    result = await stt_skill.stop_recording()
    return {"success": result.success, "data": result.data, "error": result.error}


@app.post("/voice/tts/speak")
async def tts_speak(request: dict):
    """Speak text using TTS."""
    text = request.get("text", "")
    if not text:
        return {"success": False, "error": "Text is required"}

    settings = load_settings()
    tts_skill = get_tts_skill()
    tts_skill.load_settings(settings)
    # Ensure OpenRouter client is set
    client = get_client()
    tts_skill.set_openrouter_client(client)

    result = await tts_skill.speak(text)
    return {"success": result.success, "data": result.data, "error": result.error}


@app.post("/voice/tts/stop")
async def tts_stop():
    """Stop current TTS playback."""
    settings = load_settings()
    tts_skill = get_tts_skill()
    tts_skill.load_settings(settings)

    result = await tts_skill.stop()
    return {"success": result.success, "data": result.data, "error": result.error}


@app.post("/voice/settings")
async def update_voice_settings(request: VoiceSettingsRequest):
    """Update voice settings."""
    settings = load_settings()
    if "voice" not in settings:
        settings["voice"] = {}
    settings["voice"].update(request.settings)

    # Save settings
    path = get_settings_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(settings, f, indent=2)

    # Reload voice skills with new settings
    wake_word_skill = get_wake_word_skill()
    wake_word_skill.load_settings(settings)
    stt_skill = get_stt_skill()
    stt_skill.load_settings(settings)
    tts_skill = get_tts_skill()
    tts_skill.load_settings(settings)
    client = get_client()
    tts_skill.set_openrouter_client(client)
    await tts_skill.initialize()

    return {"success": True, "settings": settings["voice"]}


# --- Voice Pipeline Orchestration Endpoints ---

@app.post("/voice/pipeline/start")
async def start_voice_pipeline():
    """Start the full voice pipeline (wake word → STT → LLM → TTS loop)."""
    settings = load_settings()
    if not settings.get("voice", {}).get("voice_enabled", False):
        return {"success": False, "error": "Voice is disabled in settings"}

    pipeline = get_pipeline()
    result = await pipeline.start()
    return {"success": result.success, "data": result.data, "error": result.error}


@app.post("/voice/pipeline/stop")
async def stop_voice_pipeline():
    """Stop the voice pipeline."""
    pipeline = get_pipeline()
    result = await pipeline.stop()
    return {"success": result.success, "data": result.data, "error": result.error}


@app.get("/voice/pipeline/status")
async def voice_pipeline_status():
    """Get voice pipeline orchestrator status."""
    pipeline = get_pipeline()
    return pipeline.get_status()


# --- Automation Orchestrator Initialization ---


@app.on_event("startup")
async def init_automation():
    """Initialize automation orchestrator on startup."""
    settings = load_settings()
    automation_settings = settings.get("automation", {})

    if not automation_settings.get("automation_enabled", False):
        logger.info("Automation is disabled in settings")
        return

    orchestrator = get_automation_orchestrator()
    # Pre-configure with settings
    orchestrator.mode = AutomationMode.WEB
    logger.info("Automation orchestrator initialized")


# --- Web Automation API Endpoints ---

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
    template: str | None = None
    slides: list[dict] = []
    output_path: str


def get_office_engine():
    """Get the Office engine configured by the user's automation settings."""
    settings = load_settings()
    automation = settings.get("automation", {})
    if not automation.get("automation_enabled", False):
        raise RuntimeError("Automation is disabled in settings")
    return get_office_automation(
        bool(automation.get("office_use_com", False)),
        automation.get("office_root"),
    )


@app.post("/automation/office/read-doc")
def office_read_doc(request: OfficeReadDocumentRequest):
    """Read a Word document."""
    try:
        return {"success": True, "data": get_office_engine().read_doc(request.path)}
    except Exception as exc:
        logger.error("Error reading Word document: %s", exc)
        return {"success": False, "error": str(exc)}


@app.post("/automation/office/edit-doc")
def office_edit_doc(request: OfficeEditDocumentRequest):
    """Apply edits to a Word document."""
    try:
        return {"success": True, "data": get_office_engine().edit_doc(request.path, request.edits)}
    except Exception as exc:
        logger.error("Error editing Word document: %s", exc)
        return {"success": False, "error": str(exc)}


@app.post("/automation/office/create-pivot")
def office_create_pivot(request: OfficeCreatePivotRequest):
    """Create an Excel pivot workbook from CSV data."""
    try:
        return {"success": True, "data": get_office_engine().create_pivot(request.csv_path, request.config)}
    except Exception as exc:
        logger.error("Error creating pivot table: %s", exc)
        return {"success": False, "error": str(exc)}


@app.post("/automation/office/read-excel")
def office_read_excel(request: OfficeReadExcelRequest):
    """Read Excel workbook worksheets and rows."""
    try:
        return {"success": True, "data": get_office_engine().read_excel(request.path)}
    except Exception as exc:
        logger.error("Error reading Excel workbook: %s", exc)
        return {"success": False, "error": str(exc)}


@app.post("/automation/office/create-presentation")
def office_create_presentation(request: OfficeCreatePresentationRequest):
    """Create a PowerPoint presentation."""
    try:
        data = get_office_engine().create_presentation(
            request.template,
            request.slides,
            request.output_path,
        )
        return {"success": True, "data": data}
    except Exception as exc:
        logger.error("Error creating presentation: %s", exc)
        return {"success": False, "error": str(exc)}


@app.post("/automation/web/start")
async def start_web_automation(request: WebNavigateRequest = None):
    """Start web automation browser session."""
    settings = load_settings()
    automation_settings = settings.get("automation", {})

    if not automation_settings.get("automation_enabled", False):
        return {"success": False, "error": "Automation is disabled in settings"}

    orchestrator = get_automation_orchestrator()

    headless = automation_settings.get("headless_default", True)
    timeout = automation_settings.get("browser_timeout", 30000)

    if request:
        headless = request.headless

    try:
        await orchestrator.start_web(headless=headless, timeout=timeout)
        return {"success": True, "data": {"mode": "web", "headless": headless, "timeout": timeout}}
    except Exception as e:
        logger.error(f"Error starting web automation: {e}")
        return {"success": False, "error": str(e)}


@app.post("/automation/web/stop")
async def stop_web_automation():
    """Stop web automation browser session."""
    orchestrator = get_automation_orchestrator()
    try:
        await orchestrator.stop_web()
        return {"success": True, "data": {"status": "stopped"}}
    except Exception as e:
        logger.error(f"Error stopping web automation: {e}")
        return {"success": False, "error": str(e)}


@app.get("/automation/web/status")
async def web_automation_status():
    """Get web automation status."""
    orchestrator = get_automation_orchestrator()
    return orchestrator.get_status()


@app.post("/automation/web/navigate")
async def web_navigate(request: WebNavigateRequest):
    """Navigate to a URL."""
    orchestrator = get_automation_orchestrator()
    if not orchestrator.web_automation:
        return {"success": False, "error": "Web automation not started. Call /automation/web/start first."}

    try:
        result = await orchestrator.execute_web_action("navigate", url=request.url, wait_until=request.wait_until)
        return result
    except Exception as e:
        logger.error(f"Error navigating: {e}")
        return {"success": False, "error": str(e)}


@app.post("/automation/web/click")
async def web_click(request: WebActionRequest):
    """Click an element."""
    orchestrator = get_automation_orchestrator()
    if not orchestrator.web_automation:
        return {"success": False, "error": "Web automation not started."}

    try:
        result = await orchestrator.execute_web_action("click", selector=request.selector)
        return result
    except Exception as e:
        logger.error(f"Error clicking: {e}")
        return {"success": False, "error": str(e)}


@app.post("/automation/web/type")
async def web_type(request: WebTypeRequest):
    """Type into an element."""
    orchestrator = get_automation_orchestrator()
    if not orchestrator.web_automation:
        return {"success": False, "error": "Web automation not started."}

    try:
        result = await orchestrator.execute_web_action("type", selector=request.selector, text=request.text)
        return result
    except Exception as e:
        logger.error(f"Error typing: {e}")
        return {"success": False, "error": str(e)}


@app.post("/automation/web/extract")
async def web_extract(request: WebExtractRequest):
    """Extract content from elements."""
    orchestrator = get_automation_orchestrator()
    if not orchestrator.web_automation:
        return {"success": False, "error": "Web automation not started."}

    try:
        result = await orchestrator.execute_web_action("extract", selector=request.selector, attribute=request.attribute)
        return result
    except Exception as e:
        logger.error(f"Error extracting: {e}")
        return {"success": False, "error": str(e)}


@app.post("/automation/web/screenshot")
async def web_screenshot(request: WebScreenshotRequest):
    """Take a screenshot."""
    orchestrator = get_automation_orchestrator()
    if not orchestrator.web_automation:
        return {"success": False, "error": "Web automation not started."}

    try:
        result = await orchestrator.execute_web_action("screenshot", full_page=request.full_page)
        return result
    except Exception as e:
        logger.error(f"Error taking screenshot: {e}")
        return {"success": False, "error": str(e)}


@app.post("/automation/web/wait")
async def web_wait(request: WebWaitRequest):
    """Wait for element."""
    orchestrator = get_automation_orchestrator()
    if not orchestrator.web_automation:
        return {"success": False, "error": "Web automation not started."}

    try:
        result = await orchestrator.execute_web_action("wait_for", selector=request.selector, state=request.state)
        return result
    except Exception as e:
        logger.error(f"Error waiting: {e}")
        return {"success": False, "error": str(e)}


@app.post("/automation/web/content")
async def web_get_content():
    """Get page content for LLM analysis."""
    orchestrator = get_automation_orchestrator()
    if not orchestrator.web_automation:
        return {"success": False, "error": "Web automation not started."}

    try:
        result = await orchestrator.execute_web_action("get_page_content")
        return result
    except Exception as e:
        logger.error(f"Error getting page content: {e}")
        return {"success": False, "error": str(e)}


@app.post("/automation/settings")
async def update_automation_settings(request: dict):
    """Update automation settings."""
    settings = load_settings()
    if "automation" not in settings:
        settings["automation"] = {}
    settings["automation"].update(request.get("settings", {}))

    # Save settings
    path = get_settings_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    save_settings(settings)

    return {"success": True, "settings": settings["automation"]}


# --- Desktop Automation API Endpoints ---

class DesktopFindWindowRequest(BaseModel):
    title: str = ""
    class_name: str = ""
    process_name: str = ""


class DesktopFocusWindowRequest(BaseModel):
    handle: int = None


class DesktopGetElementsRequest(BaseModel):
    max_depth: int = 3
    include_children: bool = True


class DesktopClickRequest(BaseModel):
    name: str = ""
    control_type: str = ""
    automation_id: str = ""
    coordinates: list[int] = None


class DesktopTypeRequest(BaseModel):
    text: str
    name: str = ""
    control_type: str = ""
    automation_id: str = ""
    coordinates: list[int] = None


class DesktopGetTextRequest(BaseModel):
    name: str = ""
    control_type: str = ""
    automation_id: str = ""


class DesktopScreenshotRequest(BaseModel):
    region: list[int] = None
    full_screen: bool = False


class DesktopOCRRequest(BaseModel):
    region: list[int] = None
    image_base64: str = ""


class DesktopResizeWindowRequest(BaseModel):
    width: int
    height: int
    handle: int = None


@app.post("/automation/desktop/start")
async def start_desktop_automation():
    """Start desktop automation session."""
    settings = load_settings()
    automation_settings = settings.get("automation", {})

    if not automation_settings.get("automation_enabled", False):
        return {"success": False, "error": "Automation is disabled in settings"}

    orchestrator = get_automation_orchestrator()
    ocr_enabled = automation_settings.get("ocr_enabled", False)

    try:
        orchestrator.start_desktop(ocr_enabled=ocr_enabled)
        return {"success": True, "data": {"mode": "desktop", "ocr_enabled": ocr_enabled}}
    except Exception as e:
        logger.error(f"Error starting desktop automation: {e}")
        return {"success": False, "error": str(e)}


@app.post("/automation/desktop/stop")
async def stop_desktop_automation():
    """Stop desktop automation session."""
    orchestrator = get_automation_orchestrator()
    try:
        orchestrator.stop_desktop()
        return {"success": True, "data": {"status": "stopped"}}
    except Exception as e:
        logger.error(f"Error stopping desktop automation: {e}")
        return {"success": False, "error": str(e)}


@app.get("/automation/desktop/status")
def desktop_automation_status():
    """Get desktop automation status."""
    orchestrator = get_automation_orchestrator()
    return orchestrator.get_status()


@app.post("/automation/desktop/find-window")
def desktop_find_window(request: DesktopFindWindowRequest):
    """Find and activate a window."""
    orchestrator = get_automation_orchestrator()
    if not orchestrator.desktop_active:
        return {"success": False, "error": "Desktop automation not started. Call /automation/desktop/start first."}

    try:
        result = orchestrator.execute_desktop_action("find_window", title=request.title, class_name=request.class_name, process_name=request.process_name)
        return result
    except Exception as e:
        logger.error(f"Error finding window: {e}")
        return {"success": False, "error": str(e)}


@app.post("/automation/desktop/focus-window")
def desktop_focus_window(request: DesktopFocusWindowRequest):
    """Focus a window by handle."""
    orchestrator = get_automation_orchestrator()
    if not orchestrator.desktop_active:
        return {"success": False, "error": "Desktop automation not started."}

    try:
        result = orchestrator.execute_desktop_action("focus_window", handle=request.handle)
        return result
    except Exception as e:
        logger.error(f"Error focusing window: {e}")
        return {"success": False, "error": str(e)}


@app.post("/automation/desktop/elements")
def desktop_get_elements(request: DesktopGetElementsRequest):
    """Get UI elements from current window."""
    orchestrator = get_automation_orchestrator()
    if not orchestrator.desktop_active:
        return {"success": False, "error": "Desktop automation not started."}

    try:
        result = orchestrator.execute_desktop_action("get_ui_elements", max_depth=request.max_depth, include_children=request.include_children)
        return result
    except Exception as e:
        logger.error(f"Error getting UI elements: {e}")
        return {"success": False, "error": str(e)}


@app.post("/automation/desktop/click")
def desktop_click(request: DesktopClickRequest):
    """Click a UI element."""
    orchestrator = get_automation_orchestrator()
    if not orchestrator.desktop_active:
        return {"success": False, "error": "Desktop automation not started."}

    try:
        result = orchestrator.execute_desktop_action(
            "click",
            name=request.name or None,
            control_type=request.control_type or None,
            automation_id=request.automation_id or None,
            coordinates=tuple(request.coordinates) if request.coordinates else None,
        )
        return result
    except Exception as e:
        logger.error(f"Error clicking element: {e}")
        return {"success": False, "error": str(e)}


@app.post("/automation/desktop/type")
def desktop_type(request: DesktopTypeRequest):
    """Type text into a UI element."""
    orchestrator = get_automation_orchestrator()
    if not orchestrator.desktop_active:
        return {"success": False, "error": "Desktop automation not started."}

    try:
        result = orchestrator.execute_desktop_action(
            "type",
            text=request.text,
            name=request.name or None,
            control_type=request.control_type or None,
            automation_id=request.automation_id or None,
            coordinates=tuple(request.coordinates) if request.coordinates else None,
        )
        return result
    except Exception as e:
        logger.error(f"Error typing: {e}")
        return {"success": False, "error": str(e)}


@app.post("/automation/desktop/get-text")
def desktop_get_text(request: DesktopGetTextRequest):
    """Get text from a UI element."""
    orchestrator = get_automation_orchestrator()
    if not orchestrator.desktop_active:
        return {"success": False, "error": "Desktop automation not started."}

    try:
        result = orchestrator.execute_desktop_action(
            "get_text",
            name=request.name or None,
            control_type=request.control_type or None,
            automation_id=request.automation_id or None,
        )
        return result
    except Exception as e:
        logger.error(f"Error getting element text: {e}")
        return {"success": False, "error": str(e)}


@app.post("/automation/desktop/screenshot")
def desktop_screenshot(request: DesktopScreenshotRequest):
    """Take a screenshot."""
    orchestrator = get_automation_orchestrator()
    if not orchestrator.desktop_active:
        return {"success": False, "error": "Desktop automation not started."}

    try:
        result = orchestrator.execute_desktop_action("screenshot", region=tuple(request.region) if request.region else None, full_screen=request.full_screen)
        return result
    except Exception as e:
        logger.error(f"Error taking screenshot: {e}")
        return {"success": False, "error": str(e)}


@app.post("/automation/desktop/ocr")
def desktop_ocr(request: DesktopOCRRequest):
    """Extract text from screen region using OCR."""
    orchestrator = get_automation_orchestrator()
    if not orchestrator.desktop_active:
        return {"success": False, "error": "Desktop automation not started."}

    try:
        result = orchestrator.execute_desktop_action("ocr", region=tuple(request.region) if request.region else None, image_base64=request.image_base64 or None)
        return result
    except Exception as e:
        logger.error(f"Error running OCR: {e}")
        return {"success": False, "error": str(e)}


@app.post("/automation/desktop/resize-window")
def desktop_resize_window(request: DesktopResizeWindowRequest):
    """Resize a window."""
    orchestrator = get_automation_orchestrator()
    if not orchestrator.desktop_active:
        return {"success": False, "error": "Desktop automation not started."}

    try:
        result = orchestrator.execute_desktop_action("resize_window", handle=request.handle, width=request.width, height=request.height)
        return result
    except Exception as e:
        logger.error(f"Error resizing window: {e}")
        return {"success": False, "error": str(e)}


@app.get("/automation/desktop/list-windows")
def desktop_list_windows():
    """List all visible windows."""
    orchestrator = get_automation_orchestrator()
    if not orchestrator.desktop_active:
        policy = load_automation_policy()
        if not policy["automation_enabled"]:
            return {"success": False, "error": "Automation is disabled in settings"}
        from automation.desktop import get_desktop_automation
        da = get_desktop_automation()
        result = da.list_windows()
        return result

    try:
        result = orchestrator.execute_desktop_action("list_windows")
        return result
    except Exception as e:
        logger.error(f"Error listing windows: {e}")
        return {"success": False, "error": str(e)}


# --- Google OAuth Endpoints ---

class GoogleAuthConfigRequest(BaseModel):
    """Google OAuth configuration request."""
    client_id: str
    client_secret: str


@app.get("/google/auth/url")
async def google_auth_url():
    """Get Google OAuth authorization URL."""
    oauth = get_google_oauth_manager()
    if not oauth.is_configured():
        return {"success": False, "error": "Google OAuth not configured. Set client_id and client_secret in settings."}
    try:
        url = oauth.get_authorization_url()
        return {"success": True, "auth_url": url}
    except Exception as e:
        logger.error(f"Error generating auth URL: {e}")
        return {"success": False, "error": str(e)}


@app.get("/oauth/callback")
async def oauth_callback(request: Request):
    """Handle Google OAuth callback."""
    code = request.query_params.get("code")
    error = request.query_params.get("error")

    if error:
        return RedirectResponse(url=f"/oauth/callback?error={error}")

    if not code:
        return RedirectResponse(url="/oauth/callback?error=no_code")

    oauth = get_google_oauth_manager()
    status = await oauth.handle_callback(code)

    # Redirect to frontend with status
    if status.connected:
        return RedirectResponse(url="/?google_auth=success")
    else:
        return RedirectResponse(url=f"/?google_auth=error&message={status.error}")


@app.get("/google/auth/status")
async def google_auth_status():
    """Get Google OAuth connection status."""
    oauth = get_google_oauth_manager()
    status = await oauth.get_status()
    return {
        "success": True,
        "connected": status.connected,
        "email": status.email,
        "scopes": status.scopes,
        "expires_at": status.expires_at,
        "error": status.error,
    }


@app.post("/google/auth/configure")
async def google_auth_configure(request: GoogleAuthConfigRequest):
    """Configure Google OAuth credentials."""
    oauth = get_google_oauth_manager()
    oauth.configure(request.client_id, request.client_secret)

    # Save to settings file
    settings = load_settings()
    if "google" not in settings:
        settings["google"] = {}
    settings["google"]["client_id"] = request.client_id
    settings["google"]["client_secret"] = request.client_secret

    path = get_settings_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(settings, f, indent=2)

    return {"success": True, "message": "Google OAuth configured"}


@app.post("/google/auth/disconnect")
async def google_auth_disconnect():
    """Disconnect Google account and remove tokens."""
    oauth = get_google_oauth_manager()
    status = await oauth.disconnect()
    return {
        "success": not status.connected,
        "connected": status.connected,
        "error": status.error,
    }