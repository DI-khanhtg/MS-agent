"use strict";

class MicrosoftSession {
    constructor() {
        this.settings = null;
        this.client = null;
        this.account = null;
        this.initialization = null;
        this.renewal = null;
    }

    initialize() {
        if (!this.initialization) this.initialization = this._initialize();
        return this.initialization;
    }

    async _initialize() {
        const response = await fetch("/api/auth/config", {cache: "no-store"});
        if (!response.ok) throw new Error("Không tải được cấu hình đăng nhập Microsoft.");
        this.settings = await response.json();
        if (!this.settings.enabled || !this.settings.configured) return this.settings;
        this.client = new msal.PublicClientApplication({
            auth: {
                clientId: this.settings.client_id,
                authority: this.settings.authority,
                redirectUri: this.settings.redirect_uri,
                postLogoutRedirectUri: this.settings.redirect_uri,
            },
            cache: {cacheLocation: "memoryStorage"},
        });
        await this.client.initialize();
        return this.settings;
    }

    async login() {
        await this.initialize();
        if (!this.client) throw new Error(this.settings?.enabled
            ? "Cần cấu hình ứng dụng đăng nhập Microsoft trên máy chủ."
            : "Đăng nhập Microsoft chưa được bật trên máy chủ.");
        const expectedOrigin = new URL(this.settings.redirect_uri).origin;
        if (expectedOrigin !== location.origin) {
            throw new Error("Mở ứng dụng tại " + expectedOrigin + " để đăng nhập Microsoft.");
        }
        const result = await this.client.loginPopup({
            scopes: this.settings.scopes,
            prompt: "select_account",
        });
        if (!result.account) throw new Error("Microsoft chưa trả về tài khoản đăng nhập.");
        this.account = result.account;
        this.client.setActiveAccount(result.account);
        return result.account;
    }

    async accessToken() {
        await this.initialize();
        if (!this.settings.enabled) return null;
        if (!this.account) throw new Error("Hãy đăng nhập Microsoft để tiếp tục.");
        if (!this.renewal) {
            this.renewal = this.client.acquireTokenSilent({
                scopes: this.settings.scopes,
                account: this.account,
            }).then(result => {
                if (!result.accessToken) throw new Error("Không nhận được access token cho ứng dụng.");
                return result.accessToken;
            }).catch(() => {
                throw new Error("Phiên Microsoft cần xác nhận lại. Hãy bấm Đăng nhập Microsoft.");
            }).finally(() => { this.renewal = null; });
        }
        return this.renewal;
    }

    async logout() {
        const account = this.account;
        this.account = null;
        this.renewal = null;
        if (!this.client || !account) return;
        try {
            await this.client.logoutPopup({account, postLogoutRedirectUri: this.settings.redirect_uri});
        } finally {
            await this.client.clearCache({account});
        }
    }
}

function microsoftLoginError(error) {
    if (error?.errorCode === "user_cancelled") return "Bạn đã hủy đăng nhập Microsoft.";
    if (error?.errorCode === "popup_window_error") return "Hãy cho phép cửa sổ bật lên để đăng nhập Microsoft.";
    const aad = String(error?.errorMessage || "").match(/AADSTS\d+/)?.[0];
    if (aad === "AADSTS50011") return "Redirect URI chưa khớp. Thêm địa chỉ đăng nhập hiển thị bên dưới vào Entra dưới nền tảng SPA.";
    if (aad) return "Microsoft từ chối đăng nhập (" + aad + "). Kiểm tra cấu hình ứng dụng và quyền truy cập.";
    return error?.errorCode ? "Không đăng nhập được Microsoft. Hãy thử lại." : error.message;
}
