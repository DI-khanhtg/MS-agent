from test_microsoft_login import MSAL_FIXTURE, playwright
from test_microsoft_login import app_url as app_url


def test_documents_attach_to_chat_with_selected_import_folder(app_url, document_files):
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
        files = [
            {"name": name, "mimeType": "application/octet-stream", "buffer": content}
            for name, content, _ in document_files.values()
        ]
        page.locator("#document-upload").set_input_files(files, timeout=3000)
        playwright.expect(page.locator("#folder-hint")).to_contain_text("Đã nhập 4 tệp")
        for name, _, _ in document_files.values():
            playwright.expect(page.locator("#selected-documents")).to_contain_text(name)
        assert ".pptx" in page.locator("#document-upload").get_attribute("accept")
        page.locator("#message").fill("Summarize the attached documents")
        with page.expect_request("**/api/chat/stream") as chat:
            page.locator("#message").press("Enter")
        assert chat.value.post_data_json["local_folder"]
        page.wait_for_function("() => !busy")
        page.locator("[data-open-connection]").last.click()
        page.locator("#microsoft-logout").click()
        playwright.expect(page.locator("#microsoft-connection")).to_have_attribute(
            "data-connected", "false"
        )
        playwright.expect(page.locator("#selected-documents")).to_be_hidden()
        assert page.locator("#document-upload").input_value() == ""
        browser.close()
