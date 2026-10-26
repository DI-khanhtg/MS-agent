"use strict";

// Model and retrieved text remain untrusted even when rendered as Markdown.
const ChatMarkdown = (() => {
    const states = new WeakMap();
    const escapeHTML = text => String(text).replace(/[&<>"']/g, char => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    })[char]);
    const parser = new marked.Marked({
        gfm: true,
        breaks: true,
        renderer: {
            html: ({text}) => escapeHTML(text),
            image: ({text}) => escapeHTML(text),
        },
    });

    function render(node, source) {
        const fragment = DOMPurify.sanitize(parser.parse(source), {
            RETURN_DOM_FRAGMENT: true,
            ALLOWED_TAGS: ["p", "br", "strong", "b", "em", "i", "del", "s", "a", "ul", "ol", "li",
                "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "code", "hr",
                "table", "thead", "tbody", "tr", "th", "td"],
            ALLOWED_ATTR: ["href", "title", "class", "start", "align"],
            ALLOW_DATA_ATTR: false,
            ALLOW_ARIA_ATTR: false,
            SANITIZE_NAMED_PROPS: true,
        });
        for (const anchor of fragment.querySelectorAll("a[href]")) {
            try {
                const url = new URL(anchor.getAttribute("href"), location.href);
                if (!["http:", "https:", "mailto:"].includes(url.protocol)) {
                    anchor.removeAttribute("href");
                    continue;
                }
                anchor.rel = "noopener noreferrer";
                if (url.origin !== location.origin) anchor.target = "_blank";
            } catch {
                anchor.removeAttribute("href");
            }
        }
        for (const table of Array.from(fragment.querySelectorAll("table"))) {
            const wrapper = document.createElement("div");
            wrapper.className = "markdown-table";
            wrapper.setAttribute("role", "region");
            wrapper.setAttribute("aria-label", "Bảng dữ liệu, có thể cuộn ngang");
            wrapper.tabIndex = 0;
            table.replaceWith(wrapper);
            wrapper.append(table);
        }
        for (const pre of fragment.querySelectorAll("pre")) {
            pre.tabIndex = 0;
            pre.setAttribute("aria-label", "Khối mã, có thể cuộn ngang");
        }
        node.replaceChildren(fragment);
    }

    function set(node, text) {
        const state = states.get(node);
        if (state?.frame) cancelAnimationFrame(state.frame);
        states.set(node, {text: String(text || ""), frame: null});
        render(node, states.get(node).text);
    }

    function flush(node) {
        const state = states.get(node);
        if (!state) return;
        if (state.frame) cancelAnimationFrame(state.frame);
        state.frame = null;
        if (node.isConnected) render(node, state.text);
    }

    function append(node, text) {
        if (!states.has(node)) states.set(node, {text: "", frame: null});
        const state = states.get(node);
        state.text += String(text || "");
        if (state.frame === null) state.frame = requestAnimationFrame(() => {
            state.frame = null;
            if (node.isConnected) render(node, state.text);
        });
    }

    return {set, append, flush, text: node => states.get(node)?.text || ""};
})();
