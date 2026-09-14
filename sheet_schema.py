"""Định nghĩa DUY NHẤT 1 nơi cho toàn bộ cấu trúc cột sheet config sản phẩm:
(internal_key, nhãn hiển thị tiếng Anh ngắn gọn, chú thích tiếng Việt).

QUAN TRỌNG (đổi từ 2026-09-14, xem sheets_client.py): `read_config_rows()`/
`write_result()` giờ đọc/ghi theo TÊN HEADER ở dòng 1 (phải khớp CHÍNH XÁC
label ở đây), KHÔNG còn theo vị trí/thứ tự cột nữa — chèn/xoá/đổi thứ tự cột
bất kỳ đâu trên sheet thật đều AN TOÀN. Ngược lại: đổi TÊN label ở đây mà
KHÔNG đổi luôn tên header thật trên sheet (hoặc gõ sai chính tả khi tự thêm
cột) sẽ làm tool báo lỗi ngay khi khởi động/đọc sheet (thiếu cột bắt buộc) —
đây là thay đổi NGƯỢC với quy tắc cũ (trước đây đổi tên thoải mái, đổi vị
trí thì vỡ ngầm không báo lỗi). Vẫn nên chạy `scripts/setup_sheet_headers.py`
(tab hoàn toàn mới) hoặc `SheetsClient.add_header_column` (tab đã có data)
khi thêm cột mới, để tên header trên sheet luôn khớp đúng chính tả ở đây."""

COLUMNS: list[tuple[str, str, str]] = [
    ("ENABLED", "Enabled", "Tích để tool xử lý dòng này; bỏ tích để bỏ qua. Nếu tích mà vẫn không chạy, kiểm tra Status — có thể do thiếu My Listing URL hoặc Compare URL."),
    ("NAME", "Name", "Tên gợi nhớ, chỉ để bạn dễ nhận biết — không ảnh hưởng tool."),
    ("LAST_STATUS", "Status", "Tool TỰ GHI kết quả lần chạy gần nhất — không nhập tay."),
    ("LAST_UPDATED_AT", "Updated At", "Tool TỰ GHI thời gian chạy gần nhất — không nhập tay."),
    ("NEW_OFFER_LINK", "New Link", "Tool TỰ GHI nếu phải tạo lại offer mới (khi Eldorado báo lỗi 429) — không nhập tay."),
    ("OWN_LISTING_URL", "My Listing URL", "Link tới offer CỦA BẠN trên Eldorado (vào Dashboard > Offers, copy link offer đó)."),
    ("COMPARE_URL", "Compare URL", "Copy link trang sản phẩm đó trên Eldorado (trang khách xem, có danh sách mọi seller) để tool so giá."),
    ("STOCK", "Stock", "Số lượng bạn thực sự đang có để bán."),
    ("PRICE_MIN", "Price Min", "Giá thấp nhất cho phép bán. QUAN TRỌNG: nếu để TRỐNG, tool sẽ KHÔNG bám giá đối thủ nữa cho dòng này (giá đứng yên hoặc nhảy thẳng lên Price Max nếu có) — muốn 'không giới hạn giá sàn' mà vẫn bám giá đối thủ bình thường thì điền 0, đừng để trống."),
    ("PRICE_MAX", "Price Max", "Giá cao nhất cho phép bán — để trống nếu không muốn giới hạn. Lưu ý: nếu dòng này thiếu Price Min hoặc không có đối thủ hợp lệ, tool có thể đặt giá THẲNG bằng đúng số này, không chỉ dùng làm giới hạn trên."),
    ("EXTERNAL_SHEET_LINK", "Link Sheet", "CHỈ điền khi Price Min và/hoặc Price Max THẬT nằm ở 1 Google Sheet KHÁC (không phải sheet này) — dán URL (hoặc thẳng spreadsheet ID) sheet đó vào đây. Để trống nếu đã điền thẳng ở cột 'Price Min'/'Price Max' bên trên (cùng sheet thì không cần các cột Link Sheet/Name Sheet/Cell Min/Cell Max này). Sheet đó phải share quyền Viewer cho đúng email service account đang dùng, nếu không tool sẽ không đọc được."),
    ("EXTERNAL_SHEET_NAME", "Name Sheet", "Tên tab (sheet name, hiện ở dưới cùng file Google Sheets) chứa Price Min/Price Max, bên trong file ở cột 'Link Sheet'."),
    ("EXTERNAL_SHEET_CELL", "Cell Min", "Ô chứa giá Price Min bên trong tab ở cột 'Name Sheet', vd: B5. Để trống nếu dòng này chỉ cần lấy Price Max từ sheet ngoài, không cần Price Min."),
    ("EXTERNAL_SHEET_CELL_MAX", "Cell Max", "Ô chứa giá Price Max bên trong tab ở cột 'Name Sheet' (cùng sheet ngoài với Cell Min, chỉ khác ô), vd: B6. Để trống nếu dòng này chỉ cần lấy Price Min từ sheet ngoài, không cần Price Max."),
    ("DISCOUNT_AMOUNT", "Discount", "Số tiền trừ khỏi giá đối thủ rẻ nhất khi tính giá bám theo họ (vd 0.01 = rẻ hơn họ 1 cent)."),
    ("ROUND_DECIMALS", "Round Decimals", "Làm tròn giá XUỐNG còn bao nhiêu số sau dấu phẩy (vd 2 = tới từng cent)."),
    ("ALWAYS_UNDERCUT", "Always Undercut", "Tích: luôn hạ giá theo đối thủ kể cả khi mình đang rẻ hơn họ. Bỏ tích: giữ nguyên giá nếu mình đã rẻ hơn rồi."),
    ("MIN_PURCHASE_BASE", "Min Purchase Base", "Giá trị (USD) tối thiểu của 1 đơn hàng — để trống nếu không cần tự tính lại số lượng mua tối thiểu."),
    ("MIN_PURCHASE_COEF", "Min Purchase Step", "Số lượng mua tối thiểu sẽ được làm tròn lên tới bội số của số này."),
    ("COMPETITOR_STOCK_MIN", "Min Competitor Stock", "Bỏ qua đối thủ có tồn kho thấp hơn số này khi so giá — để trống nếu không lọc."),
    ("COMPETITOR_MIN_RATING_COUNT", "Min Competitor Ratings", "Bỏ qua đối thủ có ít lượt đánh giá hơn số này — để trống nếu không lọc."),
    ("COMPETITOR_MIN_FEEDBACK_PERCENT", "Min Feedback %", "Bỏ qua đối thủ có % feedback thấp hơn số này — để trống nếu không lọc."),
    ("SELLER_BLACKLIST", "Seller Blacklist", "Tên seller luôn bị loại khỏi so sánh (cách nhau bằng dấu ;). Tool tự động loại thêm 'CNLTeam' dù không ghi ở đây."),
    ("ALLOW_RECREATE_ON_RATE_LIMIT", "Allow Recreate on Rate Limit", "Tích: cho phép tool tự xoá + tạo lại offer khi Eldorado báo lỗi quá tải (429), VÀ tự tạo lại offer (từ dữ liệu backup) khi phát hiện offer đã bị xoá mất (404) — tự cập nhật luôn My Listing URL sang offer mới. Bỏ tích: tool chỉ báo lỗi ở Status, không tự xoá/tạo/tạo lại gì cả."),
    ("RELAX_SECONDS", "Relax (seconds)", "Số giây NGHỈ sau khi chạy xong HẾT TẤT CẢ sản phẩm đang bật, trước khi bắt đầu vòng chạy tiếp theo — đây là cấu hình cho CẢ VÒNG CHẠY, không phải riêng sản phẩm này. Nếu nhiều sản phẩm đang bật có số khác nhau, tool lấy số LỚN NHẤT. Để trống = dùng mặc định trong .env (LOOP_DELAY_SECONDS)."),
]

INTERNAL_KEYS: list[str] = [key for key, _, _ in COLUMNS]
