"""End-to-end: built React UI -> real sidecar -> mock OpenRouter, driven in Chromium.

Opt-in because it needs Node (it builds the UI against its own sidecar), a Playwright browser
(`python -m playwright install chromium`) and free ports 1420, 18765 and 18799. It does not use
8765, so a running copy of Tesseract can stay open:

    TESSERACT_E2E=1 python -m unittest tests.e2e.test_workspace_e2e
"""

import functools
import http.server
import json
import os
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import uvicorn

from tests.secret_doubles import MemoryKeyring

REPO = Path(__file__).resolve().parents[3]
UI_PORT, SIDECAR_PORT, MOCK_PORT = 1420, 18765, 18799
UI = f"http://127.0.0.1:{UI_PORT}"


def _serve_uvicorn(app, port: int) -> uvicorn.Server:
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    _wait_for_port(port)
    return server


def _port_in_use(port: int) -> bool:
    for family, host in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
        with socket.socket(family) as probe:
            if probe.connect_ex((host, port)) == 0:
                return True
    return False


def _wait_for_port(port: int, timeout: float = 20) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.1)
    raise RuntimeError(f"Nothing listening on port {port}")


@unittest.skipUnless(os.environ.get("TESSERACT_E2E") == "1", "set TESSERACT_E2E=1 to run browser tests")
class WorkspaceEndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        busy = [port for port in (UI_PORT, SIDECAR_PORT, MOCK_PORT) if _port_in_use(port)]
        if busy:
            raise RuntimeError(f"Stop whatever is using ports {busy} (a running app, sidecar or vite) first")

        cls.home = tempfile.TemporaryDirectory()
        cls.workdir = tempfile.TemporaryDirectory()
        cls.dist = Path(cls.home.name, "dist")
        subprocess.run(
            f'npx vite build --outDir "{cls.dist}" --emptyOutDir --logLevel error',
            cwd=REPO, shell=True, check=True,
            env={**os.environ, "VITE_SIDECAR_URL": f"http://127.0.0.1:{SIDECAR_PORT}"},
        )
        Path(cls.workdir.name, "notes.md").write_text("hello", encoding="utf-8")
        settings = Path(cls.home.name, ".tesseract", "settings.json")
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({"openrouter_api_key": "sk-or-e2e", "default_model": "mock/fast"}), encoding="utf-8")

        cls.patches = [
            patch.dict(os.environ, {"OPENROUTER_BASE_URL": f"http://127.0.0.1:{MOCK_PORT}/v1"}),
            patch("os.path.expanduser", return_value=cls.home.name),
        ]
        for active in cls.patches:
            active.start()
        import keyring

        cls.previous_keyring = keyring.get_keyring()
        keyring.set_keyring(MemoryKeyring())
        cls.previous_cwd = os.getcwd()
        os.chdir(cls.workdir.name)  # the filesystem skill is sandboxed to the working directory

        import server
        from llm import providers
        from tests.e2e import mock_openrouter

        providers.reset_provider_clients()
        cls.servers = [_serve_uvicorn(mock_openrouter.app, MOCK_PORT), _serve_uvicorn(server.app, SIDECAR_PORT)]

        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(cls.dist))
        cls.static = http.server.ThreadingHTTPServer(("127.0.0.1", UI_PORT), handler)
        threading.Thread(target=cls.static.serve_forever, daemon=True).start()

        from playwright.sync_api import sync_playwright

        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.static.shutdown()
        for running in cls.servers:
            running.should_exit = True
        os.chdir(cls.previous_cwd)
        import keyring

        keyring.set_keyring(cls.previous_keyring)
        for active in cls.patches:
            active.stop()
        import chat_store

        if chat_store._store is not None:
            chat_store._store.close()
            chat_store._store = None
        cls.home.cleanup()
        cls.workdir.cleanup()

    def setUp(self):
        self.page = self.browser.new_page(viewport={"width": 1280, "height": 800})

    def tearDown(self):
        self.page.close()

    def send(self, page, text: str) -> None:
        page.get_by_label("Message Tesseract").fill(text)
        page.keyboard.press("Enter")
        page.get_by_text(f"You asked: {text}").wait_for()

    def sidebar(self, page):
        return page.get_by_role("navigation", name="Chats")

    def open_new_chat(self, page, prompt: str) -> str:
        page.goto(f"{UI}/#/chat/new")
        self.send(page, prompt)
        page.wait_for_url("**/#/chat/*")
        chat_id = page.url.rsplit("/", 1)[-1]
        self.assertNotEqual(chat_id, "new")
        return chat_id

    def row(self, page, title: str):
        return self.sidebar(page).get_by_role("listitem").filter(has=page.get_by_role("link", name=title, exact=True))

    def test_new_chat_appears_in_sidebar_with_title(self):
        page = self.page
        self.open_new_chat(page, "Plan my week ahead")

        sidebar = self.sidebar(page)
        sidebar.get_by_role("link", name="Plan my week ahead", exact=True).wait_for()
        self.assertTrue(sidebar.get_by_role("heading", name="Today").is_visible())

    def test_switching_chats_restores_each_conversation(self):
        page = self.page
        self.open_new_chat(page, "Alpha topic question")
        self.open_new_chat(page, "Beta topic question")

        self.sidebar(page).get_by_role("link", name="Alpha topic question", exact=True).click()

        page.get_by_text("You asked: Alpha topic question").wait_for()
        page.get_by_text("You asked: Beta topic question").wait_for(state="detached")

    def test_rename_and_delete_from_sidebar(self):
        page = self.page
        self.open_new_chat(page, "Rename me please")

        self.row(page, "Rename me please").get_by_role("button", name="More options").click()
        page.get_by_role("menuitem", name="Rename").click()
        title = page.get_by_label("Chat title")
        title.fill("Weekly plan")
        title.press("Enter")
        self.sidebar(page).get_by_role("link", name="Weekly plan", exact=True).wait_for()

        self.row(page, "Weekly plan").get_by_role("button", name="More options").click()
        page.get_by_role("menuitem", name="Delete").click()
        page.get_by_role("button", name="Delete chat").click()
        self.sidebar(page).get_by_role("link", name="Weekly plan", exact=True).wait_for(state="detached")

    def test_deleting_open_chat_moves_to_another(self):
        page = self.page
        first_id = self.open_new_chat(page, "First of two chats")
        self.open_new_chat(page, "Second of two chats")

        self.row(page, "Second of two chats").get_by_role("button", name="More options").click()
        page.get_by_role("menuitem", name="Delete").click()
        page.get_by_role("button", name="Delete chat").click()

        page.wait_for_url(f"**/#/chat/{first_id}")
        page.get_by_text("You asked: First of two chats").wait_for()

    def test_search_filters_chats(self):
        page = self.page
        self.open_new_chat(page, "Zebra crossing unique")
        self.open_new_chat(page, "Quokka sighting unique")

        page.get_by_label("Search chats").fill("zebra")

        sidebar = self.sidebar(page)
        sidebar.get_by_role("link", name="Quokka sighting unique", exact=True).wait_for(state="detached")
        self.assertTrue(sidebar.get_by_role("link", name="Zebra crossing unique", exact=True).is_visible())

    def test_model_choice_is_remembered_per_chat(self):
        page = self.page
        self.open_new_chat(page, "Model memory A")
        page.get_by_label("Model", exact=True).select_option(label="Claude 3.5 Sonnet")
        self.open_new_chat(page, "Model memory B")
        page.get_by_label("Model", exact=True).select_option(label="Mock Fast")

        self.sidebar(page).get_by_role("link", name="Model memory A", exact=True).click()
        page.get_by_text("You asked: Model memory A").wait_for()

        self.assertEqual(page.get_by_label("Model", exact=True).input_value(), "anthropic/claude-3.5-sonnet")

    def test_scroll_position_is_remembered_per_chat(self):
        page = self.browser.new_page(viewport={"width": 1280, "height": 600})
        try:
            self.open_new_chat(page, "Scroll memory first line")
            for index in range(5):
                self.send(page, f"Scroll memory filler {index} with a few more words")
            page.get_by_role("log").hover()
            page.mouse.wheel(0, -6000)
            page.wait_for_function("() => document.querySelector('[role=log]').scrollTop < 40", timeout=4000)

            self.open_new_chat(page, "Scroll memory other chat")
            self.sidebar(page).get_by_role("link", name="Scroll memory first line", exact=True).click()
            page.get_by_text("You asked: Scroll memory first line").wait_for()

            page.wait_for_function("() => document.querySelector('[role=log]').scrollTop < 40", timeout=2000)
        finally:
            page.close()

    def test_zz_sidebar_shows_error_when_sidecar_offline(self):
        # Runs last (unittest sorts by name): it stops the sidecar for the rest of the class.
        self.servers[1].should_exit = True
        deadline = time.time() + 10
        while _port_in_use(SIDECAR_PORT) and time.time() < deadline:
            time.sleep(0.1)
        page = self.page
        page.goto(f"{UI}/#/chat/new")

        sidebar = self.sidebar(page)
        sidebar.get_by_text("Couldn't load chats").wait_for()
        self.assertTrue(sidebar.get_by_role("button", name="Check again").is_visible())
        self.assertEqual(sidebar.get_by_text("No chats yet").count(), 0)

    def test_launch_sequence_finishes_and_app_is_usable(self):
        page = self.page
        page.goto(f"{UI}/#/chat/new")

        page.locator(".launch-sequence").wait_for(state="attached", timeout=1000)
        page.locator(".launch-sequence").wait_for(state="detached", timeout=2500)
        self.assertTrue(page.get_by_label("Message Tesseract").is_editable())

    def test_reduced_motion_skips_launch_sequence(self):
        context = self.browser.new_context(reduced_motion="reduce", viewport={"width": 1280, "height": 800})
        try:
            page = context.new_page()
            page.goto(f"{UI}/#/chat/new", wait_until="domcontentloaded")
            page.get_by_label("Message Tesseract").wait_for()

            self.assertEqual(page.locator(".launch-sequence").count(), 0)
        finally:
            context.close()

    def test_jump_pill_is_hidden_on_an_empty_chat(self):
        page = self.browser.new_page(viewport={"width": 600, "height": 400})
        try:
            page.goto(f"{UI}/#/chat/new")
            page.get_by_label("Message Tesseract").wait_for()
            page.get_by_role("log").hover()
            page.mouse.wheel(0, -2000)
            page.wait_for_timeout(600)

            self.assertEqual(page.get_by_role("button", name="Jump to latest").count(), 0)
        finally:
            page.close()

    def test_jump_to_latest_appears_after_scrolling_up(self):
        page = self.browser.new_page(viewport={"width": 1280, "height": 600})
        try:
            page.goto(f"{UI}/#/chat/new")
            for index in range(6):
                self.send(page, f"Message number {index} with enough words to take up a line")

            log = page.get_by_role("log")
            log.hover()
            page.mouse.wheel(0, -4000)
            jump = page.get_by_role("button", name="Jump to latest")
            jump.wait_for()
            jump.click()

            page.wait_for_function(
                "() => { const el = document.querySelector('[role=log]');"
                " return el.scrollHeight - el.scrollTop - el.clientHeight < 4; }",
                timeout=3000,
            )
        finally:
            page.close()

    def test_prompt_runs_a_tool_and_shows_the_answer(self):
        page = self.page
        page.goto(f"{UI}/#/chat/new")
        page.get_by_text("Assistant ready").wait_for()

        page.get_by_label("Message Tesseract").fill("List the files here")
        page.get_by_role("button", name="Send").click()

        log = page.get_by_role("list", name="What Tesseract did")
        log.get_by_text("Listing a folder").wait_for()
        log.get_by_text("Done").wait_for()
        page.get_by_text("Your working folder contains").wait_for()
        self.assertTrue(page.get_by_text("notes.md").is_visible())

    def test_plain_reply_renders_markdown(self):
        page = self.page
        page.goto(f"{UI}/#/chat/new")
        page.get_by_label("Message Tesseract").fill("Say hi")
        page.keyboard.press("Enter")

        strong = page.locator(".markdown strong", has_text="Mock reply.")
        strong.wait_for()
        self.assertTrue(strong.is_visible())

    def test_conversation_survives_visiting_another_page(self):
        page = self.page
        page.goto(f"{UI}/#/chat/new")
        page.get_by_label("Message Tesseract").fill("Remember this line")
        page.keyboard.press("Enter")
        page.get_by_text("You asked: Remember this line").wait_for()

        page.get_by_role("link", name="Settings", exact=True).click()
        page.get_by_role("heading", name="Profile").wait_for()
        self.sidebar(page).get_by_role("link", name="Remember this line", exact=True).click()

        page.get_by_text("You asked: Remember this line").wait_for(timeout=3000)

    def test_settings_save_reaches_the_settings_file(self):
        page = self.page
        page.goto(f"{UI}/#/settings/automation")
        page.get_by_label("Allow automation").check()
        page.get_by_role("button", name="Save settings").click()
        page.get_by_text("Settings saved").wait_for()

        saved = json.loads(Path(self.home.name, ".tesseract", "settings.json").read_text(encoding="utf-8"))
        self.assertTrue(saved["automation"]["automation_enabled"])
        self.assertEqual(saved.get("openrouter_api_key", ""), "")  # moved into the vault at startup
        import secret_store

        self.assertEqual(secret_store.get_secret("openrouter_api_key"), "sk-or-e2e")

    def openrouter_card(self, page):
        return page.locator(".account-card").filter(has=page.get_by_role("heading", name="OpenRouter"))

    def test_openrouter_key_card_shows_saved_key_and_replace(self):
        page = self.page
        page.goto(f"{UI}/#/settings/models")
        page.get_by_text("ending in -e2e").wait_for()

        page.get_by_role("button", name="Replace key").click()
        page.get_by_label("OpenRouter API key").fill("not-a-key")
        self.openrouter_card(page).get_by_role("button", name="Save key").click()

        page.get_by_text("sk-or-", exact=False).first.wait_for()

    def test_model_picker_groups_connected_models(self):
        page = self.page
        page.goto(f"{UI}/#/chat/new")
        picker = page.get_by_label("Model", exact=True)
        picker.wait_for()

        self.assertEqual(picker.locator("optgroup").evaluate_all("groups => groups.map(g => g.label)"), ["OpenRouter"])
        self.assertIn("Mock Fast", picker.locator('optgroup[label="OpenRouter"] option').all_inner_texts())

    def test_zy_no_accounts_shows_connect_a_model(self):
        page = self.page
        page.goto(f"{UI}/#/settings/models")
        page.get_by_role("button", name="Remove").click()
        page.get_by_label("OpenRouter API key").wait_for()
        # Settings already lists the accounts, so its default-model picker says so instead of linking back here.
        page.get_by_text("Connect an account above to choose a default model.").wait_for()
        self.assertEqual(page.get_by_role("button", name="Connect a model").count(), 0)

        page.goto(f"{UI}/#/chat/new")
        page.get_by_role("button", name="Connect a model").click()
        page.wait_for_url("**/#/settings/models")

        page.get_by_label("OpenRouter API key").fill("sk-or-e2e")
        self.openrouter_card(page).get_by_role("button", name="Save key").click()
        page.get_by_text("ending in -e2e").wait_for()

    def test_legal_pages_live_under_about(self):
        page = self.page
        page.goto(f"{UI}/#/settings/about")
        page.get_by_role("link", name="Privacy Policy").click()
        page.get_by_role("heading", name="Privacy Policy", level=1).wait_for()
        self.assertIn("#/settings/about/privacy", page.url)
        page.goto(f"{UI}/#/terms")
        page.get_by_role("heading", name="Terms of Service", level=1).wait_for()

    def test_theme_switch_applies_instantly(self):
        page = self.page
        page.goto(f"{UI}/#/settings/appearance")
        page.get_by_role("radio", name="Dark").check()

        page.wait_for_function("() => document.documentElement.dataset.theme === 'dark'", timeout=2000)
        page.reload()
        page.wait_for_function("() => document.documentElement.dataset.theme === 'dark'", timeout=2000)
        page.get_by_role("radio", name="Light").check()

    def test_system_theme_follows_os_live(self):
        page = self.page
        page.goto(f"{UI}/#/settings/appearance")
        page.get_by_role("radio", name="System").check()

        page.emulate_media(color_scheme="dark")
        page.wait_for_function("() => document.documentElement.dataset.theme === 'dark'", timeout=2000)
        page.emulate_media(color_scheme="light")
        page.wait_for_function("() => document.documentElement.dataset.theme === 'light'", timeout=2000)

    # --- Approvals: "save a note" makes the mock model call filesystem__write_file on approved.txt.

    def approved_file(self) -> Path:
        return Path(self.workdir.name, "approved.txt")

    def ask_to_save(self, page, prompt: str, route: str = "#/chat/new"):
        """Send a prompt that needs a write and return its approval card."""
        self.approved_file().unlink(missing_ok=True)
        page.goto(f"{UI}/{route}")
        page.get_by_label("Message Tesseract").fill(prompt)
        page.keyboard.press("Enter")
        card = page.get_by_role("region", name="Approval needed: Write a file")
        card.wait_for()
        return card

    def test_change_tool_asks_and_allow_writes_the_file(self):
        page = self.page
        card = self.ask_to_save(page, "Please save a note now")
        self.assertTrue(card.locator("dd", has_text="approved.txt").is_visible())

        card.get_by_role("button", name="Allow", exact=True).click()

        card.get_by_role("status").filter(has_text="Allowed").wait_for()
        page.get_by_text("Saved approved.txt.").wait_for()
        self.assertEqual(self.approved_file().read_text(encoding="utf-8"), "hello from the e2e test")

    def test_dont_allow_leaves_the_file_alone(self):
        page = self.page
        card = self.ask_to_save(page, "Please save a note, then I will refuse")

        card.get_by_role("button", name="Don't allow").click()

        page.get_by_role("list", name="What Tesseract did").get_by_text("Not allowed").wait_for()
        page.get_by_text("Okay, I left it alone.").wait_for()
        self.assertFalse(self.approved_file().exists())

    def test_typed_yes_answers_the_card(self):
        page = self.page
        card = self.ask_to_save(page, "Please save a note, I will type yes")
        self.assertTrue(page.get_by_role("button", name="Reply").is_visible())

        page.get_by_label("Message Tesseract").fill("yes go ahead")
        page.keyboard.press("Enter")

        card.get_by_role("status").filter(has_text="Allowed from your reply").wait_for()
        page.get_by_role("article", name="You said").filter(has_text="yes go ahead").wait_for()
        page.get_by_text("Saved approved.txt.").wait_for()
        self.assertTrue(self.approved_file().exists())

    def test_always_allow_shows_in_permissions_and_revoke_restores_asking(self):
        page = self.page
        card = self.ask_to_save(page, "Please save a note and always allow it")
        card.get_by_role("button", name="Always allow this").click()
        page.get_by_text("Saved approved.txt.").wait_for()

        page.goto(f"{UI}/#/settings/permissions")
        trusted = page.get_by_role("list", name="Always allowed")
        trusted.get_by_text("Write a file").wait_for()

        self.approved_file().unlink()
        page.goto(f"{UI}/#/chat/new")
        page.get_by_label("Message Tesseract").fill("Please save a note without asking")
        page.keyboard.press("Enter")
        page.get_by_text("Saved approved.txt.").wait_for()
        self.assertEqual(page.get_by_role("region", name="Approval needed: Write a file").count(), 0)
        self.assertTrue(self.approved_file().exists())

        page.goto(f"{UI}/#/settings/permissions")
        page.get_by_role("button", name="Revoke Write a file").click()
        page.get_by_text("Nothing is always allowed.").wait_for()

        card = self.ask_to_save(page, "Please save a note after revoking")
        card.get_by_role("button", name="Don't allow").click()
        page.get_by_text("Okay, I left it alone.").wait_for()

    def test_pending_card_survives_visiting_settings(self):
        page = self.page
        self.ask_to_save(page, "Please save a note across settings")
        page.wait_for_url("**/#/chat/*")

        page.get_by_role("link", name="Settings", exact=True).click()
        page.get_by_role("heading", name="Profile").wait_for()
        page.go_back()  # the sidebar shows the chat's title only once the turn is saved

        card = page.get_by_role("region", name="Approval needed: Write a file")
        card.get_by_role("button", name="Allow", exact=True).click()
        page.get_by_text("Saved approved.txt.").wait_for()
        self.assertTrue(self.approved_file().exists())

    def test_profile_saves_to_settings_file(self):
        page = self.page
        page.goto(f"{UI}/#/settings/profile")
        page.get_by_label("Your name").fill("Dhrishit")
        page.get_by_label("What should Tesseract call you?").fill("boss")
        page.get_by_role("button", name="Save settings").click()
        page.get_by_text("Settings saved").wait_for()

        saved = json.loads(Path(self.home.name, ".tesseract", "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["profile"]["name"], "Dhrishit")
        self.assertEqual(saved["profile"]["address_as"], "boss")

if __name__ == "__main__":
    unittest.main()
