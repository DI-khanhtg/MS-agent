"""Optional browser checks: uv run --with playwright pytest tests/browser -q."""

import socket
import threading
import time
from collections.abc import AsyncIterator

import pytest
import uvicorn

playwright = pytest.importorskip("playwright.sync_api")

from enterprise_agent.agent.runtime import AgentResult  # noqa: E402
from enterprise_agent.api.app import create_app  # noqa: E402
from enterprise_agent.config import Settings  # noqa: E402
from enterprise_agent.security.identity import Principal  # noqa: E402
from enterprise_agent.security.token_validation import AuthenticationError  # noqa: E402

TENANT = "11111111-1111-1111-1111-111111111111"
FRONTEND = "33333333-3333-3333-3333-333333333333"
BACKEND = "22222222-2222-2222-2222-222222222222"

MSAL_FIXTURE = """
window.nextLoginUser = "alice";
window.loginFailure = false;
window.msal = {
 PublicClientApplication: class {
  constructor(config) { window.receivedMsalConfig = config; }
  async initialize() {}
  getAllAccounts() { return []; }
  setActiveAccount(account) { this.account = account; }
  async loginPopup(request) {
   if (window.loginFailure) throw {errorCode: "user_cancelled"};
   window.requestedScopes = request.scopes;
   this.account = {username: window.nextLoginUser + "@example.test",
                   homeAccountId: window.nextLoginUser};
   return {account: this.account, accessToken: "token-" + window.nextLoginUser};
  }
  async acquireTokenSilent(request) {
   if (window.tokenExpired) throw {errorCode: "interaction_required"};
   return {account: request.account, accessToken: "token-" + request.account.homeAccountId};
  }
  async logoutPopup() { this.account = null; }
  async clearCache() { this.account = null; }
 }
};
"""


class Validator:
    async def validate(self, token):
        if token not in {"token-alice", "token-bob"}:
            raise AuthenticationError("invalid_token", "Invalid fixture token")
        user = token.removeprefix("token-")
        return Principal(
            TENANT,
            user,
            user,
            f"{user}@example.test",
            frozenset({"access_as_user"}),
            FRONTEND,
            token,
        )


class Runtime:
    async def chat(self, message, session_id, context=None):
        return AgentResult(text=f"Hello {context.principal.object_id}", tool_calls=[])

    async def stream_chat(self, message, session_id, context=None) -> AsyncIterator[str]:
        yield f"Hello {context.principal.object_id}"


@pytest.fixture
def app_url(tmp_path):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    url = f"http://localhost:{port}"
    settings = Settings(
        _env_file=None,
        auth_enabled=True,
        azure_tenant_id=TENANT,
        azure_client_id=BACKEND,
        azure_client_secret="test-secret",
        entra_api_audience=BACKEND,
        auth_allowed_tenant_ids=TENANT,
        entra_allowed_client_ids=FRONTEND,
        entra_spa_redirect_uri=f"{url}/auth/callback",
        enabled_tool_groups="local_read,artifact_generation",
        local_documents_root=tmp_path / "documents",
        artifact_storage_path=tmp_path / "artifacts",
        audit_log_path=tmp_path / "audit.jsonl",
    )
    app = create_app(settings=settings, runtime=Runtime(), token_validator=Validator())
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started
    yield url
    server.should_exit = True
    thread.join(timeout=5)
    sock.close()


def test_login_headers_folder_import_and_logout_isolate_accounts(app_url, tmp_path):
    with playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page()
        failures = []
        page.on("pageerror", lambda error: failures.append(str(error)))
        page.route(
            "**/static/vendor/msal-browser.min.js",
            lambda route: route.fulfill(content_type="text/javascript", body=MSAL_FIXTURE),
        )
        protected = []
        page.on(
            "request",
            lambda request: (
                protected.append(request)
                if "/api/" in request.url and "/api/auth/config" not in request.url
                else None
            ),
        )
        page.goto(app_url)
        playwright.expect(page.locator("#mode")).to_be_hidden()
        playwright.expect(page.locator("#microsoft-connection")).to_have_text("Kết nối Microsoft")
        assert not protected
        page.locator("[data-open-connection]").last.click()
        page.locator("#microsoft-login").click()
        playwright.expect(page.locator("#account-name")).to_have_text("alice@example.test")
        playwright.expect(page.locator("#microsoft-connection")).to_have_text(
            "Đã kết nối Microsoft"
        )
        playwright.expect(page.locator("#microsoft-connection")).to_have_attribute(
            "data-connected", "true"
        )
        playwright.expect(page.locator("#microsoft-connection")).to_have_attribute(
            "title", "alice@example.test"
        )
        playwright.expect(page.locator("#connection-symbol use")).to_have_attribute(
            "href", "#i-connected"
        )
        assert "gemini" not in page.locator("body").inner_text().lower()
        assert page.evaluate("receivedMsalConfig.cache.cacheLocation") == "memoryStorage"
        assert page.evaluate("requestedScopes") == [f"api://{BACKEND}/access_as_user"]
        leaked = []
        page.route(
            "https://outside.example/**",
            lambda route: (leaked.append(route.request.url), route.fulfill(status=403)),
        )
        error = page.evaluate("""async () => {
            try { await api('https://outside.example/private'); }
            catch (error) { return error.message; }
        }""")
        assert not leaked
        assert "cùng ứng dụng" in error
        folder = tmp_path / "Project-A"
        folder.mkdir()
        (folder / "requirements.md").write_text("Local project facts", encoding="utf-8")
        page.locator("#folder-upload").set_input_files(str(folder))
        playwright.expect(page.locator("#folder-hint")).to_contain_text("1 tệp")
        page.locator("#message").fill("hello")
        page.locator("#message").press("Enter")
        playwright.expect(page.locator(".bubble.agent")).to_have_text("Hello alice")
        assert any(
            request.headers.get("authorization") == "Bearer token-alice" for request in protected
        )
        # A response started by Alice must not render after logout.
        pending_history = []
        page.route("**/api/sessions/*", lambda route: pending_history.append(route))
        page.evaluate("""() => {
            const original = Response.prototype.json;
            Response.prototype.json = async function() {
                const value = await original.call(this);
                if (this.url.includes('/api/sessions/')) window.oldHistoryParsed = true;
                return value;
            };
        }""")
        page.locator("#sessions button").first.click()
        page.locator("[data-open-connection]").last.click()
        page.locator("#microsoft-logout").click()
        playwright.expect(page.locator("#microsoft-connection")).to_have_text("Kết nối Microsoft")
        playwright.expect(page.locator("#microsoft-connection")).to_have_attribute(
            "data-connected", "false"
        )
        assert pending_history
        pending_history[0].fulfill(
            json={"messages": [{"role": "user", "text": "ALICE PRIVATE HISTORY"}]}
        )
        page.wait_for_function("window.oldHistoryParsed === true")
        playwright.expect(page.locator("#messages .bubble")).to_have_count(0)
        playwright.expect(page.locator("#sessions button")).to_have_count(0)
        assert page.locator("#folder").input_value() == ""
        page.evaluate("nextLoginUser = 'bob'")
        page.locator("#microsoft-login").click()
        playwright.expect(page.locator("#account-name")).to_have_text("bob@example.test")
        playwright.expect(page.locator("#microsoft-connection")).to_have_attribute(
            "title", "bob@example.test"
        )
        playwright.expect(page.locator("#messages .bubble")).to_have_count(0)
        assert page.evaluate("Object.keys(localStorage).length") == 0
        assert not failures
        page.evaluate("tokenExpired = true")
        page.evaluate("api('/api/me').catch(() => {})")
        playwright.expect(page.locator("#microsoft-connection")).to_have_attribute(
            "data-connected", "false"
        )
        browser.close()


def test_folder_with_sixty_files_does_not_hit_chat_rate_limit(app_url, tmp_path):
    with playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page()
        page.route(
            "**/static/vendor/msal-browser.min.js",
            lambda route: route.fulfill(content_type="text/javascript", body=MSAL_FIXTURE),
        )
        page.goto(app_url)
        page.locator("[data-open-connection]").last.click()
        page.locator("#microsoft-login").click()
        playwright.expect(page.locator("#account-name")).to_have_text("alice@example.test")
        folder = tmp_path / "Many-documents"
        folder.mkdir()
        for index in range(60):
            (folder / f"file-{index}.txt").write_text(f"Project fact {index}", encoding="utf-8")
        page.locator("#folder-upload").set_input_files(str(folder))
        playwright.expect(page.locator("#folder-hint")).to_contain_text(
            "Đã nhập 60 tệp", timeout=15000
        )
        browser.close()


def test_real_msal_assets_initialize_without_untrusted_scripts(app_url):
    with playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page()
        failures = []
        page.on("pageerror", lambda error: failures.append(str(error)))
        page.goto(app_url)
        playwright.expect(page.locator("#mode")).to_be_hidden()
        assert page.evaluate("typeof msal.PublicClientApplication") == "function"
        assert not failures
        browser.close()


def test_cancelled_login_keeps_protected_data_locked(app_url):
    with playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page()
        page.route(
            "**/static/vendor/msal-browser.min.js",
            lambda route: route.fulfill(content_type="text/javascript", body=MSAL_FIXTURE),
        )
        page.goto(app_url)
        page.locator("[data-open-connection]").last.click()
        page.evaluate("loginFailure = true")
        page.locator("#microsoft-login").click()
        playwright.expect(page.locator("#connection-status")).to_contain_text("đã hủy")
        playwright.expect(page.locator("#microsoft-connection")).to_have_text("Kết nối Microsoft")
        playwright.expect(page.locator("#sessions button")).to_have_count(0)
        browser.close()


def test_pending_audio_close_cannot_submit_old_recording_as_new_account(app_url):
    with playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(
            channel="chrome",
            headless=True,
            args=["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"],
        )
        page = browser.new_page()
        page.route(
            "**/static/vendor/msal-browser.min.js",
            lambda route: route.fulfill(content_type="text/javascript", body=MSAL_FIXTURE),
        )
        submitted_accounts = []

        def transcribe(route):
            submitted_accounts.append(route.request.headers.get("authorization"))
            route.fulfill(json={"text": "ALICE PRIVATE VOICE"})

        page.route("**/api/voice/transcribe", transcribe)
        page.goto(app_url)
        page.locator("[data-open-connection]").last.click()
        page.locator("#microsoft-login").click()
        playwright.expect(page.locator("#account-name")).to_have_text("alice@example.test")
        page.evaluate("config.speech_configured = true")
        page.locator("#mic").click()
        playwright.expect(page.locator("#mic")).to_have_attribute("aria-pressed", "true")
        page.evaluate("""() => {
            const context = recording.context;
            const close = context.close.bind(context);
            context.close = () => new Promise(resolve => {
                window.finishAudioClose = () => close().then(resolve);
            });
            window.pendingAudioStop = stopRecording();
        }""")
        page.locator("[data-open-connection]").last.click()
        page.locator("#microsoft-logout").click()
        page.wait_for_function("() => microsoftSession.account === null && !busy")
        page.evaluate("nextLoginUser = 'bob'")
        page.locator("#microsoft-login").click()
        playwright.expect(page.locator("#account-name")).to_have_text("bob@example.test")
        page.evaluate("""async () => {
            await finishAudioClose();
            await pendingAudioStop;
            await new Promise(requestAnimationFrame);
        }""")
        assert not submitted_accounts, "Alice's recording was submitted after account switching"
        playwright.expect(page.locator("#message")).to_have_value("")
        browser.close()


def test_pending_microphone_permission_stops_stream_after_account_switch(app_url):
    with playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(
            channel="chrome",
            headless=True,
            args=["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"],
        )
        page = browser.new_page()
        page.route(
            "**/static/vendor/msal-browser.min.js",
            lambda route: route.fulfill(content_type="text/javascript", body=MSAL_FIXTURE),
        )
        page.goto(app_url)
        page.locator("[data-open-connection]").last.click()
        page.locator("#microsoft-login").click()
        playwright.expect(page.locator("#account-name")).to_have_text("alice@example.test")
        page.evaluate("""() => {
            config.speech_configured = true;
            const getMedia = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
            navigator.mediaDevices.getUserMedia = constraints => new Promise(resolve => {
                window.grantPendingMicrophone = async () => {
                    const stream = await getMedia(constraints);
                    window.pendingMicrophoneTracks = stream.getTracks();
                    resolve(stream);
                };
            });
            window.pendingMicrophoneStart = document.getElementById('mic').onclick();
        }""")
        page.locator("[data-open-connection]").last.click()
        page.locator("#microsoft-logout").click()
        page.wait_for_function("() => microsoftSession.account === null && !busy")
        page.evaluate("nextLoginUser = 'bob'")
        page.locator("#microsoft-login").click()
        playwright.expect(page.locator("#account-name")).to_have_text("bob@example.test")
        page.evaluate("""async () => {
            await grantPendingMicrophone();
            await pendingMicrophoneStart;
        }""")
        assert page.evaluate("recording === null"), "Alice's pending capture restarted as Bob"
        assert page.evaluate("pendingMicrophoneTracks.every(track => track.readyState === 'ended')")
        playwright.expect(page.locator("#mic")).to_have_attribute("aria-pressed", "false")
        browser.close()
