# Đọc PDF, Word, PowerPoint và Excel

Agent có thể trích xuất và phân tích nội dung văn bản từ các định dạng sau:

| Loại tệp | Phần được đọc |
| --- | --- |
| PDF `.pdf` | Chữ có thể bôi chọn, kèm số trang |
| Word `.docx` | Đoạn văn, bảng/bảng lồng, header và footer |
| PowerPoint `.pptx` | Nội dung chữ, nhóm shape, bảng và ghi chú người trình bày, kèm số slide |
| Excel `.xlsx` | Giá trị ô đã lưu và tên sheet |
| `.txt`, `.md`, `.csv`, `.json` | Văn bản UTF-8 |

## Tệp trên máy

1. Đăng nhập Microsoft tại **http://localhost:8000**.
2. Bấm **biểu tượng kẹp giấy – Đính kèm tài liệu** cạnh ô chat.
3. Chọn một hoặc nhiều PDF, DOCX, PPTX, XLSX hoặc tệp văn bản. Có thể chọn cả thư mục
   qua **Chọn thư mục trên máy** ở bảng bên phải.
4. Đợi giao diện báo nhập thành công. Tên tệp đang chọn xuất hiện phía trên ô chat.
5. Gửi yêu cầu, ví dụ:

   > Tóm tắt các tài liệu vừa đính kèm, nêu yêu cầu chính và trích dẫn trang/slide khi có.

   > Đọc file PowerPoint đã chọn, tổng hợp nội dung từng slide và ghi chú người trình bày.

Tệp được sao chép vào vùng riêng của tài khoản. Chọn tệp không tự gọi Gemini; agent đọc
khi bạn gửi yêu cầu. **Bỏ chọn nguồn** bỏ lựa chọn trong giao diện, không xóa bản sao đã
nhập hoặc nội dung hội thoại trước đó. Đăng xuất xóa lựa chọn và tên tệp đang hiển thị.

Giới hạn hiện tại: 2 MB mỗi tệp nhập từ trình duyệt, số tệp lưu theo tài khoản theo
`LOCAL_MAX_FILES` (mặc định 200). Các tệp được chia lô tải lên có giới hạn; nếu nhập một
phần thất bại, giao diện báo rõ số tệp đã nhận. Tệp ẩn và định dạng không hỗ trợ được bỏ qua.
Chức năng nhập local dùng trong development; bản production tiếp tục dùng nguồn được quản lý.

## Tệp trên OneDrive/SharePoint

Gửi yêu cầu kèm tên/vị trí tệp, ví dụ:

> Tìm objectives.pptx trong OneDrive của tôi và tóm tắt từng slide.

Agent lấy metadata để xác định drive/item, sau đó dùng `workiq_read_document` để đọc
bytes thực qua `fetch_blob`, áp dụng cùng bộ trích xuất và egress filter như tệp local.
Liệt kê tên tệp chưa phải là đọc nội dung. Quyền truy cập vẫn theo tài khoản đã đăng nhập.

Adapter chỉ tải từ đường dẫn tài nguyên Microsoft365 được cho phép, không mở tùy ý
download URL. Work IQ có giới hạn tải riêng (mặc định4MB, có thể khác theo tenant/path),
theo [Microsoft tool reference](https://learn.microsoft.com/en-us/microsoft-365/copilot/extensibility/work-iq/mcp/tool-reference#fetch_blob).
Giới hạn bộ đọc phía ứng dụng là5MB dữ liệu nguồn; giới hạn thấp hơn vẫn áp dụng.

## Phạm vi nội dung và giới hạn

Với tài liệu local, danh sách trả về `document_id` để đọc từng file. Mã này được kiểm tra
trong vùng riêng của tài khoản và không chứa tên hay đường dẫn. Agent dùng mã thay vì tên file
đã bị che bởi bộ lọc thông tin; việc che tên số như `1571309665.pdf` không làm hỏng thao tác đọc.
Nếu thư mục gốc vượt giới hạn số file, mã được cấp khi liệt kê thư mục con vẫn đọc được.
Sau khi server khởi động lại hoặc bộ nhớ mã đầy, cần liệt kê lại thư mục con đó trước khi đọc.

Nếu đọc OneDrive thất bại, agent phân biệt lỗi đường dẫn, tải file/quyền truy cập,
hết thời gian và lỗi trích xuất văn bản. Lỗi đường dẫn hoặc tải file chưa có nghĩa PDF bị hỏng.
Đường dẫn item đã xác định được tự thêm `/content`; agent cần lấy ID thực từ metadata,
không đoán ID từ tên đã bị che `[REDACTED]`.

- **PDF scan/ảnh chụp:** chưa chạy OCR trong phiên bản này. Trang không có chữ sẽ có
  cảnh báo; PDF chỉ có ảnh không được xem là đã đọc được nội dung.
- **Ảnh, biểu đồ, sơ đồ và bố cục trực quan:** chưa phân tích hình ảnh. PowerPoint chỉ có
  ảnh/biểu đồ sẽ báo thiếu chữ; số slide không tự được tính thành nội dung trích xuất.
- **Công thức Excel:** chỉ đọc kết quả cached đã lưu. Không tính công thức hoặc chạy macro;
  kết quả chưa có cache được báo là thiếu.
- **Định dạng cũ `.doc`, `.ppt`, `.xls`:** cần lưu lại thành `.docx`, `.pptx`, `.xlsx`.
- Tệp mã hóa/bảo vệ mật khẩu, hỏng hoặc sai định dạng bị từ chối với thông báo lỗi.
- Mỗi PDF tối đa200 trang, mỗi PPTX tối đa200 slide. Tổng dữ liệu Office ZIP giải nén
  tối đa50MB; văn bản trích xuất tối đa100000 ký tự. Tệp vượt giới hạn cần được chia nhỏ.

Kết quả tool có `source`, `total_chars`, `next_offset`, `empty` và `extraction`:
`format`, `unit_count`, `coverage=text_only`, `warnings`. Agent phải theo các chunk còn
lại, báo thiếu nội dung khi cần và giữ nguồn trích dẫn. Mỗi chunk tối đa3500 ký tự;
giới hạn số tool call/deadline vẫn có thể khiến lượt đọc chỉ hoàn thành một phần.

Upload WAV được chuyển vào phần **Kết nối Microsoft → Giọng nói**. Kẹp giấy trong chat
hiện dành cho tài liệu.
