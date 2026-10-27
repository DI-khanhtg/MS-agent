# Enterprise Agent

Ứng dụng chat dùng Gemini để tổng hợp tài liệu, đọc Microsoft 365 qua Work IQ theo quyền tài khoản đã đăng nhập, và tạo Word, PDF, Excel, PowerPoint, Markdown hoặc JSON.

Với **dịch vụ dùng chung**, chủ dịch vụ cấu hình backend, Gemini key và ứng dụng Microsoft Entra một lần. Người dùng mở địa chỉ HTTPS được cung cấp rồi đăng nhập Microsoft bằng tài khoản cơ quan/trường học. Người dùng không cần clone mã nguồn, cài Python hay nhập key. Các tổ chức khác có thể cần quản trị viên chấp thuận ứng dụng và quyền Work IQ trước khi sử dụng.

## Dành cho người dùng

1. Mở URL HTTPS do chủ dịch vụ cung cấp, chọn **Kết nối Microsoft → Đăng nhập Microsoft**.
2. Dùng tài khoản cơ quan/trường học và cho phép popup nếu được hỏi. Nếu cần phê duyệt tổ chức, liên hệ quản trị viên.
3. Hỏi về thông tin bạn được phép đọc hoặc tạo bản nháp. Nếu dịch vụ cho phép tải tài liệu, có thể chọn tệp/thư mục trên máy.
4. Xem lại nguồn và nội dung trước khi tải hoặc chia sẻ kết quả.

Repository này chưa cung cấp URL dịch vụ đang chạy. `https://agent.example.com` trong tài liệu chỉ là ví dụ; chủ dịch vụ phải triển khai tên miền và HTTPS. Xem [cách sử dụng và giới hạn](enterprise-agent/README.md).

## Dành cho chủ dịch vụ

Làm theo [hướng dẫn host dùng chung](enterprise-agent/README.md#host-dùng-chung): tạo hai app Entra hỗ trợ nhiều tổ chức, cấp quyền/consent, cấu hình secrets trên server và đặt ứng dụng sau HTTPS. Chủ dịch vụ chịu hạn mức và chi phí AI chung. Cấu hình này hỗ trợ một tiến trình/một replica với dữ liệu bền vững; chưa có điều phối nhiều replica.

Nếu cần lấy mã nguồn, cài [Git](https://git-scm.com/downloads) và [uv](https://docs.astral.sh/uv/getting-started/installation/):

```powershell
git clone --branch feat/gemini-microsoft-documents https://github.com/DI-khanhtg/MS-agent.git
cd MS-agent/enterprise-agent
uv run enterprise-agent setup --shared --output .env.shared
docker compose --env-file .env.shared -f compose.shared.yaml up --build
```

Các lệnh trên lấy nhánh `feat/gemini-microsoft-documents`, nơi chứa các cập nhật đang phát triển. `main` cũng đã có Enterprise Agent, nhưng có thể chưa có các thay đổi mới nhất của nhánh này. Nếu đã clone, từ thư mục repository chạy `git fetch origin`, `git switch feat/gemini-microsoft-documents`, rồi `git pull --ff-only` trước khi vào `enterprise-agent`.

Wizard dành cho chủ dịch vụ, hỏi cấu hình AI, Entra và URL HTTPS công khai. `.env.shared` chứa secrets của server, được Git bỏ qua. Compose kèm backend và proxy Caddy tự quản lý HTTPS; chủ dịch vụ cần domain thật, DNS trỏ về server và mở cổng 80/443 theo hướng dẫn. Upload mặc định tắt; thêm `--uploads` vào wizard khi được tổ chức cho phép. Chưa có website được triển khai từ repository này. Chỉ đăng nhập và truy vấn thực bằng tài khoản đã được phép mới xác nhận được kết nối Microsoft 365.

Key Gemini và backend client secret chỉ ở server. Tệp được người dùng tải lên được sao chép tới server và nội dung được đọc có thể gửi tới dịch vụ AI. Lịch sử/tệp được tách theo tenant và tài khoản; chủ dịch vụ quản lý thời hạn lưu, sao lưu và xóa dữ liệu.

## Chạy local hoặc thử demo

Chạy backend riêng vẫn được hỗ trợ qua [hướng dẫn chạy local](enterprise-agent/README.md#chạy-local): dùng `setup`, `doctor`, `start --open`. Đây là lựa chọn cho chủ bản cài đặt local. Fabric, Speech và Foundry là các tích hợp tùy chọn.

Muốn xem giao diện trước khi có key hoặc cấu hình Microsoft:

```powershell
uv run enterprise-agent demo --open
```

Demo dùng dữ liệu mẫu và kết quả mẫu xác định, không gọi Gemini hoặc Microsoft IQ. Xem [README ứng dụng](enterprise-agent/README.md) để biết giới hạn đọc tài liệu và kiểm thử.

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
