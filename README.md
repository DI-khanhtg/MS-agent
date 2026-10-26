# Enterprise Agent

Ứng dụng chat chạy trên máy của bạn: dùng Gemini để tổng hợp tài liệu, đọc Microsoft 365 qua Work IQ theo quyền tài khoản đã đăng nhập, và tạo Word, PDF, Excel, PowerPoint, Markdown hoặc JSON.

Mỗi người clone repository, chạy **backend local riêng** và cấu hình **key/app của mình một lần**. Những lần sau chỉ cần chạy ứng dụng rồi đăng nhập Microsoft. Tài khoản cơ quan/trường học ở các tổ chức khác nhau dùng cấu hình tenant của tổ chức mình. Xem [hướng dẫn thiết lập đầy đủ](enterprise-agent/docs/SELF_HOST_SETUP.md) cho bước quản trị Microsoft Entra.

## Bắt đầu

Cài [Git](https://git-scm.com/downloads) và [uv](https://docs.astral.sh/uv/getting-started/installation/). uv tự tải Python phù hợp khi cần; không cần cài Node.js cho giao diện và các định dạng tài liệu mặc định. Xem [cách uv quản lý Python](https://docs.astral.sh/uv/guides/install-python/).

```powershell
git clone --branch feat/gemini-microsoft-documents https://github.com/DI-khanhtg/MS-agent.git
cd MS-agent/enterprise-agent
uv run enterprise-agent setup
uv run enterprise-agent doctor
uv run enterprise-agent start --open
```

Hiện Enterprise Agent nằm ở nhánh `feat/gemini-microsoft-documents`; `main` vẫn là bản IQ samples cũ. Giữ tùy chọn `--branch` ở trên cho đến khi nhánh được merge. Nếu đã clone, từ thư mục repository chạy `git fetch origin`, `git switch feat/gemini-microsoft-documents`, rồi `git pull --ff-only` trước khi vào `enterprise-agent`.

Lệnh `setup` hỏi Gemini API key, tenant ID, backend app ID/client secret và frontend SPA app ID. Key và secret được nhập ẩn, lưu trong `.env` được Git bỏ qua; wizard không ghi đè `.env` đã có. Chuẩn bị các giá trị theo [hướng dẫn cấu hình một lần](enterprise-agent/docs/SELF_HOST_SETUP.md). `doctor` chỉ kiểm tra cấu hình offline; đăng nhập và truy vấn thực mới xác nhận được dịch vụ hoạt động.

Trình duyệt mở **http://localhost:8000**. Chọn **Kết nối Microsoft → Đăng nhập Microsoft**, dùng tài khoản cơ quan/trường học thuộc tenant đã cấu hình. Nếu bạn không được tạo app hoặc cấp consent, nhờ quản trị viên tổ chức hỗ trợ theo hướng dẫn; mỗi người dùng key của mình.

Muốn xem giao diện trước khi có key hoặc cấu hình Microsoft:

```powershell
uv run enterprise-agent demo --open
```

Demo dùng dữ liệu mẫu và kết quả mẫu xác định, không gọi Gemini hoặc Microsoft IQ. Xem [README ứng dụng](enterprise-agent/README.md) để biết cách dùng, dữ liệu lưu trên máy và giới hạn đọc tài liệu. Fabric, Speech và Foundry là các tích hợp tùy chọn.

## Các sample khác và nguồn gốc

Repository này phát triển từ các IQ samples của Microsoft và bổ sung Enterprise Agent. Đây là repository tùy biến do chủ repository duy trì; tên sản phẩm và các liên kết nguồn không thể hiện Microsoft bảo trợ hay xác nhận bản tùy biến.

| Sample | Mô tả |
| --- | --- |
| [Refund Agent (A365)](refund-agent-a365/) | Sample A365/Foundry với Fabric, Foundry IQ, Work IQ và dashboard React/Vite |
| [Travel Agent (Hosted)](travel-agent-hosted/) | Sample hosted Foundry Agent Framework dùng chính sách du lịch, Work IQ Mail và evaluations |

Tài liệu học từ Microsoft: [The Microsoft IQ Series](https://github.com/microsoft/iq-series), [Build Lab 532](https://github.com/microsoft/Build26-LAB532), [IQ Discussions](https://aka.ms/iq/discussions). Các sample có yêu cầu cài đặt riêng trong README tương ứng.

## Contributing

For this fork, open an issue or pull request in this repository. The paragraphs below
preserve the upstream Microsoft contribution and CLA procedures for attribution; they
do not promise that Microsoft's CLA bot runs on this fork.

This project welcomes contributions and suggestions. Most contributions require you to agree to a
Contributor License Agreement (CLA) declaring that you have the right to, and actually do, grant us
the rights to use your contribution. For details, visit [Contributor License Agreements](https://cla.opensource.microsoft.com).

When you submit a pull request, a CLA bot will automatically determine whether you need to provide
a CLA and decorate the PR appropriately (e.g., status check, comment). Simply follow the instructions
provided by the bot. You will only need to do this once across all repos using our CLA.

This project has adopted the [Microsoft Open Source Code of Conduct](https://opensource.microsoft.com/codeofconduct/).
For more information see the [Code of Conduct FAQ](https://opensource.microsoft.com/codeofconduct/faq/) or
contact [opencode@microsoft.com](mailto:opencode@microsoft.com) with any additional questions or comments.

## Trademarks

This project may contain trademarks or logos for projects, products, or services. Authorized use of Microsoft
trademarks or logos is subject to and must follow
[Microsoft's Trademark & Brand Guidelines](https://www.microsoft.com/legal/intellectualproperty/trademarks/usage/general).
Use of Microsoft trademarks or logos in modified versions of this project must not cause confusion or imply Microsoft sponsorship.
Any use of third-party trademarks or logos are subject to those third-party's policies.
