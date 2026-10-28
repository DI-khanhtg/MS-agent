import re

from test_microsoft_login import MSAL_FIXTURE, playwright
from test_microsoft_login import app_url as app_url


def prepare(page):
    page.route(
        "**/static/vendor/msal-browser.min.js",
        lambda route: route.fulfill(content_type="text/javascript", body=MSAL_FIXTURE),
    )


def login(page, user="alice"):
    playwright.expect(page.locator("#microsoft-login")).to_be_enabled()
    page.evaluate("user => { window.nextLoginUser = user; }", user)
    page.locator("[data-open-connection]").last.click()
    page.locator("#microsoft-login").click()
    playwright.expect(page.locator("#account-name")).to_have_text(user + "@example.test")
    page.wait_for_function("() => !busy")


def create_saved_chat(page, text):
    return page.evaluate(
        """async message => {
        const response = await api('/api/chat', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({message})
        });
        return (await response.json()).session_id;
    }""",
        text,
    )


def test_new_chat_updates_url_and_history_navigation_restores_messages(app_url):
    with playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page()
        prepare(page)
        page.goto(app_url)
        login(page)
        page.locator("#message").fill("First conversation")
        page.locator("#message").press("Enter")
        page.wait_for_function("() => !busy")
        playwright.expect(page).to_have_url(re.compile(r"/chat/[^/]+$"))
        first_url = page.url
        first_id = first_url.rsplit("/", 1)[-1]
        page.locator("#new").click()
        playwright.expect(page).to_have_url(app_url + "/")
        page.locator("#message").fill("Second conversation")
        page.locator("#message").press("Enter")
        page.wait_for_function("() => !busy")
        playwright.expect(page).to_have_url(re.compile(r"/chat/[^/]+$"))
        second_url = page.url
        assert second_url != first_url
        page.locator(f'#sessions button[data-session-id="{first_id}"]').click()
        playwright.expect(page).to_have_url(first_url)
        playwright.expect(page.locator(".bubble.user")).to_have_text("First conversation")
        page.go_back()
        playwright.expect(page).to_have_url(second_url)
        playwright.expect(page.locator(".bubble.user")).to_have_text("Second conversation")
        page.go_forward()
        playwright.expect(page).to_have_url(first_url)
        playwright.expect(page.locator(".bubble.user")).to_have_text("First conversation")
        browser.close()


def test_direct_link_and_reload_restore_the_chat_after_authentication(app_url):
    with playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page()
        prepare(page)
        page.goto(app_url)
        login(page)
        session = create_saved_chat(page, "Saved deep link content")
        url = app_url + "/chat/" + session
        page.goto(url)
        playwright.expect(page.locator("#microsoft-login")).to_be_enabled()
        playwright.expect(page.locator(".bubble")).to_have_count(0)
        playwright.expect(page).to_have_url(url)
        login(page)
        playwright.expect(page.locator(".bubble.user")).to_have_text("Saved deep link content")
        page.reload()
        playwright.expect(page.locator("#microsoft-login")).to_be_enabled()
        playwright.expect(page).to_have_url(url)
        login(page)
        playwright.expect(page.locator(".bubble.user")).to_have_text("Saved deep link content")
        assert page.evaluate("Object.keys(localStorage).length") == 0
        browser.close()


def test_foreign_chat_link_reveals_no_history_and_new_chat_remains_available(app_url):
    with playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page()
        prepare(page)
        page.goto(app_url)
        login(page)
        session = create_saved_chat(page, "ALICE PRIVATE CHAT")
        page.goto(app_url + "/chat/" + session)
        login(page, "bob")
        playwright.expect(page.locator(".bubble")).to_have_count(0)
        playwright.expect(page.locator("#status")).to_contain_text("không có quyền")
        assert "ALICE PRIVATE CHAT" not in page.locator("body").inner_text()
        playwright.expect(page.locator("#send")).to_be_disabled()
        page.locator("#new").click()
        playwright.expect(page).to_have_url(app_url + "/")
        playwright.expect(page.locator("#send")).to_be_enabled()
        browser.close()


def test_late_history_response_does_not_replace_a_more_recent_selection(app_url):
    with playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page()
        prepare(page)
        page.goto(app_url)
        login(page)
        first = create_saved_chat(page, "Old selection")
        second = create_saved_chat(page, "Latest selection")
        page.evaluate("refresh()")
        pending = []
        page.evaluate(
            """id => {
            const original = Response.prototype.json;
            Response.prototype.json = async function() {
                const result = await original.call(this);
                if (this.url.endsWith('/api/sessions/' + id)) window.lateRouteParsed = true;
                return result;
            };
        }""",
            first,
        )
        page.route("**/api/sessions/" + first, lambda route: pending.append(route))
        page.locator(f'#sessions button[data-session-id="{first}"]').click()
        page.wait_for_timeout(100)
        page.locator(f'#sessions button[data-session-id="{second}"]').click()
        playwright.expect(page.locator(".bubble.user")).to_have_text("Latest selection")
        assert pending
        pending[0].fulfill(json={"messages": [{"role": "user", "text": "Old selection"}]})
        page.wait_for_function("window.lateRouteParsed === true")
        playwright.expect(page).to_have_url(app_url + "/chat/" + second)
        playwright.expect(page.locator(".bubble.user")).to_have_text("Latest selection")
        browser.close()


def test_back_during_stream_does_not_get_overwritten_by_late_metadata(app_url):
    with playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page()
        prepare(page)
        page.goto(app_url)
        login(page)
        first = create_saved_chat(page, "First saved chat")
        second = create_saved_chat(page, "Second saved chat")
        page.evaluate("refresh()")
        page.locator(f'#sessions button[data-session-id="{first}"]').click()
        playwright.expect(page).to_have_url(app_url + "/chat/" + first)
        page.locator(f'#sessions button[data-session-id="{second}"]').click()
        playwright.expect(page).to_have_url(app_url + "/chat/" + second)
        page.evaluate(
            """session => {
            const original = window.fetch;
            window.fetch = (input, options) => {
                if (String(input).endsWith('/api/chat/stream')) {
                    return Promise.resolve(new Response(new ReadableStream({start(controller) {
                        window.finishRouteStream = () => {
                            const body = 'event: metadata\\ndata: '
                                + JSON.stringify({session_id: session})
                                + '\\n\\nevent: delta\\ndata: {"text":"Finished"}\\n\\n'
                                + 'event: done\\ndata: {}\\n\\n';
                            controller.enqueue(new TextEncoder().encode(body)); controller.close();
                        };
                    }}), {headers: {'Content-Type': 'text/event-stream'}}));
                }
                return original(input, options);
            };
        }""",
            second,
        )
        page.locator("#message").fill("Another request")
        page.locator("#message").press("Enter")
        page.wait_for_function("() => typeof window.finishRouteStream === 'function'")
        page.go_back()
        playwright.expect(page).to_have_url(app_url + "/chat/" + first)
        page.evaluate("window.finishRouteStream()")
        page.wait_for_function("() => !busy")
        playwright.expect(page).to_have_url(app_url + "/chat/" + first)
        playwright.expect(page.locator(".bubble.user")).to_have_text("First saved chat")
        browser.close()


def test_back_during_upload_restores_the_chat_before_the_next_message(app_url):
    with playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page()
        prepare(page)
        page.goto(app_url)
        login(page)
        first = create_saved_chat(page, "First upload chat")
        second = create_saved_chat(page, "Second upload chat")
        page.evaluate("refresh()")
        page.locator(f'#sessions button[data-session-id="{first}"]').click()
        playwright.expect(page).to_have_url(app_url + "/chat/" + first)
        page.locator(f'#sessions button[data-session-id="{second}"]').click()
        playwright.expect(page).to_have_url(app_url + "/chat/" + second)
        pending = []
        page.route("**/api/local-documents/upload", lambda route: pending.append(route))
        with page.expect_request("**/api/local-documents/upload"):
            page.locator("#document-upload").set_input_files(
                {
                    "name": "route-test.txt",
                    "mimeType": "text/plain",
                    "buffer": b"Synthetic upload",
                }
            )
        page.go_back()
        playwright.expect(page).to_have_url(app_url + "/chat/" + first)
        assert pending
        pending[0].fulfill(json={"uploaded_files": 1})
        page.wait_for_function("() => !busy")
        playwright.expect(page.locator(".bubble.user")).to_have_text("First upload chat")
        assert page.evaluate("sessionId") == first
        browser.close()


def test_back_invalidates_delayed_history_even_while_upload_is_busy(app_url):
    with playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page()
        prepare(page)
        page.goto(app_url)
        login(page)
        first = create_saved_chat(page, "First race chat")
        second = create_saved_chat(page, "Second race chat")
        page.evaluate("refresh()")
        page.locator(f'#sessions button[data-session-id="{first}"]').click()
        playwright.expect(page).to_have_url(app_url + "/chat/" + first)
        history_requests, uploads = [], []
        page.route("**/api/sessions/" + second, lambda route: history_requests.append(route))
        page.route("**/api/local-documents/upload", lambda route: uploads.append(route))
        page.evaluate(
            """id => {
            const original = Response.prototype.json;
            Response.prototype.json = async function() {
                const result = await original.call(this);
                if (this.url.endsWith('/api/sessions/' + id)) window.busyHistoryParsed = true;
                return result;
            };
        }""",
            second,
        )
        with page.expect_request("**/api/sessions/" + second):
            page.locator(f'#sessions button[data-session-id="{second}"]').click()
        with page.expect_request("**/api/local-documents/upload"):
            page.locator("#document-upload").set_input_files(
                {
                    "name": "race.txt",
                    "mimeType": "text/plain",
                    "buffer": b"Synthetic upload",
                }
            )
        page.go_back()
        playwright.expect(page).to_have_url(app_url + "/")
        history_requests[0].fulfill(
            json={"messages": [{"role": "user", "text": "Second race chat"}]}
        )
        page.wait_for_function("window.busyHistoryParsed === true")
        playwright.expect(page).to_have_url(app_url + "/")
        uploads[0].fulfill(json={"uploaded_files": 1})
        page.wait_for_function("() => !busy")
        playwright.expect(page.locator(".bubble")).to_have_count(0)
        assert page.evaluate("sessionId") is None
        browser.close()
