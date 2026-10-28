# Enterprise Agent

Ứng dụng chat dùng Gemini để tổng hợp tài liệu, đọc Microsoft 365 qua Work IQ theo quyền tài khoản đã đăng nhập, và tạo Word, PDF, Excel, PowerPoint, Markdown hoặc JSON.

Với **dịch vụ dùng chung**, chủ dịch vụ cấu hình backend, Gemini key và ứng dụng Microsoft Entra một lần. Người dùng mở địa chỉ HTTPS được cung cấp rồi đăng nhập Microsoft bằng tài khoản cơ quan/trường học. Người dùng không cần clone mã nguồn, cài Python hay nhập key. Các tổ chức khác có thể cần quản trị viên chấp thuận ứng dụng và quyền Work IQ trước khi sử dụng.

## Dành cho người dùng

1. Mở URL HTTPS do chủ dịch vụ cung cấp, chọn **Kết nối Microsoft → Đăng nhập Microsoft**.
2. Dùng tài khoản cơ quan/trường học và cho phép popup nếu được hỏi. Nếu cần phê duyệt tổ chức, liên hệ quản trị viên.
3. Hỏi về thông tin bạn được phép đọc hoặc tạo bản nháp. Nếu dịch vụ cho phép tải tài liệu, có thể chọn tệp/thư mục trên máy.
4. Xem lại nguồn và nội dung trước khi tải hoặc chia sẻ kết quả.

Xem [chức năng và giới hạn](enterprise-agent/README.md) và [kiến trúc ứng dụng](enterprise-agent/ARCHITECTURE.md).

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
