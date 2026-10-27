# Thiết lập backend local riêng

Hướng dẫn này dành cho người chủ động chạy backend riêng trên máy mình. Với dịch vụ dùng chung, người dùng chỉ mở URL HTTPS do chủ dịch vụ cung cấp rồi đăng nhập Microsoft, không cần tự nhập key hay tạo app. Chủ dịch vụ làm theo [Triển khai backend dùng chung](SHARED_HOSTING.md); các bước localhost bên dưới giữ dành cho local.

Nếu Windows chưa có uv, cài bằng PowerShell theo [hướng dẫn chính thức](https://docs.astral.sh/uv/getting-started/installation/):

```powershell
winget install --id=astral-sh.uv -e
```

Mở lại terminal rồi kiểm tra `uv --version`. uv tự tải Python 3.12 theo `.python-version` của dự án;
không cần kích hoạt virtualenv thủ công. Trên macOS/Linux, dùng hướng dẫn cài uv ở liên kết trên.

Hướng dẫn này dành cho mỗi người clone repository và chạy ứng dụng trên máy của mình. Bạn cấu hình Gemini key và Microsoft Entra một lần; sau đó khởi động backend và đăng nhập bằng tài khoản cơ quan/trường học. Mỗi bản cài đặt dùng tenant của tổ chức cho phép nó hoạt động. Tài khoản Microsoft cá nhân không nằm trong luồng hướng dẫn này.

## 1. Clone và chuẩn bị

Cài [Git](https://git-scm.com/downloads) và [uv](https://docs.astral.sh/uv/getting-started/installation/). uv [tự tải Python còn thiếu](https://docs.astral.sh/uv/guides/install-python/); Node.js, Azure CLI, Fabric, Speech và Foundry không phải yêu cầu cho luồng cơ bản.

```powershell
git clone --branch feat/gemini-microsoft-documents https://github.com/DI-khanhtg/MS-agent.git
cd MS-agent/enterprise-agent
```

Dùng nhánh `feat/gemini-microsoft-documents` cho đến khi được merge; `main` hiện vẫn là IQ samples cũ. Với clone đã có, ở thư mục repository chạy `git fetch origin`, `git switch feat/gemini-microsoft-documents`, `git pull --ff-only`, rồi vào `enterprise-agent`.

Tạo Gemini API key trong [Google AI Studio](https://aistudio.google.com/apikey) bằng tài khoản được phép sử dụng. Mặc định ứng dụng chọn [`gemini-3.8-flash`](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash). Gemini là dịch vụ bên ngoài, có hạn mức và có thể phát sinh chi phí. Dùng `GEMINI_MODEL` để chọn model khác mà key của bạn được truy cập. Requests dùng [API tương thích OpenAI của Google](https://ai.google.dev/gemini-api/docs/openai), gửi đến Google.

Nếu chỉ muốn thử giao diện, chạy `uv run enterprise-agent demo --open`; bỏ qua các bước key và Microsoft cho đến khi dùng dữ liệu thật.

## 2. Cấu hình Microsoft Entra một lần

Thực hiện trong [Microsoft Entra admin center](https://entra.microsoft.com/) của tổ chức. Nếu chính sách không cho bạn tạo app, thêm permission hay cấp admin consent, nhờ quản trị viên tổ chức thực hiện. Không dùng chung key hoặc secret thật từ người đã chia sẻ repository.

Luồng cơ bản dùng **hai app registrations** cùng tenant, với **Accounts in this organizational directory only**. Giữ lại ba GUID và một secret: tenant ID, backend app ID, frontend app ID, backend secret **Value**. Hai app ID là hai giá trị khác nhau. Đây là cấu hình local cho một tenant; người thuộc tổ chức khác tự cấu hình trong tenant của họ. Triển khai SaaS nhiều tenant cần đăng ký, consent, allowlist và đánh giá riêng.

### Backend API

1. Tạo app registration, ví dụ `Enterprise Agent Local API`. Ghi **Directory (tenant) ID** và **Application (client) ID**.
2. Trong **Expose an API**, đặt Application ID URI thành `api://<backend-app-id>`, rồi thêm scope `access_as_user`. Chọn người được consent theo chính sách tổ chức.
3. Trong manifest, đặt `api.requestedAccessTokenVersion` thành `2`. Backend xác minh access token v2 có audience của backend.
4. Trong **Certificates & secrets**, tạo client secret cho backend local. Sao chép **Value**, không phải Secret ID; ghi nhớ ngày hết hạn để thay trước khi hết hạn. Secret chỉ đặt ở backend, không đặt vào SPA.
5. Trong **API permissions**, thêm delegated permission **`WorkIQAgent.Ask`** của Work IQ API. Nhờ quản trị viên cấp tenant admin consent theo chính sách tổ chức. Nếu chưa thấy Work IQ, quản trị viên cần kiểm tra tình trạng cung cấp dịch vụ và service principal.

Backend dùng [On-Behalf-Of (OBO)](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-on-behalf-of-flow) để đổi token của người dùng sang token Work IQ. Ứng dụng không triển khai application-only Work IQ.

### Frontend SPA

1. Tạo app registration riêng, ví dụ `Enterprise Agent Local UI`, trong cùng tenant. Ghi **Application (client) ID** của app này.
2. Vào **Authentication → Add a platform → Single-page application**, thêm chính xác `http://localhost:8000/auth/callback`.
3. SPA dùng [Authorization Code + PKCE](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-auth-code-flow). Không cần bật implicit-grant access-token/ID-token checkboxes hoặc tạo secret cho SPA.
4. Trong **API permissions → Add a permission → My APIs**, chọn backend vừa tạo; thêm delegated permission `access_as_user`, và cấp consent theo chính sách tenant.
5. Quay lại backend, vào **Expose an API → Authorized client applications**, thêm frontend SPA app ID và chọn scope `access_as_user` để preauthorize client. Wizard cũng đặt frontend ID này vào `ENTRA_ALLOWED_CLIENT_IDS` để backend kiểm tra client gọi API.

Browser chỉ xin token cho backend: `api://<backend-app-id>/access_as_user`. Backend xác minh tenant, audience, scope và client trước khi gọi Work IQ theo quyền người dùng. Không đưa Microsoft Graph token hoặc Work IQ token trực tiếp vào luồng đăng nhập giao diện này.

### Sẵn sàng cho Work IQ

Quản trị viên cần xác nhận Work IQ khả dụng, tenant policy, delegated permissions/consent và yêu cầu billing/licensing hiện hành. Xem [Work IQ MCP overview](https://learn.microsoft.com/en-us/microsoft-365/copilot/extensibility/work-iq/mcp/overview) và [Work IQ setup của ứng dụng](WORKIQ_SETUP.md). Liên kết **Enable your tenant for Work IQ** trong tài liệu Microsoft có hướng dẫn provision service principal khi cần; các bước dành cho A2A không thay thế toàn bộ cấu hình MCP.

Đăng nhập thành công hay đổi được token OBO chưa chứng minh rằng Work IQ có thể đọc dữ liệu. Tài khoản vẫn phải có quyền trên email, Teams, OneDrive/SharePoint cần truy vấn. Ứng dụng không tự cấp quyền mới sau khi đăng nhập.

## 3. Chạy wizard

Từ thư mục `enterprise-agent`:

```powershell
uv run enterprise-agent setup
uv run enterprise-agent doctor
uv run enterprise-agent start --open
```

Wizard hỏi các giá trị sau; nhập key/secret được ẩn:

| Giá trị được hỏi | Nguồn |
| --- | --- |
| Gemini API key | Google AI Studio của bạn |
| Gemini model | Mặc định `gemini-3.8-flash` |
| Tenant ID | Directory ID của tổ chức |
| Backend app ID | Application ID của backend API |
| Frontend SPA app ID | Application ID của frontend |
| Backend client secret | Secret **Value** của backend |

Wizard chỉ tạo `.env` khi file chưa có và không ghi đè cấu hình hiện có. Nó bật auth, durable database và các nhóm `workiq_read,local_read,artifact_generation`. Khi cần chỉnh lại, sửa `.env` của bản cài đặt này. File [`.env.example`](../.env.example) là mẫu công khai chỉ chứa placeholder; `.env` thật được Git bỏ qua.

`doctor` chỉ kiểm tra cấu hình offline, không gọi Gemini, Microsoft hoặc kiểm tra secret/token thật. `start` kiểm tra cấu hình trước khi chạy; `--open` mở đúng origin **http://localhost:8000**. Dùng **Kết nối Microsoft → Đăng nhập Microsoft**, cho phép popup và chọn tài khoản thuộc tenant đã cấu hình. Sau đó thử truy vấn một tài liệu/email bạn biết mình có quyền đọc để xác minh luồng thực.

Những lần sau chỉ cần `uv run enterprise-agent start --open`. `uv run enterprise-agent` cũng khởi động ứng dụng. Ctrl+C dừng server; khi phát triển dùng `uv run enterprise-agent start --reload`. Nếu đổi port/origin, cần chỉnh đồng thời callback SPA trong Entra và `ENTRA_SPA_REDIRECT_URI`.

## 4. Cấu hình thủ công hoặc Docker tùy chọn

Nếu không dùng wizard, ở `enterprise-agent` sao chép mẫu **chỉ khi chưa có `.env`**, rồi thay placeholder bằng giá trị của chính bạn:

```powershell
Copy-Item .env.example .env
```

Các biến chính cho luồng đăng nhập:

```dotenv
RUNTIME_MODE=gemini
GEMINI_API_KEY=<your-own-gemini-key>
GEMINI_MODEL=gemini-3.8-flash
AUTH_ENABLED=true
STORAGE_BACKEND=database
AZURE_TENANT_ID=<tenant-guid>
AZURE_CLIENT_ID=<backend-app-guid>
AZURE_CLIENT_SECRET=<backend-secret-value>
ENTRA_API_AUDIENCE=<backend-app-guid>
AUTH_ALLOWED_TENANT_IDS=<tenant-guid>
ENTRA_REQUIRED_SCOPES=access_as_user
ENTRA_ALLOWED_CLIENT_IDS=<frontend-spa-guid>
ENTRA_SPA_CLIENT_ID=<frontend-spa-guid>
ENTRA_SPA_REDIRECT_URI=http://localhost:8000/auth/callback
ENABLED_TOOL_GROUPS=workiq_read,local_read,artifact_generation
```

Để dùng Docker, cài Docker có **Compose 2.24 trở lên**; compose dùng `env_file` với `required: false`. Sau khi tạo/chỉnh `.env`:

```powershell
docker compose up --build
```

Mở **http://localhost:8000**. Compose chuyển cấu hình `.env` vào container; nếu chưa có `.env`, compose dùng demo. Docker là tùy chọn, không cần cho lệnh `uv`. Khi dừng, dùng `docker compose down`; giữ volume nếu muốn giữ dữ liệu.

## 5. Dữ liệu và xử lý lỗi

Token trình duyệt chỉ ở trong bộ nhớ trang; refresh cần đăng nhập lại. Backend lưu lịch sử/phiên/phê duyệt trong `data/agent.db` theo tenant/người dùng. Tệp bạn chọn được sao chép vào `data/documents/_users`; đăng xuất hoặc bỏ chọn nguồn không xóa bản sao hay lịch sử. Có thể gửi phần văn bản agent đọc tới Gemini sau egress filter. Chỉ nhập dữ liệu mà tổ chức cho phép xử lý bằng dịch vụ ngoài.

Nhập trình duyệt giới hạn 2 MB/tệp; PDF tối đa 200 trang, PPTX tối đa 200 slide; chưa có OCR, đọc hình ảnh hay định dạng Office nhị phân cũ. Xem [Document reading](DOCUMENT_READING.md), [Operations](OPERATIONS.md) và [Security](SECURITY.md) về giới hạn, sao lưu và ranh giới dữ liệu.

| Triệu chứng | Kiểm tra |
| --- | --- |
| `setup` báo đã có `.env` | Sửa file hiện có; wizard giữ nguyên để tránh mất cấu hình |
| `doctor` báo thiếu cấu hình | Bổ sung trường được nêu; không chia sẻ nội dung secret khi nhờ hỗ trợ |
| `AADSTS50011` | Callback đúng loại **SPA**, đúng `http://localhost:8000/auth/callback` |
| Consent/admin approval required | Nhờ quản trị viên kiểm tra frontend permission và backend Work IQ consent |
| 401 | Đăng nhập lại; token phải dành cho backend API với v2 token |
| 403 | Kiểm tra tenant/client allowlist, scope và quyền tài khoản |
| Đăng nhập được, Work IQ lỗi | Kiểm tra enablement, policy, consent, billing và quyền dữ liệu; xem [Work IQ setup](WORKIQ_SETUP.md) |
| Gemini báo key/model/quota hoặc 503 | Kiểm tra key, model được cấp quyền và hạn mức; thử lại sau với lỗi tạm thời |

Xem [Microsoft login](MICROSOFT_LOGIN.md) cho callback và nhập thư mục, [Local testing](LOCAL_TESTING.md) cho demo/kiểm thử. Các bước live cần chính tài khoản và tenant đã cấp quyền; cấu hình hợp lệ offline chưa xác nhận kết nối thật.
