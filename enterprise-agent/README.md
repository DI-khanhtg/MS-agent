# Enterprise Agent

Chat với Gemini, tìm thông tin Microsoft 365 qua Work IQ và tổng hợp tài liệu thành Word, PDF, Excel, PowerPoint, Markdown hoặc JSON. Giao diện và backend chạy trên máy của bạn; đăng nhập Microsoft dùng tài khoản cơ quan/trường học thuộc tenant bạn cấu hình.

## Chạy lần đầu

Cài [Git](https://git-scm.com/downloads) và [uv](https://docs.astral.sh/uv/getting-started/installation/). uv [tự tải Python khi cần](https://docs.astral.sh/uv/guides/install-python/). Không cần Node.js cho giao diện hoặc bộ tạo tài liệu Python mặc định.

```powershell
git clone --branch feat/gemini-microsoft-documents https://github.com/DI-khanhtg/MS-agent.git
cd MS-agent/enterprise-agent
uv run enterprise-agent setup
uv run enterprise-agent doctor
uv run enterprise-agent start --open
```

Nhánh này đang chứa Enterprise Agent; `main` vẫn là IQ samples cũ. Nếu đã clone, ở thư mục repository chạy `git fetch origin`, `git switch feat/gemini-microsoft-documents`, `git pull --ff-only`, rồi `cd enterprise-agent`.

Trước khi chạy `setup`, chuẩn bị Gemini key và hai app Microsoft Entra theo [hướng dẫn thiết lập một lần](docs/SELF_HOST_SETUP.md). Wizard lần lượt hỏi key, model (mặc định `gemini-3.8-flash`), tenant ID, backend app ID, frontend SPA app ID và backend client secret. Key/secret được nhập ẩn. Wizard tạo `.env` được Git bỏ qua khi chưa có file; cấu hình đã có được giữ nguyên. Để thay đổi cấu hình sau này, sửa `.env` trên máy của bạn.

Mỗi người có backend local và cấu hình riêng. Người ở tổ chức khác dùng app/tenant do tổ chức của họ cho phép; đăng nhập không tự cấp quyền vào dữ liệu hay tenant khác. Nếu không có quyền tạo app/cấp consent, gửi [hướng dẫn quản trị](docs/SELF_HOST_SETUP.md#2-cấu-hình-microsoft-entra-một-lần) cho quản trị viên của mình. Fabric, Speech và Foundry không phải điều kiện để bắt đầu.

## Dùng hằng ngày

Trong thư mục `enterprise-agent`:

```powershell
uv run enterprise-agent start --open
```

Lệnh kiểm tra cấu hình trước khi khởi động và mở **http://localhost:8000**, khớp callback Microsoft. `uv run enterprise-agent` cũng khởi động ứng dụng. Khi phát triển, dùng `uv run enterprise-agent start --reload`. Dừng bằng Ctrl+C.

1. Chọn **Kết nối Microsoft → Đăng nhập Microsoft** và cho phép popup nếu được hỏi.
2. Hỏi thông tin bạn được phép đọc, ví dụ: “Tìm email mới nhất về dự án X và tóm tắt các việc cần làm.”
3. Dùng **kẹp giấy – Đính kèm tài liệu** hoặc **Chọn thư mục trên máy** để nhập tài liệu local, rồi yêu cầu tóm tắt hoặc tạo bản nháp.
4. Tải kết quả trong **Tài liệu của bạn**. Kiểm tra nội dung, nguồn và các phần thiếu trước khi sử dụng.

Token đăng nhập chỉ nằm trong bộ nhớ của trang. Refresh cần đăng nhập lại; phiên Microsoft SSO có thể giúp thao tác nhanh hơn. Lịch sử backend vẫn lưu theo tài khoản. Chi tiết: [Đăng nhập Microsoft](docs/MICROSOFT_LOGIN.md).

## Thử trước khi có key

```powershell
uv run enterprise-agent demo --open
```

Demo không cần `.env` hay key, chỉ thay cấu hình của tiến trình đang chạy và giữ nguyên `.env` thật. Nó dùng dữ liệu mẫu và kết quả xác định, không gọi Gemini hoặc Microsoft IQ; auth, Speech, telemetry và kết nối database từ xa đều bị tắt. Demo lưu riêng tại `data/demo.db`, `data/artifacts/demo` và `data/demo-audit.jsonl`, tách khỏi lịch sử live ở `data/agent.db`. Chọn thư mục mẫu `Project-X` rồi **Tạo bản nháp**, hoặc thử các gợi ý chat. Demo không thay thế việc kiểm thử đăng nhập và dữ liệu thật. Xem [Local testing](docs/LOCAL_TESTING.md).

## Dữ liệu và giới hạn

Cấu hình setup dùng SQLite tại `data/agent.db` để giữ lịch sử/phiên/phê duyệt. Tài liệu nhập qua trình duyệt được sao chép vào vùng tài khoản ở `data/documents/_users`; bỏ chọn hoặc đăng xuất không xóa bản sao hay lịch sử đã lưu. `.env`, cơ sở dữ liệu và đầu ra runtime được Git bỏ qua. Không đưa các file này lên GitHub.

Nguồn được agent đọc có thể gửi tới Gemini sau khi qua bộ lọc egress; xin phép tổ chức trước khi dùng dữ liệu công việc với dịch vụ bên ngoài. Gemini key và backend client secret nằm ở backend local. Microsoft 365 dùng quyền delegated của người đăng nhập qua OBO; quyền của bạn và chính sách tenant vẫn áp dụng.

Hỗ trợ UTF-8 TXT/MD/CSV/JSON, DOCX, PPTX, PDF và XLSX. Nhập từ trình duyệt tối đa 2 MB/tệp; PDF tối đa 200 trang, PPTX tối đa 200 slide. Chưa có OCR hoặc phân tích ảnh/biểu đồ; `.doc`, `.ppt`, `.xls` cần chuyển sang định dạng mới. Xem [Đọc tài liệu](docs/DOCUMENT_READING.md) để biết giới hạn trích xuất và cách báo phần chưa đọc.

## Kiểm tra và tài liệu kỹ thuật

```powershell
uv run enterprise-agent doctor
```

`doctor` kiểm tra cấu hình offline, không kiểm tra key thật, token, consent, billing hay quyền truy cập dịch vụ. `/health` là trạng thái cấu hình/liveness. Nếu đăng nhập được nhưng không đọc được Microsoft 365, kiểm tra [Work IQ setup](docs/WORKIQ_SETUP.md) và [xử lý lỗi đăng nhập](docs/MICROSOFT_LOGIN.md#kiểm-tra-và-xử-lý-lỗi).

| Tài liệu | Nội dung |
| --- | --- |
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
