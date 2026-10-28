# Enterprise Agent

Ứng dụng chat dùng Gemini để đọc Microsoft 365 qua Work IQ, phân tích tài liệu và tạo Word, PDF, Excel, PowerPoint, Markdown hoặc JSON. Dịch vụ dùng chung được cấu hình một lần trên server; người dùng mở website HTTPS rồi đăng nhập Microsoft bằng tài khoản cơ quan/trường học.

## Dành cho người dùng

1. Mở địa chỉ HTTPS do chủ dịch vụ cung cấp.
2. Chọn **Kết nối Microsoft → Đăng nhập Microsoft** và cho phép popup.
3. Hỏi về tài liệu bạn được quyền đọc, hoặc dán link chia sẻ OneDrive/SharePoint.
4. Yêu cầu tạo bản nháp và tải kết quả trong **Tài liệu của bạn**.

Mỗi hội thoại có URL riêng dạng `/chat/<session_id>`. Bạn có thể lưu bookmark, tải lại trang và dùng Back/Forward để mở lại hội thoại. Khi tải lại trang, đăng nhập Microsoft lại rồi ứng dụng tự mở hội thoại trong URL. Nút **Hội thoại mới** trở về `/`; link hội thoại chỉ mở được nội dung của tài khoản có quyền truy cập.

Nêu rõ định dạng đầu ra, ví dụ: “Đọc tài liệu này, phân tích rủi ro và xuất một file Word DOCX” hoặc “Tạo cả DOCX và PPTX từ nội dung trên”. Backend trả link tải từ file đã lưu, kể cả khi model bỏ sót link hoặc không trả thêm lời nhắn sau khi tạo file. Link được giữ nguyên khi mở lại lịch sử chat.

Người dùng dịch vụ chung không cần cài Python hoặc cung cấp API key. Quản trị viên tổ chức có thể cần chấp thuận ứng dụng. Đăng nhập thành công chưa xác nhận quyền đọc Microsoft 365; quyền tài khoản, consent và điều kiện dịch vụ của tổ chức vẫn áp dụng.

## Chuẩn bị mã nguồn

Cài [Git](https://git-scm.com/downloads) và [uv](https://docs.astral.sh/uv/getting-started/installation/), rồi chạy:

```powershell
git clone --branch feat/gemini-microsoft-documents https://github.com/DI-khanhtg/MS-agent.git
cd MS-agent/enterprise-agent
```

Nếu đã clone, chạy các lệnh tiếp theo từ thư mục `enterprise-agent`. Các lệnh trên chọn nhánh phát triển `feat/gemini-microsoft-documents`; `main` cũng đã có ứng dụng nhưng có thể chưa có các cập nhật mới nhất của nhánh này.

## Cấu hình Microsoft Entra một lần

Chủ bản cài đặt cần Gemini API key và hai app registrations riêng trong [Microsoft Entra](https://entra.microsoft.com/): backend API và frontend SPA.

**Backend API:**

1. Ghi Directory (tenant) ID và Application (client) ID.
2. Trong **Expose an API**, đặt URI `api://<backend-app-id>` và tạo scope `access_as_user`.
3. Trong manifest, đặt `api.requestedAccessTokenVersion` thành `2`.
4. Tạo client secret và lưu **Value** ở backend.
5. Thêm delegated permission `WorkIQAgent.Ask` của Work IQ và cấp consent theo chính sách tổ chức.
6. Để tải file qua Microsoft Graph, thêm delegated permission `Files.Read` và hoàn tất consent cho backend. Token Graph dùng các quyền đã được consent; OBO không tự mở hộp thoại xin quyền.

**Frontend SPA:**

1. Ghi Application (client) ID của frontend.
2. Trong **Authentication → Add a platform → Single-page application**, thêm `http://localhost:8000/auth/callback` cho local, hoặc `https://<hostname>/auth/callback` cho dịch vụ chung.
3. Thêm delegated permission `api://<backend-app-id>/access_as_user` của backend.
4. Trong backend **Expose an API → Authorized client applications**, thêm frontend ID và chọn scope `access_as_user`.

SPA dùng Authorization Code + PKCE; không tạo client secret cho frontend hoặc bật implicit grant. Các giá trị tương ứng nằm trong [.env.example](.env.example) và [.env.shared.example](.env.shared.example).

Khi phục vụ nhiều tổ chức, cả hai app cần hỗ trợ **Accounts in any organizational directory**. Đặt `AUTH_TENANT_MODE=organizations` và cấu hình `AUTH_ALLOWED_TENANT_IDS` theo các tổ chức được phép; để trống cho phép tổ chức hợp lệ có consent phù hợp. Consent của tenant chủ dịch vụ không thay thế consent ở tenant khác.

## Chạy local

```powershell
uv run enterprise-agent setup
uv run enterprise-agent doctor
uv run enterprise-agent start --open
```

Wizard tạo `.env` và không ghi đè file đã có. Nếu đã cấu hình, chỉ cần chạy `doctor` và `start --open`. Mở **http://localhost:8000**, khớp callback đã đăng ký. Khi phát triển, dùng `start --reload`; dừng bằng Ctrl+C.

Thử giao diện với dữ liệu mẫu, không cần key hoặc đăng nhập:

```powershell
uv run enterprise-agent demo --open
```

Demo không gọi Gemini/Microsoft và dùng database, artifact cùng audit log riêng.

## Host dùng chung

```powershell
uv run enterprise-agent setup --shared --output .env.shared
uv run enterprise-agent doctor --env-file .env.shared
```

File `.env.shared` giữ key và secret của chủ dịch vụ. Giữ thống nhất ba địa chỉ:

```dotenv
PUBLIC_BASE_URL=https://agent.example.com
ALLOWED_HOSTS=agent.example.com,localhost,127.0.0.1
ENTRA_SPA_REDIRECT_URI=https://agent.example.com/auth/callback
```

`agent.example.com` là ví dụ; thay bằng hostname thật và thêm callback chính xác dưới nền tảng SPA trong Entra.

Với domain trỏ về server, cổng 80/443 truy cập được và Docker đã cài:

```powershell
docker compose --env-file .env.shared -f compose.shared.yaml up --build
```

[Compose](compose.shared.yaml) chạy backend một worker và Caddy quản lý HTTPS. Volume giữ database và file đầu ra. Nếu chạy trực tiếp sau reverse proxy HTTPS hoặc tunnel:

```powershell
uv run enterprise-agent start --env-file .env.shared
```

Backend và proxy/tunnel phải luôn chạy. Nếu host trên máy cá nhân, máy cần bật và có Internet. Proxy phải hỗ trợ chat streaming SSE. Upload mặc định tắt; chủ dịch vụ có thể bật bằng wizard `--uploads` khi phù hợp.

## Dữ liệu và giới hạn

- Microsoft 365 được đọc theo quyền người đăng nhập; ứng dụng không tự cấp quyền vào tài liệu người khác.
- Tìm tài liệu cá nhân/được chia sẻ qua chỉ mục OneDrive/SharePoint cần Work IQ `retrieve` và điều kiện Copilot Credits của tenant. Mỗi lượt chat tối đa hai truy vấn tìm kiếm; chỉ mục không bảo đảm bao phủ mọi file.
- Hỗ trợ TXT/MD/CSV/JSON UTF-8, DOCX, PPTX, PDF có chữ và XLSX. Chưa có OCR cho PDF scan; định dạng `.doc`, `.ppt`, `.xls` cần chuyển đổi.
- Upload qua trình duyệt tối đa 2 MB/tệp; PDF tối đa 200 trang, PPTX tối đa 200 slide. File qua link trên 4 MB dùng Graph riêng, với giới hạn ứng dụng 20 MB.
- Lịch sử, phiên và artifact nằm trên máy chạy backend, tách theo tenant/tài khoản. Upload tạo bản sao trên backend; đăng xuất không xóa dữ liệu đã lưu.
- Nội dung được đọc có thể gửi tới Gemini sau bộ lọc egress. Chủ dịch vụ chịu chi phí Gemini và các dịch vụ Microsoft liên quan.
- `.env`, `.env.shared`, database và đầu ra runtime được Git bỏ qua. Giữ key/secret ở backend.

`docs` không cần cho vận hành. Log kỹ thuật nằm riêng trong `data/*.jsonl`, còn database chứa lịch sử chat; xóa log không xóa lịch sử chat. `LOG_LEVEL` điều chỉnh log console, không tắt audit log. Audit giúp chẩn đoán lỗi và theo dõi lượt gọi/chi phí, không lưu nguyên văn nội dung chat hay tài liệu.

## Kiểm tra và xử lý lỗi

`doctor` chỉ kiểm tra cấu hình offline. `/health` báo trạng thái server/cấu hình, không xác nhận Gemini key, consent, billing hoặc quyền dữ liệu thực tế.

| Hiện tượng | Cần kiểm tra |
| --- | --- |
| `AADSTS50011` | Callback Entra phải khớp protocol, hostname và `/auth/callback`, dưới nền tảng SPA |
| `401` | Đăng nhập lại; trình duyệt cần token dành cho backend API |
| `403` hoặc lỗi consent Graph | Consent của backend, quyền file của người dùng, tenant/client được phép |
| Không tìm thấy file được chia sẻ | Tên/link đúng, quyền tài khoản, chỉ mục và Work IQ `retrieve`/billing |
| Agent kết thúc không có nội dung | Nếu đã tạo file, backend trả link tải; nếu chưa có file thì UI báo lỗi. Kiểm tra mục tài liệu trước khi thử lại và xem `finish_reason` trong audit log |

Token frontend nằm trong bộ nhớ trang. Refresh cần đăng nhập lại; phiên SSO của Microsoft có thể giúp thao tác nhanh hơn. Fabric, Speech và Foundry là tích hợp tùy chọn.

Kiểm thử phát triển:

```powershell
uv sync --extra dev
uv run pytest -m "not live" -q
uv run ruff check .
```

Test live trong `tests/integration` và `evals` cần bật biến `RUN_*_LIVE_EVALS`, cấu hình dịch vụ và token phù hợp; có thể phát sinh chi phí. Chỉ truyền access token qua môi trường của tiến trình, không lưu vào Git hoặc `.env`.
