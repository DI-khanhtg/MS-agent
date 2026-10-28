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

## Chức năng

- Đọc tài liệu Microsoft 365 theo quyền người đăng nhập và xử lý link chia sẻ OneDrive/SharePoint.
- Liệt kê thư mục và thư mục con, đọc nhiều tài liệu trong một lần gọi.
- Phân tích nội dung và tạo DOCX, PPTX, XLSX, PDF, Markdown hoặc JSON qua chat.
- Reviewer kiểm tra bản nháp theo bằng chứng; writer biên tập nội dung. Hai công cụ chỉ nhận dữ liệu đã qua bộ lọc egress và không tự truy cập nguồn.
- Lưu lịch sử, phiên agent và tài liệu theo tenant/tài khoản.

Xem [kiến trúc ứng dụng](ARCHITECTURE.md) để biết các thành phần và luồng xử lý.

## Dữ liệu và giới hạn

- Microsoft 365 được đọc theo quyền người đăng nhập; ứng dụng không tự cấp quyền vào tài liệu người khác.
- Tìm tài liệu cá nhân/được chia sẻ qua chỉ mục OneDrive/SharePoint cần Work IQ `retrieve` và điều kiện Copilot Credits của tenant. Mỗi lượt chat tối đa hai truy vấn tìm kiếm; chỉ mục không bảo đảm bao phủ mọi file.
- Hỗ trợ TXT/MD/CSV/JSON UTF-8, DOCX, PPTX, PDF có chữ và XLSX. Chưa có OCR cho PDF scan; định dạng `.doc`, `.ppt`, `.xls` cần chuyển đổi.
- Link thư mục SharePoint/OneDrive được liệt kê đệ quy (mặc định 3 cấp, tối đa 5; tối đa 200 mục và 40 thư mục con mỗi lượt), rồi đọc nội dung các file được chọn, mỗi lượt gọi tối đa 6 file. Có Graph `Files.Read` thì liệt kê đầy đủ thư mục lớn; nếu không, chỉ 25 mục đầu mỗi thư mục và agent sẽ báo là chưa đủ.
- Upload qua trình duyệt tối đa 2 MB/tệp; PDF tối đa 200 trang, PPTX tối đa 200 slide. File qua link trên 4 MB dùng Graph riêng, với giới hạn ứng dụng 20 MB.
- Lịch sử, phiên và artifact nằm trên máy chạy backend, tách theo tenant/tài khoản. Upload tạo bản sao trên backend; đăng xuất không xóa dữ liệu đã lưu.
- Nội dung được đọc có thể gửi tới Gemini sau bộ lọc egress. Chủ dịch vụ chịu chi phí Gemini và các dịch vụ Microsoft liên quan.
- Các file cấu hình riêng, database và đầu ra runtime được Git bỏ qua. Giữ key/secret ở backend.
