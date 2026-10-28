"use strict";
const $ = id => document.getElementById(id);
let sessionId = null,
    config = {},
    recording = null,
    busy = false,
    currentView = "chat";
const microsoftSession = new MicrosoftSession();
let workspaceGeneration = 0;
let chatNavigation = 0,
    chatLoading = false,
    chatUnavailable = false,
    pendingChatNavigation = false;
let importedFolder = null;
const sharedDeployment = () => (microsoftSession.settings?.deployment_mode || config.deployment_mode) === "shared";
const uploadsEnabled = () => config.tool_groups?.includes("local_read")
    && config.auth_enabled
    && (config.document_uploads_enabled ?? !sharedDeployment());
const operatorHelp = "Không thể đăng nhập lúc này. Hãy thử lại hoặc liên hệ quản trị viên dịch vụ; tổ chức của bạn có thể cần phê duyệt quyền truy cập.";
function loginError(error) {
    if (!sharedDeployment() || ["user_cancelled", "popup_window_error"].includes(error?.errorCode)) return microsoftLoginError(error);
    return operatorHelp;
}
function configureDeployment() {
    const shared = sharedDeployment();
    const localSettings = microsoftSession.settings && !shared;
    $("manual-connection").hidden = !localSettings;
    $("login-setup").hidden = !localSettings;
    $("folder-source").hidden = !localSettings;
    $("connection-description").textContent = shared
        ? "Dùng tài khoản Microsoft cơ quan hoặc trường học để truy cập những dữ liệu bạn được phép đọc. Tổ chức của bạn có thể cần quản trị viên phê duyệt."
        : "Đăng nhập để đọc OneDrive, SharePoint và Microsoft 365 theo quyền tài khoản của bạn.";
    if (shared) {
        $("composer-context").textContent = "Không gian làm việc của bạn";
        $("source-description").textContent = "Chọn định dạng để tạo bản nháp từ nội dung hội thoại và tài liệu của bạn.";
    }
}
const headers = async () => {
    try {
        const token = (!sharedDeployment() && $("token").value.trim()) || await microsoftSession.accessToken();
        return token ? {Authorization: "Bearer " + token} : {};
    } catch (error) {
        setMicrosoftConnection(false);
        throw error;
    }
};

function status(text, error = false) {
    $("status").textContent = text;
    $("status").className = error ? "error" : "";
}
function element(tag, className, text) {
    const node = document.createElement(tag);
    node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
}
function icon(name) {
    const node = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    node.setAttribute("class", "icon");
    node.setAttribute("aria-hidden", "true");
    const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    use.setAttribute("href", "#i-" + name);
    node.append(use);
    return node;
}
function emptyState(target, title, description, symbol = "file") {
    const state = element("div", "empty-state");
    state.append(icon(symbol), element("strong", "", title), element("p", "", description));
    target.replaceChildren(state);
}
function toggleSidebar(open) {
    const mobile = window.matchMedia("(max-width: 700px)").matches;
    const expanded = mobile && open;
    $("sidebar").classList.toggle("open", expanded);
    $("sidebar").inert = mobile && !expanded;
    $("sidebar-overlay").hidden = !expanded;
    $("menu-toggle").setAttribute("aria-expanded", String(expanded));
    document.querySelector(".main").inert = expanded;
    if (expanded) $("new").focus();
}
function showView(view) {
    currentView = view;
    for (const name of ["chat", "documents", "approvals"]) {
        $(name + "-view").hidden = name !== view;
    }
    const titles = { chat: "Trợ lý công việc", documents: "Tài liệu của bạn", approvals: "Cần phê duyệt" };
    $("view-title").textContent = titles[view];
    for (const button of document.querySelectorAll(".nav-item")) {
        const selected = button.dataset.view === view;
        button.classList.toggle("active", selected);
        if (selected) button.setAttribute("aria-current", "page");
        else button.removeAttribute("aria-current");
    }
    toggleSidebar(false);
}
function updateChatControls() {
    $("send").disabled = busy || chatLoading || chatUnavailable;
    $("proposal").disabled = busy || chatLoading || chatUnavailable;
}
function chatIdFromLocation() {
    const match = location.pathname.match(/^\/chat\/([^/]+)\/?$/);
    if (!match) return null;
    try {
        const id = decodeURIComponent(match[1]);
        return id.length <= 128 ? id : null;
    } catch {
        return null;
    }
}
function setChatLocation(id, replace = false) {
    const path = id ? "/chat/" + encodeURIComponent(id) : "/";
    if (location.pathname === path) {
        if (replace && (location.search || location.hash)) history.replaceState({}, "", path);
        return;
    }
    history[replace ? "replaceState" : "pushState"]({}, "", path);
}
function resetConversation({updateUrl = true} = {}) {
    chatNavigation += 1;
    chatLoading = false;
    chatUnavailable = false;
    pendingChatNavigation = false;
    sessionId = null;
    $("messages").replaceChildren();
    $("welcome").hidden = false;
    for (const button of document.querySelectorAll("#sessions button")) button.classList.remove("selected");
    showView("chat");
    if (updateUrl) setChatLocation(null);
    updateChatControls();
}
async function openConversation(id, {updateUrl = true} = {}) {
    resetConversation({updateUrl: false});
    const generation = workspaceGeneration, navigation = chatNavigation;
    chatLoading = true;
    updateChatControls();
    status("Đang mở hội thoại…");
    try {
        const saved = await (await api("/api/sessions/" + encodeURIComponent(id))).json();
        if (generation !== workspaceGeneration || navigation !== chatNavigation) return false;
        sessionId = id;
        for (const turn of saved.messages) bubble(turn.role === "user" ? "user" : "agent", turn.text);
        for (const button of document.querySelectorAll("#sessions button")) {
            button.classList.toggle("selected", button.dataset.sessionId === id);
        }
        if (updateUrl) setChatLocation(id);
        status("Đã mở lại hội thoại.");
        return true;
    } catch (error) {
        if (generation !== workspaceGeneration || navigation !== chatNavigation) return false;
        chatUnavailable = true;
        status(error.status === 404
            ? "Không tìm thấy hội thoại hoặc bạn không có quyền truy cập. Hãy tạo hội thoại mới hoặc đăng nhập đúng tài khoản."
            : error.message, true);
        return false;
    } finally {
        if (generation === workspaceGeneration && navigation === chatNavigation) {
            chatLoading = false;
            updateChatControls();
        }
    }
}
async function restoreChatLocation() {
    const id = chatIdFromLocation();
    if (!id) {
        resetConversation({updateUrl: false});
        return;
    }
    if (microsoftSession.settings?.enabled && !microsoftSession.account && !$("token").value.trim()) {
        resetConversation({updateUrl: false});
        status("Đăng nhập Microsoft để mở hội thoại này.");
        return;
    }
    await openConversation(id, {updateUrl: false});
}
async function restorePendingChatLocation() {
    if (!pendingChatNavigation) return;
    pendingChatNavigation = false;
    await restoreChatLocation();
}
window.addEventListener("popstate", () => {
    chatNavigation += 1;
    if (busy) {
        pendingChatNavigation = true;
        return;
    }
    void restoreChatLocation();
});
function scrollToLatest() {
    const scroll = $("conversation-scroll");
    scroll.scrollTop = scroll.scrollHeight;
}
async function downloadArtifact(path, filename) {
    const generation = workspaceGeneration;
    const response = await api(path);
    const blob = await response.blob();
    if (generation !== workspaceGeneration) return;
    if (!filename) {
        const disposition = response.headers.get("content-disposition") || "";
        const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
        filename = encoded ? decodeURIComponent(encoded)
            : disposition.match(/filename="([^"]+)"/i)?.[1] || "document";
    }
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function renderFiles(target, files) {
    target.replaceChildren();
    if (!files.length) {
        emptyState(target, "Chưa có tài liệu", "Bản nháp bạn tạo sẽ xuất hiện tại đây.");
        return;
    }
    for (const item of files) {
        const button = element("button", "file-button");
        const format = item.filename.split(".").pop().toUpperCase();
        const info = element("span", "file-info");
        const size = item.size_bytes < 1024 ? item.size_bytes + " B" : Math.ceil(item.size_bytes / 1024) + " KB";
        info.append(element("span", "file-name", item.filename), element("span", "file-meta", format + " · " + size));
        button.title = "Tải xuống " + item.filename;
        button.append(element("span", "file-type", format), info, icon("download"));
        button.onclick = async () => {
            const generation = workspaceGeneration;
            button.disabled = true;
            try {
                await downloadArtifact(item.download_url, item.filename);
            } catch (error) {
                if (generation !== workspaceGeneration) return;
                status(error.message, true);
                if (currentView !== "chat") showView("chat");
            } finally {
                button.disabled = false;
            }
        };
        target.append(button);
    }
}
function configureWorkspace() {
    const local = config.tool_groups.includes("local_read") && (!sharedDeployment() || uploadsEnabled());
    configureDeployment();
    $("mode").hidden = config.runtime_mode !== "demo";
    $("mode").title = "Bản demo theo mẫu, không gọi LLM hay Microsoft IQ";
    $("workspace-kind").textContent = config.auth_enabled ? "Truy cập theo tài khoản Microsoft" : "Phiên làm việc local";
    $("composer-context").textContent = config.runtime_mode === "demo" ? "Dữ liệu demo" : "Trợ lý công việc";
    $("runtime-note").textContent = config.runtime_mode === "demo" ? "Bản demo theo mẫu · Chưa sử dụng AI hay dữ liệu Microsoft thật." : "AI có thể nhầm lẫn. Hãy kiểm tra nội dung và nguồn trước khi sử dụng.";
    $("speechStatus").textContent = config.speech_configured ? "Azure Speech đã cấu hình. Hỗ trợ WAV 16 kHz, mono, tối đa 60 giây." : "Chưa kết nối Azure Speech. Bạn vẫn có thể nhập yêu cầu bằng văn bản.";
    $("folder").disabled = !local;
    $("folder").readOnly = config.auth_enabled;
    $("choose-folder").hidden = !uploadsEnabled();
    $("attach-documents").hidden = !uploadsEnabled();
    $("upload-privacy").hidden = !(sharedDeployment() && uploadsEnabled());
    $("document-upload").accept = (config.document_extensions || []).join(",");
    if (!importedFolder) $("folder-hint").textContent = local && config.auth_enabled
        ? "Chọn thư mục trên máy để thêm tài liệu cho tài khoản này."
        : local ? "Tài liệu trong thư mục được dùng làm nguồn." : "Dùng nội dung hội thoại và nguồn dữ liệu đã kết nối.";
    $("welcome-description").textContent = local ? "Từ tài liệu đầu vào đến bản nháp sẵn sàng để bạn xem lại." : "Tổng hợp thông tin và tạo tài liệu từ những nguồn bạn có quyền truy cập.";
    const suggestions = local ? [
        ["Soạn một proposal", "Tạo bản nháp từ tài liệu dự án", "Đọc folder Project-X và tạo proposal DOCX, có nguồn và các giả định cần xác nhận."],
        ["Tổng hợp báo cáo", "Đưa thông tin về một tài liệu", "Đọc folder Project-X và tạo báo cáo proposal PDF, có trích dẫn nguồn."],
        ["Chuẩn bị slide", "Phác thảo nội dung trình bày", "Đọc folder Project-X và tạo proposal PPTX, trình bày nội dung chính và nguồn tham khảo."]
    ] : [
        ["Soạn một proposal", "Làm rõ mục tiêu và phạm vi", "Giúp tôi soạn một proposal. Hỏi tôi về mục tiêu, phạm vi và nguồn tài liệu cần sử dụng trước khi tạo bản nháp."],
        config.tool_groups.includes("fabric_read") ? ["Phân tích số liệu", "Hỏi dữ liệu đã kết nối qua Fabric", "Tổng hợp các KPI mới nhất từ Fabric, nêu rõ nguồn và thời điểm dữ liệu."] : ["Tổng hợp công việc", "Sắp xếp thông tin theo ưu tiên", "Giúp tôi tổng hợp công việc. Hỏi tôi về thông tin đầu vào trước khi bắt đầu."],
        ["Chuẩn bị slide", "Từ ý tưởng đến nội dung trình bày", "Giúp tôi chuẩn bị slide. Hỏi tôi về chủ đề, người xem và tài liệu nguồn trước khi tạo PPTX."]
    ];
    document.querySelectorAll(".starter-card").forEach((card, index) => {
        card.dataset.prompt = suggestions[index][2];
        $("starter-title-" + (index + 1)).textContent = suggestions[index][0];
        $("starter-description-" + (index + 1)).textContent = suggestions[index][1];
    });
}
async function api(path, options = {}) {
    if (new URL(path, location.origin).origin !== location.origin) {
        throw new Error("Chỉ gửi thông tin đăng nhập đến API cùng ứng dụng.");
    }
    const response = await fetch(path, {
        ...options,
        headers: {
            ...await headers(),
            ...options.headers
        }
    });
    if (!response.ok) {
        if (response.status === 401) setMicrosoftConnection(false);
        let data;
        try {
            data = await response.json();
        } catch {
            data = {
                detail: response.statusText
            };
        }
        const error = new Error(typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail));
        error.status = response.status;
        error.retryAfter = Math.max(1, Math.min(60, Number(response.headers.get("Retry-After")) || 60));
        throw error;
    }
    return response;
}

const EMPTY_RESPONSE_MESSAGE = "Agent đã kết thúc nhưng không trả về nội dung. Yêu cầu có thể chưa hoàn tất; hãy kiểm tra mục tài liệu trước khi thử lại.";

function bubble(role, text, pending = false) {
    $("welcome").hidden = true;
    const row = element("div", "message-row " + role + "-row");
    const avatar = element("span", "message-avatar");
    if (role === "user") avatar.textContent = "BẠN";
    else avatar.append(icon("mark"));
    avatar.setAttribute("aria-hidden", "true");
    const content = element("div", "message-content");
    const node = document.createElement("div");
    node.className = "bubble " + role;
    if (pending) {
        node.classList.add("is-pending");
        node.setAttribute("aria-busy", "true");
    }
    if (role === "user") node.textContent = text;
    else {
        ChatMarkdown.set(node, pending || String(text || "").trim() ? text : EMPTY_RESPONSE_MESSAGE);
        node.addEventListener("click", async event => {
            const link = event.target.closest?.("a[href]");
            if (!link) return;
            const url = new URL(link.href, location.href);
            if (url.origin !== location.origin || !/^\/api\/artifacts\/[\w-]+\/download$/.test(url.pathname)) return;
            event.preventDefault();
            const generation = workspaceGeneration;
            try {
                await downloadArtifact(url.pathname + url.search);
            } catch (error) {
                if (generation === workspaceGeneration) status(error.message, true);
            }
        });
    }
    content.append(element("div", "message-author", role === "user" ? "Bạn" : "Enterprise Agent"), node);
    row.append(avatar, content);
    $("messages").append(row);
    scrollToLatest();
    return node;
}
async function refresh() {
    const generation = workspaceGeneration;
    try {
        config = await (await api("/api/config")).json();
        if (generation !== workspaceGeneration) return false;
        configureWorkspace();
        const [artifacts, sessions] = await Promise.all([
            api("/api/artifacts").then(response => response.json()),
            api("/api/sessions").then(response => response.json())
        ]);
        if (generation !== workspaceGeneration) return false;
        renderFiles($("artifacts"), artifacts);
        renderFiles($("recent-artifacts"), artifacts.slice(0, 3));
        $("document-count").textContent = artifacts.length;
        $("library-count").textContent = artifacts.length + " tài liệu";
        $("sessions").replaceChildren();
        if (!sessions.length) $("sessions").append(element("p", "empty-label", "Những cuộc hội thoại của bạn sẽ được lưu tại đây."));
        sessions.sort((a, b) => b.updated_at.localeCompare(a.updated_at));
        for (const item of sessions) {
            const b = element("button", item.session_id === sessionId ? "selected" : "");
            b.dataset.sessionId = item.session_id;
            const date = new Date(item.updated_at).toLocaleDateString("vi-VN", { day: "2-digit", month: "2-digit" });
            b.append(icon("chat"), element("span", "session-title", "Hội thoại " + date), element("span", "session-turns", item.turns + " lượt"));
            b.onclick = async () => {
                if (busy) return;
                await openConversation(item.session_id);
            };
            $("sessions").append(b);
        }
        if (config.auth_enabled) {
            const actions = await (await api("/api/actions")).json();
            if (generation !== workspaceGeneration) return false;
            renderActions(actions);
        } else {
            emptyState($("actions"), "Chưa kết nối Microsoft", "Kết nối tài khoản để xem và phê duyệt thao tác trên Microsoft 365.", "lock");
            $("approval-count").textContent = "0";
        }
        return true;
    } catch (e) {
        $("mode").textContent = "Chưa kết nối";
        status(e.message, true);
        return false;
    }
}
async function send(message) {
    if (busy || chatLoading || chatUnavailable) return;
    if (microsoftSession.settings?.enabled && !microsoftSession.account && !$("token").value.trim()) {
        $("connection-dialog").showModal();
        connectionStatus("Đăng nhập Microsoft trước khi gửi yêu cầu.", true);
        return;
    }
    showView("chat");
    busy = true;
    $("send").disabled = true;
    $("proposal").disabled = true;
    bubble("user", message);
    const output = bubble("agent", "", true);
    status("Agent đang xử lý…");
    try {
        const response = await api("/api/chat/stream", {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({
                message,
                session_id: sessionId,
                local_folder: importedFolder,
            })
        });
        const reader = response.body.getReader(),
            decoder = new TextDecoder();
        let buffer = "",
            failed = false,
            completed = false;
        while (true) {
            const {
                value,
                done
            } = await reader.read();
            buffer += decoder.decode(value || new Uint8Array(), {
                stream: !done
            });
            let index;
            while ((index = buffer.indexOf("\n\n")) >= 0) {
                const block = buffer.slice(0, index);
                buffer = buffer.slice(index + 2);
                let event = "",
                    data = "";
                for (const line of block.split("\n")) {
                    if (line.startsWith("event: ")) event = line.slice(7);
                    if (line.startsWith("data: ")) data += line.slice(6);
                }
                if (!data) continue;
                const payload = JSON.parse(data);
                if (event === "metadata") {
                    sessionId = payload.session_id;
                    if (!pendingChatNavigation) setChatLocation(sessionId);
                }
                if (event === "delta") ChatMarkdown.append(output, payload.text);
                if (event === "done") completed = true;
                if (event === "error") {
                    failed = true;
                    ChatMarkdown.append(output, (ChatMarkdown.text(output) ? "\n\n" : "") + payload.detail);
                    status(payload.detail, true);
                }
            }
            scrollToLatest();
            if (done) break;
        }
        if (!completed && !failed) throw new Error("Kết nối bị gián đoạn. Vui lòng thử lại yêu cầu.");
        if (!failed && !ChatMarkdown.text(output).trim()) throw new Error(EMPTY_RESPONSE_MESSAGE);
        if (!failed) status("Hoàn tất. Bạn có thể xem tài liệu ở mục Tài liệu của bạn.");
        await refresh();
    } catch (e) {
        ChatMarkdown.append(output, (ChatMarkdown.text(output) ? "\n\n" : "") + e.message);
        status(e.message, true);
    } finally {
        output.classList.remove("is-pending");
        output.setAttribute("aria-busy", "false");
        ChatMarkdown.flush(output);
        scrollToLatest();
        busy = false;
        updateChatControls();
        await restorePendingChatLocation();
    }
}
$("chat").onsubmit = e => {
    e.preventDefault();
    if (busy || chatLoading || chatUnavailable) return;
    const text = $("message").value.trim();
    if (text) {
        $("message").value = "";
        send(text);
    }
};
$("message").onkeydown = event => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        $("chat").requestSubmit();
    }
};
$("proposal").onclick = () => {
    if (config.tool_groups?.includes("local_read") && (!sharedDeployment() || importedFolder)) {
        const folder = $("folder").value.trim();
        if (!folder) {
            status("Nhập thư mục nguồn trước khi tạo bản nháp.", true);
            $("folder").focus();
            return;
        }
        send("Đọc tất cả requirements và data trong "
            + (importedFolder ? "thư mục đã chọn " : "folder ") + folder
            + ", tạo proposal " + $("format").value + " hoàn chỉnh, có nguồn và các giả định cần xác nhận.");
    } else {
        send("Dựa trên nội dung và các nguồn đã xác minh trong hội thoại, tạo bản nháp " + $("format").value + ". Nếu thiếu thông tin, hãy hỏi lại trước khi tạo tài liệu.");
    }
};
$("new").onclick = () => {
    if (busy) return;
    resetConversation();
    $("message").value = "";
    $("message").focus();
    status("Đã bắt đầu hội thoại mới.");
};
$("connect").onclick = async () => {
    if (busy) return;
    $("connect").disabled = true;
    clearWorkspace();
    // Manual API tokens remain an advanced option, separate from the MSAL account.
    if (microsoftSession.account) await microsoftSession.logout();
    $("connection-status").textContent = "Đang kiểm tra kết nối…";
    if (await refresh()) {
        setMicrosoftConnection(Boolean(config.auth_enabled));
        $("connection-dialog").close();
        status(config.auth_enabled ? "Đã cập nhật kết nối Microsoft." : "Đã cập nhật phiên local. Kết nối Microsoft chưa được bật trên máy chủ.");
        await restoreChatLocation();
    } else {
        $("connection-status").textContent = $("status").textContent;
        $("connection-status").className = "dialog-status error";
    }
    $("connect").disabled = false;
};
for (const button of document.querySelectorAll("[data-view]")) button.onclick = () => showView(button.dataset.view);
for (const button of document.querySelectorAll("[data-open-connection]")) button.onclick = () => {
    if (busy) return;
    $("connection-status").textContent = "";
    $("connection-status").className = "dialog-status";
    if (sharedDeployment() && !microsoftSession.settings?.configured) connectionStatus(operatorHelp, true);
    $("connection-dialog").showModal();
};
$("close-connection").onclick = () => $("connection-dialog").close();
$("connection-dialog").addEventListener("click", event => {
    if (event.target === $("connection-dialog")) {
        const box = event.target.getBoundingClientRect();
        if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) event.target.close();
    }
});
document.querySelectorAll(".starter-card").forEach(card => {
    card.onclick = () => {
        if (busy) return;
        if (uploadsEnabled() && !importedFolder) {
            $("choose-folder").click();
            status("Chọn thư mục nguồn trước khi dùng gợi ý này.");
            return;
        }
        $("message").value = importedFolder
            ? card.dataset.prompt.replace("folder Project-X", "thư mục đã chọn " + $("folder").value)
            : card.dataset.prompt;
        $("message").focus();
    };
});
$("create-from-library").onclick = () => {
    showView("chat");
    document.querySelector(".create-document").scrollIntoView({ block: "center" });
    (sharedDeployment() || $("folder").disabled ? $("format") : $("folder")).focus();
};
$("menu-toggle").onclick = () => toggleSidebar(!$("sidebar").classList.contains("open"));
$("sidebar-overlay").onclick = () => { toggleSidebar(false); $("menu-toggle").focus(); };
document.addEventListener("keydown", event => {
    if (event.key === "Escape" && $("sidebar").classList.contains("open")) {
        toggleSidebar(false);
        $("menu-toggle").focus();
    }
});
window.matchMedia("(max-width: 700px)").addEventListener("change", () => toggleSidebar(false));
toggleSidebar(false);

function renderActions(actions) {
    $("actions").replaceChildren();
    $("approval-count").textContent = actions.filter(item => item.status === "pending_approval").length;
    if (!actions.length) {
        emptyState($("actions"), "Không có thao tác chờ duyệt", "Khi bạn yêu cầu soạn email hoặc tạo lịch hẹn, bản nháp sẽ xuất hiện tại đây.", "check");
        return;
    }
    for (const item of actions) {
        const card = document.createElement("details"),
            title = document.createElement("summary"),
            payload = element("dl", "action-payload");
        card.className = "action-card";
        const names = { send_email: "Gửi email", create_calendar_event: "Tạo lịch hẹn", post_teams_message: "Gửi tin nhắn Teams", update_entity: "Cập nhật thông tin" };
        const states = { draft: "Bản nháp", pending_approval: "Chờ phê duyệt", approved: "Đã duyệt", executing: "Đang thực hiện", succeeded: "Hoàn tất", failed: "Thất bại", rejected: "Đã từ chối", expired: "Hết hạn" };
        title.append(document.createTextNode(names[item.action_type] || item.action_type), element("span", "action-status", states[item.status] || item.status));
        const fieldNames = { to: "Người nhận", cc: "Đồng gửi", subject: "Tiêu đề", body: "Nội dung", start: "Bắt đầu", end: "Kết thúc", time_zone: "Múi giờ", attendees: "Người tham dự", location: "Địa điểm", chat_id: "Cuộc trò chuyện", content: "Nội dung tin nhắn", entity_url: "Mục cần cập nhật", changes: "Thay đổi" };
        for (const [key, value] of Object.entries(item.payload)) {
            const display = Array.isArray(value) ? value.join(", ") : typeof value === "object" && value !== null ? JSON.stringify(value, null, 2) : String(value ?? "");
            payload.append(element("dt", "", fieldNames[key] || key), element("dd", "", display || "—"));
        }
        card.append(title, payload);
        const button = (label, path, body) => {
            const b = document.createElement("button");
            b.className = "button";
            b.textContent = label;
            b.onclick = async () => {
                if (busy) return;
                b.disabled = true;
                try {
                    await api(path, {
                        method: "POST",
                        headers: {
                            "Content-Type": "application/json"
                        },
                        body: JSON.stringify(body || {})
                    });
                    status("Đã cập nhật trạng thái thao tác.");
                    await refresh();
                } catch (e) {
                    status(e.message, true);
                    b.disabled = false;
                }
            };
            card.append(b);
        };
        if (item.status === "draft") button("Yêu cầu duyệt", "/api/actions/" + encodeURIComponent(item.action_id) + "/request-approval");
        if (item.status === "pending_approval") {
            const reason = document.createElement("input");
            reason.placeholder = "Lý do từ chối";
            reason.setAttribute("aria-label", "Lý do từ chối");
            card.append(reason);
            const reject = document.createElement("button");
            reject.className = "button";
            reject.textContent = "Từ chối";
            reject.onclick = async () => {
                if (busy) return;
                if (!reason.value.trim()) {
                    status("Nhập lý do từ chối.", true);
                    return;
                }
                reject.disabled = true;
                try {
                    await api("/api/approvals/" + encodeURIComponent(item.action_id) + "/reject", {
                        method: "POST",
                        headers: {
                            "Content-Type": "application/json"
                        },
                        body: JSON.stringify({
                            reason: reason.value
                        })
                    });
                    await refresh();
                } catch (e) {
                    status(e.message, true);
                    reject.disabled = false;
                }
            };
            card.append(reject);
            if (config.runtime_mode === "gemini" && config.tool_groups.includes("workiq_write")) button("Duyệt và thực hiện trên Microsoft 365", "/api/approvals/" + encodeURIComponent(item.action_id) + "/approve");
        }
        $("actions").append(card);
    }
}
async function transcribe(blob) {
    const generation = workspaceGeneration;
    const form = new FormData();
    form.append("audio", blob, "command.wav");
    form.append("language", "vi-VN");
    status("Đang chuyển giọng nói thành văn bản…");
    try {
        const result = await (await api("/api/voice/transcribe", {
            method: "POST",
            body: form
        })).json();
        if (generation !== workspaceGeneration) return;
        $("message").value = result.text;
        status("Đã nhận transcript. Bấm Gửi yêu cầu để chạy agent.");
    } catch (e) {
        if (generation !== workspaceGeneration) return;
        status(e.message, true);
    }
}
$("audio").onchange = () => {
    if ($("audio").files[0]) transcribe($("audio").files[0]);
};

function wavBlob(chunks, rate) {
    const length = chunks.reduce((n, c) => n + c.length, 0),
        samples = new Float32Array(length);
    let offset = 0;
    for (const chunk of chunks) {
        samples.set(chunk, offset);
        offset += chunk.length;
    }
    const count = Math.floor(length * 16000 / rate),
        buffer = new ArrayBuffer(44 + count * 2),
        view = new DataView(buffer);
    const word = (at, s) => {
        for (let i = 0; i < s.length; i++) view.setUint8(at + i, s.charCodeAt(i));
    };
    word(0, "RIFF");
    view.setUint32(4, 36 + count * 2, true);
    word(8, "WAVE");
    word(12, "fmt ");
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, 1, true);
    view.setUint32(24, 16000, true);
    view.setUint32(28, 32000, true);
    view.setUint16(32, 2, true);
    view.setUint16(34, 16, true);
    word(36, "data");
    view.setUint32(40, count * 2, true);
    for (let i = 0; i < count; i++) {
        const sample = Math.max(-1, Math.min(1, samples[Math.floor(i * rate / 16000)]));
        view.setInt16(44 + i * 2, sample < 0 ? sample * 32768 : sample * 32767, true);
    }
    return new Blob([buffer], {
        type: "audio/wav"
    });
}
async function stopRecording() {
    if (!recording) return;
    const generation = workspaceGeneration;
    const r = recording;
    recording = null;
    clearTimeout(r.timer);
    r.processor.disconnect();
    r.source.disconnect();
    r.media.getTracks().forEach(t => t.stop());
    await r.context.close();
    if (generation !== workspaceGeneration) return;
    $("mic").replaceChildren(icon("mic"));
    $("mic").setAttribute("aria-label", "Thu âm yêu cầu");
    $("mic").setAttribute("aria-pressed", "false");
    transcribe(wavBlob(r.chunks, r.rate));
}
$("mic").onclick = async () => {
    const generation = workspaceGeneration;
    if (recording) {
        await stopRecording();
        return;
    }
    if (!config.speech_configured) {
        status("Cần cấu hình Azure Speech trước khi nhận giọng nói thật.", true);
        return;
    }
    let media;
    try {
        media = await navigator.mediaDevices.getUserMedia({
            audio: true
        });
        if (generation !== workspaceGeneration) {
            media.getTracks().forEach(track => track.stop());
            return;
        }
        const context = new AudioContext(),
            source = context.createMediaStreamSource(media),
            processor = context.createScriptProcessor(4096, 1, 1),
            chunks = [];
        processor.onaudioprocess = e => chunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
        source.connect(processor);
        processor.connect(context.destination);
        recording = {
            context,
            source,
            processor,
            chunks,
            media,
            rate: context.sampleRate,
            timer: setTimeout(stopRecording, 59000)
        };
        $("mic").replaceChildren(icon("close"));
        $("mic").setAttribute("aria-label", "Dừng thu âm");
        $("mic").setAttribute("aria-pressed", "true");
        status("Đang thu âm, tối đa 59 giây…");
    } catch (e) {
        if (media) media.getTracks().forEach(t => t.stop());
        if (generation !== workspaceGeneration) return;
        status(e.message, true);
    }
};
function connectionStatus(text, error = false) {
    $("connection-status").textContent = text;
    $("connection-status").className = "dialog-status" + (error ? " error" : "");
}

function setMicrosoftConnection(connected, username = "") {
    const button = $("microsoft-connection");
    const label = connected ? "Đã kết nối Microsoft" : "Kết nối Microsoft";
    button.dataset.connected = String(connected);
    button.title = connected ? username || "Tài khoản Microsoft đã được xác thực" : label;
    button.setAttribute("aria-label", connected && username ? label + ": " + username : label);
    $("connection-label").textContent = label;
    if (connected) {
        $("connection-symbol").replaceChildren(icon("connected"));
    } else {
        const mark = element("span", "microsoft-mark");
        for (let i = 0; i < 4; i++) mark.append(document.createElement("i"));
        $("connection-symbol").replaceChildren(mark);
    }
}

function clearWorkspace() {
    setMicrosoftConnection(false);
    workspaceGeneration += 1;
    importedFolder = null;
    if (recording) {
        clearTimeout(recording.timer);
        recording.processor.disconnect();
        recording.source.disconnect();
        recording.media.getTracks().forEach(track => track.stop());
        recording.context.close().catch(() => {});
        recording = null;
    }
    $("mic").replaceChildren(icon("mic"));
    $("mic").setAttribute("aria-label", "Thu âm yêu cầu");
    $("mic").setAttribute("aria-pressed", "false");
    resetConversation({updateUrl: false});
    for (const id of ["artifacts", "recent-artifacts", "sessions", "actions", "selected-documents"]) $(id).replaceChildren();
    $("selected-documents").hidden = true;
    $("document-count").textContent = "0";
    $("approval-count").textContent = "0";
    $("library-count").textContent = "0 tài liệu";
    $("folder").value = "";
    $("message").value = "";
    $("audio").value = "";
    $("folder-upload").value = "";
    $("document-upload").value = "";
    $("folder-hint").textContent = "Chọn thư mục trên máy để thêm tài liệu cho tài khoản này.";
}

function showMicrosoftAccount() {
    const account = microsoftSession.account;
    $("account-name").textContent = account?.username || "Chưa đăng nhập";
    $("microsoft-logout").hidden = !account;
    $("microsoft-login").textContent = account ? "Đăng nhập lại Microsoft" : "Đăng nhập Microsoft";
    $("token").disabled = Boolean(account);
    $("connect").disabled = Boolean(account);
}

function showSignedOut() {
    config = {};
    configureDeployment();
    $("mode").hidden = true;
    setMicrosoftConnection(false);
    $("workspace-kind").textContent = "Đăng nhập Microsoft để bắt đầu";
    $("folder").disabled = true;
    $("choose-folder").hidden = true;
    $("attach-documents").hidden = true;
    $("upload-privacy").hidden = true;
    if (sharedDeployment()) $("folder-hint").textContent = "Đăng nhập để dùng nguồn dữ liệu được dịch vụ cho phép.";
    status("Đăng nhập Microsoft để truy cập tài liệu của bạn.");
    showMicrosoftAccount();
}

$("microsoft-login").onclick = async () => {
    if (busy) return;
    busy = true;
    $("microsoft-login").disabled = true;
    connectionStatus("Đang mở đăng nhập Microsoft…");
    clearWorkspace();
    $("token").value = "";
    let connected = false;
    try {
        await microsoftSession.login();
        // Only display a connected account once our backend validates its access token.
        await api("/api/me");
        if (!await refresh()) throw new Error("Không tải được dữ liệu của tài khoản Microsoft.");
        showMicrosoftAccount();
        setMicrosoftConnection(true, microsoftSession.account.username);
        $("connection-dialog").close();
        status("Đã kết nối tài khoản Microsoft. Bạn có thể hỏi về OneDrive và SharePoint.");
        connected = true;
    } catch (error) {
        microsoftSession.account = null;
        clearWorkspace();
        showSignedOut();
        connectionStatus(loginError(error), true);
    } finally {
        busy = false;
        $("microsoft-login").disabled = false;
        updateChatControls();
    }
    if (connected) await restoreChatLocation();
};

$("microsoft-logout").onclick = async () => {
    if (busy) return;
    busy = true;
    $("token").value = "";
    clearWorkspace();
    connectionStatus("Đang đăng xuất Microsoft…");
    try {
        await microsoftSession.logout();
        connectionStatus("Đã đăng xuất Microsoft.");
    } catch {
        connectionStatus("Đã xóa phiên trong ứng dụng. Cửa sổ đăng xuất Microsoft chưa hoàn tất.");
    } finally {
        showSignedOut();
        busy = false;
        pendingChatNavigation = false;
        setChatLocation(null, true);
        updateChatControls();
    }
};

$("choose-folder").onclick = () => {
    if (busy) return;
    $("folder-upload").click();
};

function showSelectedDocuments(files) {
    const target = $("selected-documents");
    target.replaceChildren();
    target.hidden = files.length === 0;
    if (!files.length) return;
    const heading = element("div", "selected-documents-heading");
    const clear = element("button", "text-button", "Bỏ chọn nguồn");
    clear.type = "button";
    clear.onclick = () => {
        if (busy) return;
        importedFolder = null;
        $("folder").value = "";
        showSelectedDocuments([]);
        $("folder-hint").textContent = "Chọn tệp hoặc thư mục để dùng làm nguồn tài liệu.";
    };
    heading.append(element("strong", "", files.length + " tài liệu đang chọn"), clear);
    const list = element("div", "selected-file-list");
    for (const file of files) {
        const chip = element("span", "selected-file");
        const type = file.name.split(".").pop().toUpperCase();
        chip.title = file.webkitRelativePath || file.name;
        chip.append(element("span", "selected-file-type", type), element("span", "selected-file-name", file.name));
        list.append(chip);
    }
    target.append(heading, list);
}

async function importDocuments(selected, folderSelection = false) {
    if (busy) return;
    if (!uploadsEnabled()) return;
    if (!selected.length) return;
    const supported = new Set(config.document_extensions || []);
    const files = selected.filter(file => {
        const path = file.webkitRelativePath || file.name;
        return !path.split("/").some(part => part.startsWith("."))
            && supported.has("." + file.name.split(".").pop().toLowerCase());
    });
    if (!files.length) return status("Không có tệp hỗ trợ. Chọn PDF, DOCX, PPTX, XLSX, TXT, MD, CSV hoặc JSON.", true);
    if (files.some(file => file.size > 2_000_000)) return status("Mỗi tệp nhập vào cần nhỏ hơn hoặc bằng 2 MB.", true);
    const prefix = crypto.randomUUID();
    busy = true;
    $("choose-folder").disabled = true;
    $("document-upload").disabled = true;
    let uploaded = 0;
    const folderName = folderSelection ? files[0].webkitRelativePath?.split("/")[0] : null;
    const useImportedFolder = () => {
        importedFolder = prefix + (folderName ? "/" + folderName : "");
        $("folder").value = folderName || (uploaded === 1 ? files[0].name : uploaded + " tài liệu");
        showSelectedDocuments(files.slice(0, uploaded));
    };
    const batches = [];
    let batch = [], bytes = 0;
    for (const file of files) {
        if (batch.length && (bytes + file.size > 1_800_000 || batch.length >= 20)) {
            batches.push(batch);
            batch = [];
            bytes = 0;
        }
        batch.push(file);
        bytes += file.size;
    }
    if (batch.length) batches.push(batch);
    try {
        for (const batch of batches) {
            for (let attempt = 0; ; attempt++) {
                const form = new FormData();
                for (const file of batch) form.append("files", file, prefix + "/" + (file.webkitRelativePath || file.name));
                try {
                    const response = await api("/api/local-documents/upload", {method: "POST", body: form});
                    uploaded += (await response.json()).uploaded_files;
                    break;
                } catch (error) {
                    if (error.status !== 429 || attempt >= 1) throw error;
                    $("folder-hint").textContent = "Đã nhập " + uploaded + "/" + files.length
                        + " tệp. Chờ " + error.retryAfter + " giây để tiếp tục…";
                    await new Promise(resolve => setTimeout(resolve, error.retryAfter * 1000));
                }
            }
            $("folder-hint").textContent = "Đang nhập " + uploaded + "/" + files.length + " tệp…";
        }
        useImportedFolder();
        $("folder-hint").textContent = "Đã nhập " + uploaded + " tệp cho tài khoản của bạn. "
            + (selected.length > files.length ? "Bỏ qua " + (selected.length - files.length) + " tệp ẩn hoặc không hỗ trợ. " : "")
            + "Đây là bản sao tại thời điểm chọn tài liệu.";
        status(folderSelection
            ? "Đã thêm thư mục. Bạn có thể yêu cầu agent đọc và tổng hợp tài liệu."
            : "Đã đính kèm tài liệu. Hãy nhập yêu cầu để agent đọc và phân tích.");
    } catch (error) {
        if (uploaded) useImportedFolder();
        $("folder-hint").textContent = "Đã nhập " + uploaded + "/" + files.length + " tệp. Thư mục chưa được nhập đầy đủ.";
        status(error.message, true);
    } finally {
        busy = false;
        $("choose-folder").disabled = false;
        $("document-upload").disabled = false;
        $("folder-upload").value = "";
        $("document-upload").value = "";
        updateChatControls();
        await restorePendingChatLocation();
    }
}

$("folder-upload").onchange = () => importDocuments(Array.from($("folder-upload").files || []), true);
$("document-upload").onchange = () => importDocuments(Array.from($("document-upload").files || []));

async function initializeWorkspace() {
    $("microsoft-login").disabled = true;
    $("choose-folder").hidden = true;
    try {
        const settings = await microsoftSession.initialize();
        configureDeployment();
        if (!settings.enabled) {
            if (sharedDeployment()) {
                showSignedOut();
                connectionStatus(operatorHelp, true);
                status("Dịch vụ chưa sẵn sàng. Hãy liên hệ quản trị viên dịch vụ.", true);
                return;
            }
            $("login-setup").textContent = "Máy chủ đang dùng phiên local. Đặt AUTH_ENABLED=true để bật đăng nhập Microsoft.";
            await refresh();
            await restoreChatLocation();
            return;
        }
        clearWorkspace();
        showSignedOut();
        $("login-setup").textContent = sharedDeployment() ? "" : settings.configured
            ? "Redirect URI đăng ký trong Entra (SPA): " + settings.redirect_uri
            : "Cần cấu hình ENTRA_SPA_CLIENT_ID và các thiết lập Entra trên máy chủ.";
        if (settings.configured) {
            const origin = new URL(settings.redirect_uri).origin;
            if (origin !== location.origin) {
                $("login-origin").href = origin;
                $("login-origin").hidden = false;
            }
            $("microsoft-login").disabled = false;
        } else if (sharedDeployment()) {
            connectionStatus(operatorHelp, true);
            status("Dịch vụ chưa sẵn sàng. Hãy liên hệ quản trị viên dịch vụ.", true);
            return;
        }
        status("Đăng nhập Microsoft để truy cập tài liệu của bạn.");
    } catch (error) {
        showSignedOut();
        connectionStatus(loginError(error), true);
        status("Không khởi tạo được đăng nhập Microsoft.", true);
    }
}

initializeWorkspace();
