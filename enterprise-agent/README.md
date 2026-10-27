# Enterprise Agent

Chat với AI, tìm thông tin Microsoft 365 qua Work IQ và tổng hợp tài liệu thành Word, PDF, Excel, PowerPoint, Markdown hoặc JSON. Khi dùng dịch vụ chung, chủ dịch vụ cấu hình Gemini và Microsoft Entra trên server một lần; người dùng chỉ mở website HTTPS rồi đăng nhập Microsoft bằng tài khoản cơ quan/trường học.

## Mở dịch vụ và bắt đầu

1. Mở URL HTTPS do chủ dịch vụ gửi. Repository chưa có URL dịch vụ đã triển khai; `https://agent.example.com` là địa chỉ minh họa.
2. Chọn **Kết nối Microsoft → Đăng nhập Microsoft**, cho phép popup và chọn tài khoản cơ quan/trường học.
3. Hỏi thông tin bạn được phép đọc, ví dụ: “Tìm email mới nhất về dự án X và tóm tắt các việc cần làm.”
4. Nếu dịch vụ bật tải tài liệu, dùng **kẹp giấy – Đính kèm tài liệu** hoặc **Chọn thư mục trên máy**, rồi yêu cầu tóm tắt hoặc tạo bản nháp.
5. Tải kết quả trong **Tài liệu của bạn**. Kiểm tra nội dung, nguồn và các phần thiếu trước khi sử dụng.

Bạn không cần Gemini key, client secret, app Entra hay cài công cụ lập trình. Nếu Microsoft yêu cầu admin approval, nhờ quản trị viên tổ chức chấp thuận ứng dụng. Work IQ còn phụ thuộc quyền dữ liệu, policy, consent và điều kiện dịch vụ/licensing của tổ chức; đăng nhập thành công chưa chứng minh đọc được Microsoft 365.

Token đăng nhập chỉ nằm trong bộ nhớ của trang. Refresh cần đăng nhập lại; phiên Microsoft SSO có thể giúp thao tác nhanh hơn. Đăng xuất xóa dữ liệu đang hiển thị; lịch sử đã lưu trên server vẫn tách theo tenant và tài khoản.

## Chủ dịch vụ thiết lập một lần

Xem [Triển khai backend dùng chung](docs/SHARED_HOSTING.md) cho Entra đa tổ chức, URL HTTPS, consent, Docker, lưu trữ và vận hành. Chủ dịch vụ giữ key/secrets ở backend và quản lý chi phí, hạn mức, sao lưu và thời hạn lưu dữ liệu.

Lấy mã nguồn khi cần, với [Git](https://git-scm.com/downloads) và [uv](https://docs.astral.sh/uv/getting-started/installation/):

```powershell
git clone --branch feat/gemini-microsoft-documents https://github.com/DI-khanhtg/MS-agent.git
cd MS-agent/enterprise-agent
uv run enterprise-agent setup --shared --output .env.shared
docker compose --env-file .env.shared -f compose.shared.yaml up --build
```

Nhánh này đang chứa Enterprise Agent; `main` vẫn là IQ samples cũ. Nếu đã clone, ở thư mục repository chạy `git fetch origin`, `git switch feat/gemini-microsoft-documents`, `git pull --ff-only`, rồi `cd enterprise-agent`.

Wizard hỏi cấu hình AI, tenant chủ dịch vụ, hai app Entra, backend secret và URL HTTPS. File `.env.shared` chứa secrets, được Git bỏ qua. Compose kèm backend và proxy Caddy tự quản lý HTTPS khi domain thật trỏ về server và cổng 80/443 truy cập được. Chủ dịch vụ chuẩn bị DNS, firewall và callback Entra theo hướng dẫn trước khi gửi URL cho người dùng. Upload mặc định tắt; thêm `--uploads` vào wizard khi tổ chức cho phép. Chưa có website được triển khai từ repository này.

Các tổ chức dùng cùng app do chủ dịch vụ đăng ký; quản trị viên từng tổ chức có thể cần cấp consent. Key Gemini và backend secret không gửi tới trình duyệt hay GitHub. Fabric, Speech và Foundry không phải điều kiện để bắt đầu.

## Chạy backend local riêng (tùy chọn)

Chủ bản cài đặt local làm theo [Thiết lập local](docs/SELF_HOST_SETUP.md), chuẩn bị key/app của mình rồi chạy trong `enterprise-agent`:

```powershell
uv run enterprise-agent setup
uv run enterprise-agent doctor
uv run enterprise-agent start --open
```

Lệnh kiểm tra cấu hình trước khi khởi động và mở **http://localhost:8000**, khớp callback Microsoft. `uv run enterprise-agent` cũng khởi động ứng dụng. Khi phát triển, dùng `uv run enterprise-agent start --reload`. Dừng bằng Ctrl+C.

Chi tiết callback, đăng nhập và nhập thư mục local: [Đăng nhập Microsoft local](docs/MICROSOFT_LOGIN.md).

## Thử trước khi có key

```powershell
uv run enterprise-agent demo --open
```

Demo không cần `.env` hay key, chỉ thay cấu hình của tiến trình đang chạy và giữ nguyên `.env` thật. Nó dùng dữ liệu mẫu và kết quả xác định, không gọi Gemini hoặc Microsoft IQ; auth, Speech, telemetry và kết nối database từ xa đều bị tắt. Demo lưu riêng tại `data/demo.db`, `data/artifacts/demo` và `data/demo-audit.jsonl`, tách khỏi lịch sử live ở `data/agent.db`. Chọn thư mục mẫu `Project-X` rồi **Tạo bản nháp**, hoặc thử các gợi ý chat. Demo không thay thế việc kiểm thử đăng nhập và dữ liệu thật. Xem [Local testing](docs/LOCAL_TESTING.md).

## Dữ liệu và giới hạn

Cấu hình mặc định dùng SQLite để giữ lịch sử/phiên/phê duyệt; với dịch vụ chung, dữ liệu nằm trên server và cần volume bền vững. Tài liệu nhập qua trình duyệt được sao chép vào vùng tài khoản của server; bỏ chọn hoặc đăng xuất không xóa bản sao hay lịch sử đã lưu. Tải tài liệu ở chế độ shared mặc định tắt; chủ dịch vụ có thể bật rõ ràng. `.env`, `.env.shared`, cơ sở dữ liệu và đầu ra runtime được Git bỏ qua. Không đưa các file này lên GitHub.

Nguồn được agent đọc có thể gửi tới Gemini sau khi qua bộ lọc egress; chỉ dùng dữ liệu tổ chức cho phép xử lý bằng dịch vụ bên ngoài. Gemini key và backend client secret nằm ở backend. Microsoft 365 dùng quyền delegated của người đăng nhập qua OBO; quyền của bạn và chính sách tenant vẫn áp dụng. Bản shared hiện chạy một worker/một replica; PostgreSQL được khuyến nghị cho vận hành lâu dài nhưng không tự tạo khả năng scale ngang hay HA.

Hỗ trợ UTF-8 TXT/MD/CSV/JSON, DOCX, PPTX, PDF và XLSX. Nhập từ trình duyệt tối đa 2 MB/tệp; PDF tối đa 200 trang, PPTX tối đa 200 slide. Chưa có OCR hoặc phân tích ảnh/biểu đồ; `.doc`, `.ppt`, `.xls` cần chuyển sang định dạng mới. Xem [Đọc tài liệu](docs/DOCUMENT_READING.md) để biết giới hạn trích xuất và cách báo phần chưa đọc.

## Kiểm tra và tài liệu kỹ thuật

```powershell
uv run enterprise-agent doctor
```

`doctor` kiểm tra cấu hình offline, không kiểm tra key thật, token, consent, billing hay quyền truy cập dịch vụ. `/health` là trạng thái cấu hình/liveness. Nếu đăng nhập được nhưng không đọc được Microsoft 365, kiểm tra [Work IQ setup](docs/WORKIQ_SETUP.md) và [xử lý lỗi đăng nhập](docs/MICROSOFT_LOGIN.md#kiểm-tra-và-xử-lý-lỗi).

Với bản shared dùng file riêng, chạy `uv run enterprise-agent doctor --env-file .env.shared`; khởi động native bằng `uv run enterprise-agent start --env-file .env.shared` sau khi chuẩn bị HTTPS proxy theo [Shared hosting](docs/SHARED_HOSTING.md).

| Tài liệu | Nội dung |
| --- | --- |
| [Shared hosting](docs/SHARED_HOSTING.md) | Chủ dịch vụ cấu hình một lần, người dùng mở HTTPS và đăng nhập |
| [Self-host setup](docs/SELF_HOST_SETUP.md) | App Entra, Gemini key, biến môi trường, Docker tùy chọn |
| [Local testing](docs/LOCAL_TESTING.md) | Demo offline, kiểm thử và Speech tùy chọn |
| [Work IQ](docs/WORKIQ_SETUP.md), [Fabric](docs/FABRIC_SETUP.md) | Consent, dữ liệu doanh nghiệp và kiểm thử live |
| [Artifacts](docs/ARTIFACTS.md), [Actions](docs/ACTIONS.md) | Định dạng đầu ra và hành động cần phê duyệt |
| [Operations](docs/OPERATIONS.md), [Security](docs/SECURITY.md) | Lưu trữ, sao lưu, hạn mức và ranh giới bảo mật |
| [Implementation status](docs/IMPLEMENTATION_STATUS.md) | Phạm vi đã triển khai và phần cần xác minh live |

Kiểm thử phát triển, chạy từ `enterprise-agent`:

```powershell
uv sync --extra dev
uv run pytest -m "not live" -q
uv run ruff check .
```

Live integrations cần cấu hình và xác minh bằng tài khoản thực, có thể phát sinh chi phí. Hướng dẫn kiểm thử Work IQ/Fabric nằm trong các tài liệu tương ứng; chỉ đưa access token vào biến môi trường của tiến trình, không lưu vào `.env` hoặc Git.

Đánh giá Gemini sau khi cấu hình key thật:

```powershell
$env:RUN_LIVE_EVALS = "true"
uv run pytest evals/test_tool_selection_live.py -m live -v
```

Kiểm thử Work IQ với access token dành cho backend API của người dùng đã đăng nhập:

```powershell
$env:RUN_WORKIQ_LIVE_EVALS = "true"
$env:WORKIQ_USER_ACCESS_TOKEN = $accessToken
uv run pytest tests/integration/test_workiq_live.py -m live -v
uv run pytest evals/test_workiq_tool_selection_live.py -m live -v
```

Với Fabric đã được publish, dùng `RUN_FABRIC_LIVE_EVALS=true` và
`FABRIC_USER_ACCESS_TOKEN` rồi chạy `tests/integration/test_fabric_live.py` và
`evals/test_fabric_tool_selection_live.py` với `-m live -v`. Đánh giá nhiều nguồn còn cần
`RUN_MULTI_SOURCE_LIVE_EVALS=true` và ba nhóm đọc Work IQ/Fabric/tạo artifact. Các test live
không nằm trong kiểm tra `doctor` và không tự chạy trong lệnh khởi động.
