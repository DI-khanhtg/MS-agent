import json

import pytest
from test_microsoft_login import MSAL_FIXTURE, playwright
from test_microsoft_login import app_url as app_url

MARKDOWN = """## Tài liệu OneDrive

1. **Documents** — thư mục dự án
2. **Báo cáo.pptx** — tệp PowerPoint

| Tài liệu | Trạng thái |
| --- | --- |
| Kế hoạch | Đã đọc |

> Kiểm tra nguồn trước khi chia sẻ.

```python
print("<hello>")
```

[Mở SharePoint](https://contoso.sharepoint.com/sites/project)
"""


def test_assistant_formats_markdown_and_user_text_stays_literal(app_url):
    with playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page()
        page.goto(app_url)
        playwright.expect(page.locator("#microsoft-login")).to_be_enabled()
        page.evaluate(
            "text => { bubble('agent', text); bubble('user', '**Literal user text**'); }", MARKDOWN
        )
        agent = page.locator(".bubble.agent")
        playwright.expect(agent.locator("h2")).to_have_text("Tài liệu OneDrive")
        playwright.expect(agent.locator("ol > li")).to_have_count(2)
        playwright.expect(agent.locator("strong").first).to_have_text("Documents")
        playwright.expect(agent.locator("table tbody td").first).to_have_text("Kế hoạch")
        playwright.expect(agent.locator("pre code")).to_have_text('print("<hello>")\n')
        link = agent.get_by_role("link", name="Mở SharePoint")
        assert link.get_attribute("href") == "https://contoso.sharepoint.com/sites/project"
        assert "noopener" in link.get_attribute("rel").split()
        assert "noreferrer" in link.get_attribute("rel").split()
        playwright.expect(page.locator(".bubble.user")).to_have_text("**Literal user text**")
        playwright.expect(page.locator(".bubble.user strong")).to_have_count(0)
        browser.close()


def test_model_html_links_and_images_cannot_execute_or_fetch(app_url):
    text = """<script>window.modelExecuted = true</script>
<img src="https://outside.example/pixel" onerror="window.modelExecuted=true">
<svg onload="window.modelExecuted=true"></svg>
<iframe src="https://outside.example/frame"></iframe>
<form action="https://outside.example/"><input name="token"></form>

[Bad](javascript:alert(1)) [Data](data:text/html,hello)
![Tracker](https://outside.example/tracker.png)
**Safe text**
"""
    with playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page()
        external = []
        page.route(
            "https://outside.example/**",
            lambda route: (external.append(route.request.url), route.abort()),
        )
        page.goto(app_url)
        playwright.expect(page.locator("#microsoft-login")).to_be_enabled()
        page.evaluate("text => bubble('agent', text)", text)
        agent = page.locator(".bubble.agent")
        playwright.expect(agent.locator("strong")).to_have_text("Safe text")
        playwright.expect(agent.locator("script,img,svg,iframe,form,input,style")).to_have_count(0)
        assert not agent.locator("[onerror],[onload],[style]").count()
        assert agent.locator('a[href^="javascript:"],a[href^="data:"]').count() == 0
        assert page.evaluate("window.modelExecuted === true") is False
        assert not external
        browser.close()


def test_markdown_artifact_link_downloads_with_current_account_token(app_url):
    with playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(accept_downloads=True)
        page.route(
            "**/static/vendor/msal-browser.min.js",
            lambda route: route.fulfill(content_type="text/javascript", body=MSAL_FIXTURE),
        )
        tokens = []

        def artifact(route):
            tokens.append(route.request.headers.get("authorization"))
            route.fulfill(
                body="Example report",
                headers={
                    "content-type": "text/markdown",
                    "content-disposition": 'attachment; filename="report.md"',
                },
            )

        page.route("**/api/artifacts/example/download", artifact)
        page.goto(app_url)
        playwright.expect(page.locator("#microsoft-login")).to_be_enabled()
        page.locator("[data-open-connection]").last.click()
        page.locator("#microsoft-login").click()
        playwright.expect(page.locator("#account-name")).to_have_text("alice@example.test")
        page.evaluate("bubble('agent', '[Download report](/api/artifacts/example/download)')")
        with page.expect_download() as download:
            page.get_by_role("link", name="Download report").click()
        assert download.value.suggested_filename == "report.md"
        assert tokens == ["Bearer token-alice"]
        browser.close()


@pytest.mark.parametrize("failed", [False, True])
def test_streamed_markdown_survives_completion_error_and_history(app_url, failed):
    with playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page()
        page.route(
            "**/static/vendor/msal-browser.min.js",
            lambda route: route.fulfill(content_type="text/javascript", body=MSAL_FIXTURE),
        )
        chunks = ["1. **Doc", "uments**", " — thư mục\n2. **Báo cáo** — tệp\n\n", "`code`"]
        body = 'event: metadata\ndata: {"session_id":"md-history"}\n\n'
        body += "".join(f"event: delta\ndata: {json.dumps({'text': text})}\n\n" for text in chunks)
        body += (
            "event: error\ndata: " + json.dumps({"detail": "Source unavailable"}) + "\n\n"
            if failed
            else 'event: done\ndata: {"session_id":"md-history"}\n\n'
        )
        page.route(
            "**/api/chat/stream",
            lambda route: route.fulfill(content_type="text/event-stream", body=body),
        )
        page.route(
            "**/api/sessions",
            lambda route: route.fulfill(
                json=[
                    {
                        "session_id": "md-history",
                        "updated_at": "2026-10-26T00:00:00Z",
                        "turns": 1,
                    }
                ]
            ),
        )
        page.route(
            "**/api/sessions/md-history",
            lambda route: route.fulfill(
                json={
                    "messages": [{"role": "agent", "text": "".join(chunks)}],
                }
            ),
        )
        page.goto(app_url)
        playwright.expect(page.locator("#microsoft-login")).to_be_enabled()
        page.locator("[data-open-connection]").last.click()
        page.locator("#microsoft-login").click()
        playwright.expect(page.locator("#account-name")).to_have_text("alice@example.test")
        page.locator("#message").fill("List my documents")
        page.locator("#message").press("Enter")
        agent = page.locator(".bubble.agent")
        playwright.expect(agent.locator("strong").first).to_have_text("Documents")
        playwright.expect(agent.locator("code")).to_have_text("code")
        page.wait_for_function("() => !busy")
        if failed:
            playwright.expect(agent).to_contain_text("Source unavailable")
        page.locator("#sessions button").first.click()
        playwright.expect(page.locator(".bubble.agent strong").first).to_have_text("Documents")
        playwright.expect(page.locator(".bubble.agent ol > li")).to_have_count(2)
        browser.close()


def test_wide_table_and_code_scroll_inside_mobile_message(app_url):
    with playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 390, "height": 844})
        page.goto(app_url)
        playwright.expect(page.locator("#microsoft-login")).to_be_enabled()
        text = MARKDOWN + "\n```text\n" + "very_long_value_" * 60 + "\n```\n"
        page.evaluate("text => bubble('agent', text)", text)
        playwright.expect(page.locator(".bubble.agent table")).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert page.locator(".bubble.agent pre").last.evaluate(
            "element => element.scrollWidth > element.clientWidth"
        )
        assert page.locator(".bubble.agent").evaluate(
            "element => parseFloat(getComputedStyle(element).fontSize) >= 14"
        )
        browser.close()


@pytest.mark.parametrize("streamed_text", ["", " \n\t"])
def test_empty_completed_stream_shows_failure_and_clears_waiting_indicator(app_url, streamed_text):
    with playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page()
        page.route(
            "**/static/vendor/msal-browser.min.js",
            lambda route: route.fulfill(content_type="text/javascript", body=MSAL_FIXTURE),
        )
        body = 'event: metadata\ndata: {"session_id":"empty-reply"}\n\n'
        if streamed_text:
            body += "event: delta\ndata: " + json.dumps({"text": streamed_text}) + "\n\n"
        body += 'event: done\ndata: {"session_id":"empty-reply"}\n\n'
        page.route(
            "**/api/chat/stream",
            lambda route: route.fulfill(content_type="text/event-stream", body=body),
        )
        page.goto(app_url)
        playwright.expect(page.locator("#microsoft-login")).to_be_enabled()
        page.locator("[data-open-connection]").last.click()
        page.locator("#microsoft-login").click()
        playwright.expect(page.locator("#account-name")).to_have_text("alice@example.test")
        page.locator("#message").fill("Read my document")
        page.locator("#message").press("Enter")
        page.wait_for_function("() => !busy")
        agent = page.locator(".bubble.agent")
        playwright.expect(agent).to_contain_text("không trả về nội dung")
        assert agent.evaluate("el => getComputedStyle(el, '::after').content") in ("none", "normal")
        playwright.expect(page.locator("#send")).to_be_enabled()
        browser.close()


def test_historical_empty_answer_does_not_show_a_waiting_indicator(app_url):
    with playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page()
        page.goto(app_url)
        playwright.expect(page.locator("#microsoft-login")).to_be_enabled()
        page.evaluate("bubble('agent', '')")
        agent = page.locator(".bubble.agent")
        playwright.expect(agent).to_contain_text("không trả về nội dung")
        assert agent.evaluate("el => getComputedStyle(el, '::after').content") in ("none", "normal")
        browser.close()
