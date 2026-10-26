"""Optional headless local UI check: uv run --with playwright python scripts/smoke_ui.py."""

import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright


def smoke(url: str, channel: str, screenshot: Path) -> None:
    failures, external = [], []
    origin = urlsplit(url).netloc
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel=channel, headless=True)
        page = browser.new_page(viewport={"width": 1360, "height": 960})
        page.on("pageerror", lambda error: failures.append(str(error)))

        def local_only(route):
            if urlsplit(route.request.url).netloc != origin:
                external.append(route.request.url)
                route.abort()
            else:
                route.continue_()

        page.route("**/*", local_only)
        page.goto(url)
        expect(page.locator("#mode")).to_contain_text("DEMO LOCAL")
        screenshot.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(
            path=str(screenshot.with_stem(screenshot.stem + "-welcome")), full_page=True
        )
        page.locator(".starter-card").first.click()
        expect(page.locator("#message")).not_to_have_value("")
        expect(page.locator("#messages .bubble")).to_have_count(0)
        page.locator("[data-view='documents']").first.click()
        expect(page.locator("#documents-view")).to_be_visible()
        page.locator("[data-view='approvals']").click()
        expect(page.locator("#actions")).to_contain_text("Chưa kết nối Microsoft")
        page.locator("[data-open-connection]").last.click()
        expect(page.locator("#connection-dialog")).to_be_visible()
        page.keyboard.press("Escape")
        expect(page.locator("#connection-dialog")).not_to_be_visible()
        page.locator("#new").click()
        page.locator("#proposal").click()
        expect(page.locator("#status")).to_contain_text("Hoàn tất", timeout=30000)
        expect(page.locator("#recent-artifacts button").first).to_be_visible()
        page.locator("[data-view='documents']").first.click()
        expect(page.locator("#artifacts button").first).to_be_visible()
        with page.expect_download() as download:
            page.locator("#artifacts button").first.click()
        assert download.value.suggested_filename.endswith(".docx")
        page.locator("#new").click()
        expect(page.locator("#messages .bubble")).to_have_count(0)
        page.locator("#sessions button").first.click()
        expect(page.locator("#messages .bubble")).to_have_count(2)
        page.locator("#mic").click()
        expect(page.locator("#status")).to_contain_text("Cần cấu hình Azure Speech")
        page.screenshot(path=str(screenshot), full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        page.locator("#menu-toggle").click()
        expect(page.locator("#menu-toggle")).to_have_attribute("aria-expanded", "true")
        page.locator("#new").click()
        expect(page.locator("#menu-toggle")).to_have_attribute("aria-expanded", "false")
        expect(page.locator("#sidebar")).not_to_be_in_viewport()
        expect(page.locator("#welcome")).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.screenshot(path=str(screenshot.with_stem(screenshot.stem + "-mobile")), full_page=True)
        for width, height in ((768, 1024), (1024, 768), (1280, 720)):
            page.set_viewport_size({"width": width, "height": height})
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")

        # Exercise enterprise UI with local fixtures; no real Microsoft writes or LLM calls.
        enterprise = browser.new_page(viewport={"width": 1360, "height": 960})
        enterprise.on("pageerror", lambda error: failures.append(str(error)))
        enterprise.route("**/*", local_only)
        action = {
            "action_id": "fixture-action",
            "action_type": "send_email",
            "status": "pending_approval",
            "payload": {
                "to": ["review@example.test"],
                "subject": "Project update",
                "body": "Ready for review",
                "cc": [],
            },
        }
        approvals = []

        def fixture_api(route):
            path = urlsplit(route.request.url).path
            if route.request.headers.get("authorization") == "Bearer invalid-fixture-token":
                route.fulfill(status=401, json={"detail": "Token không hợp lệ"})
                return
            if path == "/api/auth/config":
                route.fulfill(json={"enabled": False, "configured": False})
            elif path == "/api/config":
                route.fulfill(
                    json={
                        "runtime_mode": "gemini",
                        "auth_enabled": True,
                        "tool_groups": ["fabric_read", "workiq_write", "artifact_generation"],
                        "speech_configured": False,
                    }
                )
            elif path in {"/api/artifacts", "/api/sessions"}:
                route.fulfill(json=[])
            elif path == "/api/actions":
                route.fulfill(json=[action])
            elif path == "/api/approvals/fixture-action/approve":
                approvals.append(path)
                action["status"] = "succeeded"
                route.fulfill(json=action)
            elif path == "/api/chat/stream":
                data = json.dumps({"text": "Partial answer"})
                route.fulfill(
                    content_type="text/event-stream", body=f"event: delta\ndata: {data}\n\n"
                )
            else:
                raise AssertionError(f"Unexpected fixture request: {path}")

        enterprise.route("**/api/**", fixture_api)
        enterprise.goto(url)
        expect(enterprise.locator("#mode")).to_be_hidden()
        expect(enterprise.locator("#folder")).to_be_disabled()
        expect(enterprise.locator("#starter-title-2")).to_have_text("Phân tích số liệu")
        enterprise.locator("[data-view='approvals']").click()
        enterprise.locator(".action-card summary").click()
        expect(enterprise.locator(".action-payload")).to_contain_text("review@example.test")
        enterprise.get_by_role("button", name="Duyệt và thực hiện trên Microsoft 365").click()
        expect(enterprise.locator(".action-status")).to_have_text("Hoàn tất")
        assert len(approvals) == 1
        enterprise.locator("[data-view='chat']").click()
        enterprise.locator("#message").fill("Test interrupted stream")
        enterprise.locator("#message").press("Enter")
        expect(enterprise.locator("#status")).to_contain_text("Kết nối bị gián đoạn")
        expect(enterprise.locator("#send")).to_be_enabled()
        expect(enterprise.locator(".bubble.agent")).to_contain_text("Partial answer")
        enterprise.locator("[data-open-connection]").last.click()
        enterprise.locator("#manual-connection summary").click()
        enterprise.locator("#token").fill("invalid-fixture-token")
        enterprise.locator("#connect").click()
        expect(enterprise.locator("#connection-status")).to_contain_text("Token không hợp lệ")
        expect(enterprise.locator("#connection-dialog")).to_be_visible()
        expect(enterprise.locator("#actions .action-card")).to_have_count(0)
        enterprise.close()
        assert not failures, failures
        assert not external, external
        browser.close()
    print(
        "UI passed: navigation, suggestions, connection dialog, streaming, download, history, "
        "disconnected voice, responsive layouts, approval fixtures and error states; "
        "zero external requests"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--channel", default="chrome")
    parser.add_argument("--screenshot", type=Path, default=Path("data/ui-smoke.png"))
    args = parser.parse_args()
    smoke(args.url, args.channel, args.screenshot)
