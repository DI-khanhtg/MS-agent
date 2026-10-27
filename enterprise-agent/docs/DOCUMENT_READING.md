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

Bạn có thể dán trực tiếp link chia sẻ, ví dụ:

> Đọc file PowerPoint trong link SharePoint này, tóm tắt và tạo bản Word cho tôi.

Agent dùng `workiq_resolve_document_link` để phân giải link thành file cụ thể, rồi đọc
qua `workiq_read_document`. Không cần file xuất hiện trong chỉ mục tìm kiếm và không tiêu
hai lượt tìm kiếm theo tên. Cần link HTTPS của SharePoint/OneDrive tổ chức hoặc `1drv.ms`;
link thư mục được báo rõ để bạn chọn file cụ thể. Quyền file và chính sách tenant vẫn áp dụng.

Trước khi gửi câu hỏi sang Gemini, backend thay link hợp lệ bằng `link_id` riêng trong lượt
chat để token chia sẻ và ID số không bị gửi nguyên dạng hoặc bị che hỏng. Tối đa tám link
được xử lý trong một lượt; các link vượt số này được thay bằng thông báo giới hạn. Mã hết
hạn cuối lượt; khi cần đọc lại ở lượt sau, dán lại link. Link gốc trong tin nhắn người dùng
vẫn chịu chính sách lưu lịch sử hiện có của ứng dụng.

File đã phân giải có kích thước trên 4 MB dùng đường tải Graph riêng, tối đa **20 MB**,
vì Work IQ `fetch_blob` có giới hạn mặc định 4 MB. Đường này cần **Microsoft Graph →
Delegated → Files.Read** và consent trên **app backend có ID bằng `AZURE_CLIENT_ID`**.
Không phải app SPA có `ENTRA_SPA_CLIENT_ID`. Xem [cấu hình tải file lớn](WORKIQ_SETUP.md#large-linked-files-and-microsoft-graph).
Đăng nhập Microsoft thành công không tự cấp quyền API này.

Đường tải Graph chỉ dùng token Graph tại `graph.microsoft.com`; token Work IQ không
được dùng thay thế. Các bước chuyển hướng tải file được kiểm tra từng host, bỏ bearer và
cookie, giới hạn số hop và số byte. URL tải tạm không gửi tới Gemini hay log HTTP.
Giữ nguyên giới hạn 200 trang/slide, 50 MB Office giải nén và 100000 ký tự nguồn; PDF
scan/ảnh vẫn chưa có OCR. File lớn vẫn đọc native PPTX/DOCX/XLSX, không tự chuyển sang PDF.

Gửi yêu cầu kèm tên/vị trí tệp, ví dụ:

> Tìm objectives.pptx trong OneDrive của tôi và tóm tắt từng slide.

Với tên file/chủ đề hoặc file người khác chia sẻ, agent dùng `workiq_search_documents`
để tìm trong chỉ mục OneDrive/SharePoint theo quyền của người đăng nhập. File có thể nằm
trong drive của chủ sở hữu khác; danh sách thư mục gốc của bạn không phải mục **Đã chia sẻ**.
Kết quả tìm kiếm không phải danh sách đầy đủ và có thể chưa phản ánh tài liệu mới được chia sẻ.

Agent lấy metadata để xác định drive/item, sau đó dùng `workiq_read_document` để đọc
bytes thực qua `fetch_blob`, áp dụng cùng bộ trích xuất và egress filter như tệp local.
Liệt kê tên tệp chưa phải là đọc nội dung. Quyền truy cập vẫn theo tài khoản đã đăng nhập.

Adapter chỉ tải từ đường dẫn tài nguyên Microsoft365 được cho phép, không mở tùy ý
download URL. Work IQ có giới hạn tải riêng (mặc định4MB, có thể khác theo tenant/path),
theo [Microsoft tool reference](https://learn.microsoft.com/en-us/microsoft-365/copilot/extensibility/work-iq/mcp/tool-reference#fetch_blob).
Giới hạn mặc định bộ đọc là5MB dữ liệu nguồn; file đã phân giải qua link dùng đường Graph
có thể tới20MB. Giới hạn thấp hơn của từng đường tải vẫn áp dụng. Upload trình duyệt giữ2MB.

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

Để tóm tắt toàn bộ file nhanh hơn, công cụ OneDrive cho phép `max_chars=14000`. Nội dung
trả về gồm `text` và các đoạn `continuation` theo thứ tự, mỗi đoạn không vượt giới hạn egress.
Agent theo `next_offset` cho tới khi hết; giới hạn byte có thể làm một lượt trả ít hơn 14000
ký tự, đặc biệt với Unicode. Không được coi phần trích dẫn tìm kiếm là đã đọc toàn bộ tài liệu.

Kết quả tìm kiếm cấp `document_id` riêng trong lượt chat hiện tại. Agent dùng mã này để
đọc file, kể cả khi ID số/tên file bị bộ lọc che; nếu tên thiếu phần mở rộng, backend tự
lấy metadata tên file trước khi đọc. Mã hết hạn khi lượt chat kết thúc, cần tìm lại ở lượt sau.
Văn bản được che thông tin cá nhân trước khi chia đoạn; `offset_basis=sanitized_text`
nghĩa là `total_chars`/`next_offset` đếm ký tự sau khi che. Luôn theo offset được trả về.

Tối ưu hiện tại không cần vector database hay sao chép cả kho tài liệu:

- Tìm tên/chủ đề và người chia sẻ trước, chỉ tải file phù hợp; tối đa hai truy vấn Work IQ
  thực mỗi lượt chat. Kết quả mặc định tối đa năm file, có thể chọn từ một đến mười.
- Trong cùng lượt chat, truy vấn thành công giống nhau dùng lại kết quả; các đoạn của cùng
  file dùng lại lần tải/trích xuất đầu tiên. Bộ nhớ tạm RAM giữ tối đa tám mục, tách theo
  tenant/tài khoản/token và được bỏ khi lượt chat kết thúc hoặc bị hủy. Lượt sau kiểm tra
  lại quyền và dữ liệu, không dùng cache của tài khoản khác.
- Không tự gọi lại truy vấn tìm kiếm bị timeout để tránh phí lặp. Giới hạn số lượt tìm
  không phải hạn mức tiền; kết quả và chi phí thực tế tùy dịch vụ Microsoft và Gemini.
- Lỗi tìm kiếm/quyền/billing phải được báo rõ; kết quả rỗng không chứng minh file không
  tồn tại hay người dùng không có quyền. Khi cần, cung cấp liên kết/vị trí file cụ thể.

Tìm kiếm dùng `retrieve` (public preview), cần tenant bật Work IQ, consent delegated
`WorkIQAgent.Ask` và người gọi được gán spending policy/Copilot Credits. Có phí Microsoft
riêng ngoài Gemini; đăng nhập thành công chưa xác nhận billing đã sẵn sàng.
Xem [hướng dẫn chính thức của Microsoft](https://learn.microsoft.com/en-us/microsoft-365/copilot/extensibility/work-iq/mcp/retrieve).

Upload WAV được chuyển vào phần **Kết nối Microsoft → Giọng nói**. Kẹp giấy trong chat
hiện dành cho tài liệu.
