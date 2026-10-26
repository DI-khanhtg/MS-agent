# Đăng nhập Microsoft khi chạy local

Ứng dụng dùng MSAL Browser với Authorization Code + PKCE. Bạn bấm **Kết nối Microsoft**,
chọn **Đăng nhập Microsoft**, rồi đăng nhập bằng tài khoản cơ quan/trường học thuộc tenant
đã cấu hình. Backend xác minh token trước khi dùng OBO để gọi Work IQ.

## Đăng ký địa chỉ đăng nhập một lần

Trong Microsoft Entra admin center, mở **App registrations** và chọn ứng dụng giao diện
có Client ID bằng `ENTRA_SPA_CLIENT_ID` trong `.env`. Đây là ứng dụng client đã được
allowlist trong `ENTRA_ALLOWED_CLIENT_IDS`, tách với `AZURE_CLIENT_ID` của backend.

1. Mở **Authentication → Add a platform → Single-page application**.
2. Thêm chính xác `http://localhost:8000/auth/callback`.
3. Lưu cấu hình. Giữ redirect Desktop `http://localhost` nếu còn dùng `check_login.py`.
4. Ứng dụng client cần delegated permission `access_as_user` của backend API. Giữ cấu hình
   consent Work IQ đã dùng cho kết quả `WORKIQ_TOKEN_OK`.

Tài liệu Microsoft:
[Authorization Code + PKCE](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-auth-code-flow),
[MSAL Browser redirect bridge](https://github.com/AzureAD/microsoft-authentication-library-for-js/blob/dev/lib/msal-browser/docs/redirect-bridge.md).

MSAL v5 cần bridge cùng origin ở `/auth/callback`. Ứng dụng đã đóng gói thư viện và bridge
ở `/static/vendor`; không cần npm hoặc CDN khi chạy. Bridge không được cache và không
dùng header COOP. Giấy phép và manifest phiên bản/checksum đi kèm các tài nguyên này.

## Cấu hình và chạy

```dotenv
APP_ENV=development
APP_HOST=127.0.0.1
RUNTIME_MODE=gemini
AUTH_ENABLED=true
ENTRA_SPA_CLIENT_ID=<frontend-client-id-trong-allowlist>
ENTRA_SPA_REDIRECT_URI=http://localhost:8000/auth/callback
ENABLED_TOOL_GROUPS=workiq_read,local_read,artifact_generation
```

Nếu chỉ có một `ENTRA_ALLOWED_CLIENT_IDS`, có thể để `ENTRA_SPA_CLIENT_ID` là placeholder;
backend tự chọn ID đó. Các tenant/backend/client secret, Gemini key và API scopes tiếp tục
dùng cấu hình trong [Work IQ setup](WORKIQ_SETUP.md). Client secret chỉ ở backend.

```powershell
cd C:\Users\Admin\Documents\MS-agent\enterprise-agent
uv run python scripts/check_config.py
uv run uvicorn enterprise_agent.api.app:app --reload --host 127.0.0.1 --port 8000
```

Mở **http://localhost:8000**, trùng origin của redirect URI. Dùng `127.0.0.1` trên trình
duyệt sẽ hiện liên kết chuyển sang địa chỉ đúng. Đăng nhập qua cửa sổ bật lên và cho phép
popup nếu trình duyệt hỏi. Thử câu: “Tìm tài liệu về dự án X trong OneDrive của tôi”.

Token được giữ trong bộ nhớ của trang và MSAL tự làm mới khi có thể. Nếu phiên cần xác nhận
lại, bấm **Đăng nhập Microsoft**. Refresh trang cần kết nối lại; phiên Microsoft SSO có thể
giúp bạn đăng nhập lại nhanh. Đăng xuất xóa hội thoại đang hiển thị, danh sách tài liệu,
thư mục đã chọn và thông tin đăng nhập trong trang. Dữ liệu đã lưu trên backend vẫn tách
theo tài khoản. Nút kết nối nâng cao bằng token còn dành cho kiểm tra API.

## Dùng cả Microsoft 365 và thư mục Windows

- **OneDrive/SharePoint:** hỏi agent bằng ngôn ngữ tự nhiên. Work IQ đọc theo quyền tài
  khoản đã đăng nhập; không tự cấp quyền vào thư mục hoặc tenant khác.
- **Windows:** bấm **Chọn thư mục trên máy**. Trình duyệt chỉ gửi các tệp bạn chọn. Tệp
  hỗ trợ được sao chép vào vùng riêng của tài khoản dưới `LOCAL_DOCUMENTS_ROOT/_users`.
  Sau khi nhập, hỏi agent đọc thư mục đã chọn hoặc bấm **Tạo bản nháp**.

Bạn cũng có thể bấm **kẹp giấy – Đính kèm tài liệu** ngay trong chat để chọn từng tệp
PDF, DOCX, PPTX, XLSX hoặc văn bản. Xem [Đọc tài liệu](DOCUMENT_READING.md) để biết phạm vi
trích xuất và ví dụ yêu cầu. Tên các tài liệu đang chọn xuất hiện phía trên ô chat.

Đây là bản sao tại thời điểm chọn, không phải đồng bộ thư mục hay quyền truy cập toàn ổ
đĩa. Tệp gốc không bị chỉnh sửa. Tệp nguồn được dùng để đọc/tổng hợp, còn tài liệu đầu ra
được tạo qua công cụ artifact. Nội dung được agent đọc có thể gửi đến Gemini để xử lý.

Giới hạn nhập: 2 MB mỗi tệp; tổng số tệp trong vùng tài khoản theo `LOCAL_MAX_FILES`
(mặc định 200). Hỗ trợ TXT/MD/CSV/JSON UTF-8, DOCX, PPTX, PDF và XLSX. Tệp ẩn, định dạng khác,
đường dẫn vượt phạm vi và liên kết/junction bị chặn. PDF scan cần OCR chưa có trong bản này.
Giới hạn giải nén/trích xuất văn bản vẫn áp dụng; tệp quá lớn hoặc không đọc được được báo
lỗi. Nhiều tệp được gom thành lô có giới hạn và uploader tôn trọng `Retry-After` khi gặp429.
Nếu nhập một phần thất bại, giao diện báo số tệp đã nhận và giữ thư mục phần đã nhập.

Chỉ có chế độ development hỗ trợ nhập thư mục local. Anonymous demo vẫn dùng thư mục
demo đã cấu hình; tài khoản đăng nhập không tự truy cập được dữ liệu demo chung hoặc tệp
nhập của tài khoản khác. Bật `workiq_write` là cấu hình riêng, có cơ chế phê duyệt của backend.

## Kiểm tra và xử lý lỗi

- `AADSTS50011`: kiểm tra URI đã được thêm dưới nền tảng **SPA**, đúng path/protocol/port.
- Có `LOGIN_OK`/`WORKIQ_TOKEN_OK` nhưng truy vấn thất bại: kiểm tra quyền dữ liệu, consent,
  billing và trạng thái Work IQ; lấy token thành công chưa chứng minh dịch vụ đọc được dữ liệu.
- 401: đăng nhập lại; access token phải dành cho backend, không phải token Microsoft Graph.
- 403: kiểm tra tenant/client/scopes hoặc quyền truy cập của tài khoản.
- Gemini503: thử lại sau hoặc chọn model khác có quyền truy cập bằng `GEMINI_MODEL`.

Kiểm thử offline chạy `uv run pytest`; browser checks tùy chọn chạy
`uv run --with playwright pytest tests/browser -q` trên máy có Google Chrome. Kiểm thử
browser giả lập ranh giới MSAL/identity provider; không thay thế đăng nhập và truy vấn thật.
