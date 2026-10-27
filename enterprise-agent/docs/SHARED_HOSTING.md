# Triển khai backend dùng chung

Chủ dịch vụ cấu hình Gemini và Microsoft Entra một lần trên server. Người dùng mở website HTTPS và đăng nhập bằng tài khoản Microsoft cơ quan/trường học. Nhiều tổ chức dùng cùng ứng dụng của chủ dịch vụ; quản trị viên từng tổ chức có thể cần cấp consent. Người dùng không cần clone repository, cài Python, tạo app hay cung cấp API key.

Tài liệu dùng `https://agent.example.com` làm ví dụ; đây không phải URL dịch vụ đang chạy. Chủ dịch vụ cần tên miền thật, server công khai và cấu hình Entra. Bộ Docker Compose đã kèm Caddy để xin/gia hạn chứng chỉ HTTPS khi DNS và kết nối công khai được cấu hình đúng. Chưa có triển khai thực được thực hiện trong repository này.

## 1. Chuẩn bị hai app Microsoft Entra

Trong tenant của chủ dịch vụ, tạo app backend API và app frontend SPA riêng. Với mỗi app, chọn **Accounts in any organizational directory** để phục vụ các tài khoản cơ quan/trường học ở nhiều tổ chức. Microsoft mô tả cách đăng ký nhiều tenant, xử lý issuer và consent trong [hướng dẫn multitenant](https://learn.microsoft.com/en-us/entra/identity-platform/howto-convert-app-to-be-multi-tenant).

Backend API:

1. Ghi Application (client) ID và Directory (tenant) ID của chủ dịch vụ.
2. Trong **Expose an API**, đặt Application ID URI phù hợp với app (thông thường `api://<backend-app-id>`) và thêm delegated scope `access_as_user`. App ID URI phải đáp ứng yêu cầu duy nhất của Microsoft cho app nhiều tenant.
3. Trong manifest, đặt `api.requestedAccessTokenVersion` thành `2`.
4. Tạo backend client secret, lưu **Value** ở server và theo dõi hạn để thay mới.
5. Thêm delegated permission Work IQ phù hợp với chức năng đang bật. Luồng `ask` của ứng dụng dùng `WorkIQAgent.Ask`; quản trị viên phải xem các quyền và policy đang áp dụng trước khi cấp consent.

Frontend SPA:

1. Ghi Application (client) ID của frontend.
2. Trong **Authentication → Add a platform → Single-page application**, thêm chính xác `https://agent.example.com/auth/callback`, thay bằng domain thực của bạn.
3. Thêm delegated permission `api://<backend-app-id>/access_as_user` của backend. SPA dùng Authorization Code + PKCE; không tạo client secret cho SPA.
4. Trong backend **Expose an API → Authorized client applications**, thêm frontend app ID và scope `access_as_user`. Server cũng kiểm tra frontend này trong danh sách client được phép.

Browser xin token dành cho backend; backend xác minh token rồi đổi sang token Work IQ theo quyền người dùng. Secret backend chỉ ở server. Xem [Microsoft OBO](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-on-behalf-of-flow).

Đăng nhập dùng authority `organizations` để nhận tài khoản cơ quan/trường học. `AUTH_TENANT_MODE=organizations` cho phép tenant tổ chức hợp lệ; `allowlist` chỉ nhận các tenant được khai báo trong `AUTH_ALLOWED_TENANT_IDS`. Backend kiểm tra chữ ký, issuer theo tenant, audience, scope và frontend client. Chọn allowlist nếu dịch vụ chỉ dành cho các tổ chức đã duyệt.

## 2. Consent và quyền ở từng tổ chức

Ứng dụng nhiều tenant vẫn chịu chính sách consent của tổ chức người dùng. Quản trị viên tổ chức có thể phải chấp thuận frontend/backend và các quyền Work IQ; việc chủ dịch vụ cấp consent trong tenant của mình không thay thế chấp thuận ở tổ chức khác. Hướng dẫn [multitenant của Microsoft](https://learn.microsoft.com/en-us/entra/identity-platform/howto-convert-app-to-be-multi-tenant) giải thích service principal và consent khi một tổ chức bắt đầu dùng ứng dụng.

Kiểm tra Work IQ đã khả dụng và các điều kiện permission, policy, licensing/billing hiện hành của tổ chức. Xem [Work IQ MCP overview](https://learn.microsoft.com/en-us/microsoft-365/copilot/extensibility/work-iq/mcp/overview) và [Work IQ setup](WORKIQ_SETUP.md). Đăng nhập hoặc đổi được token OBO chưa chứng minh truy vấn được dữ liệu. Thử bằng tài khoản có quyền trên một tài liệu/email đã biết; ứng dụng không tự cấp quyền vào dữ liệu người khác.

## 3. Cấu hình server

Nếu cần lấy mã nguồn, dùng nhánh đang chứa tính năng:

```powershell
git clone --branch feat/gemini-microsoft-documents https://github.com/DI-khanhtg/MS-agent.git
cd MS-agent/enterprise-agent
```

`main` hiện vẫn chứa IQ samples cũ. Cài [uv](https://docs.astral.sh/uv/getting-started/installation/) nếu muốn chạy wizard; Docker có thể dùng mẫu cấu hình mà không cần uv.

Chạy wizard dành cho chủ dịch vụ:

```powershell
uv run enterprise-agent setup --shared --output .env.shared
```

Wizard hỏi Gemini key/model, tenant chủ dịch vụ, backend/frontend app ID, backend secret và URL HTTPS công khai. Nó không ghi đè file đã tồn tại. Upload mặc định tắt; nếu tổ chức cho phép nhận bản sao tài liệu trên server, chủ dịch vụ có thể tạo cấu hình có upload bằng `uv run enterprise-agent setup --shared --uploads --output .env.shared`.

Hoặc tạo file từ mẫu khi chưa có `.env.shared` rồi sửa placeholder trên server:

```powershell
Copy-Item .env.shared.example .env.shared
```

Thay tất cả giá trị mẫu: key/secret, các app/tenant ID, hostname trong `PUBLIC_BASE_URL` và `ALLOWED_HOSTS`, cùng `ENTRA_SPA_REDIRECT_URI`. Hostname phải là domain thực đã trỏ về server; callback phải trùng chính xác địa chỉ đã đăng ký cho SPA. Không chạy công khai với `agent.example.com` hoặc `TO_BE_FILLED_LATER` còn trong cấu hình.

Các thiết lập cần thống nhất:

| Thiết lập | Ý nghĩa |
| --- | --- |
| `DEPLOYMENT_MODE=shared` | Bật luồng dịch vụ dùng chung và giao diện chỉ đăng nhập |
| `APP_ENV=production`, `AUTH_ENABLED=true` | Chạy với auth và yêu cầu lưu trữ/công cụ production |
| `PUBLIC_BASE_URL=https://agent.example.com` | Origin HTTPS công khai, không kèm path |
| `ENTRA_SPA_REDIRECT_URI=https://agent.example.com/auth/callback` | Callback khớp chính xác SPA registration |
| `AUTH_TENANT_MODE=organizations` | Nhận tài khoản tổ chức nhiều tenant; đổi `allowlist` để giới hạn tenant |
| `ENTRA_API_AUDIENCE`, `ENTRA_ALLOWED_CLIENT_IDS`, `ENTRA_SPA_CLIENT_ID` | Backend audience và frontend app được phép |
| `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET` | Tenant chủ dịch vụ và credential backend |
| `GEMINI_API_KEY`, `GEMINI_MODEL` | Cấu hình AI của chủ dịch vụ, dùng chung hạn mức/chi phí |
| `DOCUMENT_UPLOADS_ENABLED=false` | Mặc định không nhận tài liệu từ trình duyệt; bật rõ ràng khi được phép |

Giữ secrets trong `.env.shared` có quyền truy cập hạn chế hoặc secret store của hạ tầng. Không đưa key/secret vào mã frontend, README, issue, GitHub hay ảnh chụp màn hình. Không đưa access token người dùng vào file cấu hình lâu dài. App ID, authority và scope có thể công khai để browser đăng nhập; chúng không phải client secret.

## 4. Khởi động với HTTPS

Trước khi chạy Compose, chủ dịch vụ cần trỏ DNS công khai của domain thực về server và cho phép kết nối Internet tới cổng TCP **80 và 443** trên firewall/router. Hai cổng này phải sẵn sàng cho proxy Caddy; nếu đã có proxy chiếm cổng, tích hợp theo hạ tầng của bạn trước khi chạy. Callback Entra phải dùng cùng HTTPS origin với website.

Từ `enterprise-agent`, với Docker Compose:

```powershell
docker compose --env-file .env.shared -f compose.shared.yaml up --build
```

[`compose.shared.yaml`](../compose.shared.yaml) chạy hai service:

- `agent`: backend với một worker, chỉ mở cổng `8000` trong mạng Compose, không publish cổng backend ra host. Volume `agent-data` giữ database và tệp/artifact.
- `proxy`: image `caddy:2`, publish cổng `80` và `443`, đọc `PUBLIC_BASE_URL` và cấu hình từ [`infra/Caddyfile`](../infra/Caddyfile). Caddy chuyển tiếp request tới `agent:8000`; `flush_interval -1` giúp trả các phần chat streaming ngay. Hai volume `caddy-data` và `caddy-config` giữ trạng thái/chứng chỉ của proxy.

Với domain thực, DNS công khai đúng và cổng 80/443 truy cập được, [Caddy tự quản lý HTTPS](https://caddyserver.com/docs/automatic-https), gồm xin/gia hạn chứng chỉ và chuyển HTTP sang HTTPS. Người dùng mở URL HTTPS của chủ dịch vụ sau khi proxy cấp được chứng chỉ và backend hoạt động. Việc thêm các file Compose/Caddy không có nghĩa website đã được triển khai hoặc chứng chỉ đã được cấp; bản thay đổi này chưa được chạy Docker trên server công khai.

Nếu tùy biến proxy, giữ cùng origin cho UI/API/callback, cho phép chat streaming và không cache `/auth/callback` hoặc các API có dữ liệu tài khoản. Đổi domain cần đổi đồng thời `PUBLIC_BASE_URL`, `ALLOWED_HOSTS`, callback cấu hình và SPA registration.

Nếu chạy native, chỉ rõ file môi trường của dịch vụ:

```powershell
uv run enterprise-agent doctor --env-file .env.shared
uv run enterprise-agent start --env-file .env.shared
```

`doctor` kiểm tra cấu hình offline; `start` kiểm tra trước khi khởi động. Kiểm tra offline không xác nhận key, consent, licensing hay quyền dữ liệu thực. Chạy native chỉ khởi động backend: chủ dịch vụ phải tự đặt Caddy hoặc reverse proxy HTTPS phù hợp phía trước, trỏ về cổng backend của bản cài đặt và giới hạn truy cập trực tiếp tới cổng đó.

Trước khi gửi URL, đăng nhập với một tài khoản thuộc mỗi tổ chức cần phục vụ; thử truy vấn Work IQ, tạo/tải bản nháp, đăng xuất và đăng nhập tài khoản khác. Nếu bật upload, thử một tệp không nhạy cảm và kiểm tra chính sách lưu trữ của dịch vụ.

## 5. Dữ liệu, hạn mức và vận hành

Lịch sử, phiên, artifact và tệp nhập được tách theo tenant và object ID của tài khoản. Đây là dữ liệu lưu trên server dùng chung do chủ dịch vụ quản lý. Tệp được chọn được sao chép lên server; bỏ chọn, đóng trang hoặc đăng xuất không xóa dữ liệu đã lưu. Nội dung agent đọc có thể gửi tới Gemini sau egress filter. Chủ dịch vụ phải công bố phạm vi xử lý, thời hạn lưu, cách yêu cầu xóa và chính sách sao lưu trước khi cho phép dữ liệu công việc được tải lên.

Upload ở chế độ shared mặc định tắt; cấu hình mặc định chỉ bật `workiq_read,artifact_generation`. Khi được tổ chức cho phép, dùng tùy chọn wizard `--shared --uploads` khi tạo cấu hình mới, hoặc sửa cả hai thiết lập trong file server hiện có:

```dotenv
DOCUMENT_UPLOADS_ENABLED=true
ENABLED_TOOL_GROUPS=workiq_read,local_read,artifact_generation
```

Người đã đăng nhập mới dùng vùng nhập riêng của mình. Giao diện hiển thị thông báo về bản sao trên server và AI. Shared không cung cấp ô nhập đường dẫn thư mục server. Giới hạn hiện tại: 2 MB/tệp, không OCR, không đọc Office nhị phân cũ; xem [Document reading](DOCUMENT_READING.md).

Chạy **một worker, một replica**. SQLite với volume bền vững phù hợp pilot nhỏ; sao lưu cả database và tệp/artifact theo [Operations](OPERATIONS.md). PostgreSQL được khuyến nghị cho vận hành production (`uv sync --extra production` cung cấp driver), nhưng trạng thái SDK và điều phối tệp hiện chưa phân tán. Đổi database không làm hệ thống tự scale ngang hay có HA. Giữ một replica cho đến khi có cơ chế điều phối và lưu trữ phù hợp.

Key AI của chủ dịch vụ chịu tổng lưu lượng tất cả tài khoản. Các mặc định giới hạn cho một tiến trình:

| Thiết lập | Mặc định |
| --- | --- |
| `RATE_LIMIT_PER_MINUTE` | 60 request API/phút cho mỗi tài khoản |
| `SHARED_REQUESTS_PER_MINUTE` | 600 request API/phút toàn dịch vụ |
| `SHARED_CHAT_REQUESTS_PER_MINUTE` | 120 lượt chat/phút toàn dịch vụ |
| `SHARED_MAX_CONCURRENT_CHATS` | 8 lượt chat đang chạy toàn dịch vụ |
| `SHARED_MAX_CONCURRENT_PER_USER` | 2 lượt chat đang chạy mỗi tài khoản |

Đây là giới hạn lưu lượng và số lượt chạy, không phải trần chi phí AI. Theo dõi quota/chi phí, thêm hạn mức proxy phù hợp, theo dõi dung lượng volume, audit, lỗi đăng nhập và thời hạn client secret. Các bộ đếm nằm trong tiến trình và không điều phối nhiều worker/replica. Giữ volume khi cập nhật hoặc dừng container; việc xóa volume làm mất dữ liệu nếu chưa sao lưu.

Người dùng gặp lỗi đăng nhập nên thử lại, cho phép popup hoặc liên hệ quản trị viên dịch vụ. Chủ dịch vụ kiểm tra callback, app account types, consent, tenant policy, secret và quyền Work IQ bằng log server; không yêu cầu người dùng nhập key hay dán token vào giao diện shared.

Chạy local riêng và demo vẫn có trong [Self-host setup](SELF_HOST_SETUP.md) và [Local testing](LOCAL_TESTING.md). Đây là lựa chọn của người vận hành một bản cài đặt riêng; không phải bước cài đặt cho người dùng website đã được host.
