# Eldorado Repricer

Tool tự động chỉnh giá offer trên Eldorado.gg theo đối thủ **nội bộ Eldorado**
(không còn so sánh G2G/FunPay). Viết lại từ bản gốc tại
`C:\Users\Admin\Downloads\src`, xem chi tiết kiến trúc/lý do thay đổi trong
plan đã duyệt.

## Cài đặt

```
pip install -r requirements.txt
```

## Cấu hình

1. Copy `.env.example` thành `.env`.
2. Điền các giá trị THẬT vào `.env` (Claude không tự điền giúp — đây là
   credential thật của bạn):
   - `ELDORADO_EMAIL`/`ELDORADO_PASSWORD`: tài khoản seller Eldorado.
   - `ELDORADO_COGNITO_POOL_ID`/`ELDORADO_COGNITO_CLIENT_ID`: lấy từ bản gốc
     (`const.py` cũ, biến `user_pool_id`/`client_id`) hoặc tự tra lại nếu tài
     khoản đổi.
   - `GOOGLE_SERVICE_ACCOUNT_JSON`: dán nguyên văn nội dung 1 file service
     account `.json` (đã share quyền Editor vào mọi sheet cần đọc/ghi — sheet
     cấu hình chính + mọi sheet MIN/MAX/STOCK1/STOCK2/blacklist mà các dòng
     sản phẩm tham chiếu tới).
   - `SHEET_CONFIG_ID`/`CONFIG_RANGE`: giữ nguyên từ bản gốc (`config.ini`
     cũ, mục `[Sheets]`) nếu muốn dùng lại đúng sheet đang có.

## Chạy

```
python main.py
```

Mặc định `DRY_RUN=true` — chỉ tính toán + ghi log/note lên sheet, **KHÔNG ghi
giá thật lên Eldorado**. Xem vài chu kỳ chạy, thấy giá tính ra hợp lý rồi mới
đổi `DRY_RUN=false` trong `.env`.

## Test

```
pip install pytest pytest-asyncio
pytest tests/ -v
```

## Cấu trúc cột sheet (schema PHẲNG — 20 cột)

Không còn tham chiếu chéo sang sheet khác (khác thiết kế ban đầu của bản gốc)
— mọi giá trị nằm THẲNG trong dòng sản phẩm, giống style tool G2G Repricer
sibling. Định nghĩa DUY NHẤT nằm trong `sheet_schema.py` (tên cột nội bộ +
nhãn hiển thị tiếng Anh ngắn gọn ở dòng 1 + chú thích chi tiết gắn vào từng ô
header — nhân viên rê chuột vào ô header trên Google Sheets sẽ thấy giải
thích đầy đủ). Chạy `python scripts/setup_sheet_headers.py` để tự động thiết
lập đúng 20 cột này cho 1 tab mới — script sẽ:
1. XOÁ SẠCH dữ liệu cũ trong tab (chỉ chạy khi chắc chắn muốn reset),
2. Ghi nhãn + chú thích cho dòng 1,
3. Đặt **checkbox thật** (Data Validation kiểu BOOLEAN) cho cột `Enabled`
   (dòng 2-1000) — nhân viên tích/bỏ tích thay vì gõ tay `1`.

**Quan trọng:** code đọc dữ liệu theo VỊ TRÍ CỘT (thứ tự trong
`sheet_schema.COLUMNS`), KHÔNG theo chữ hiển thị ở dòng 1 — nên bạn có thể tự
sửa lại nhãn tiếng Việt cho dễ hiểu hơn nữa mà không sợ hỏng tool, miễn KHÔNG
tự ý thêm/xoá/đổi thứ tự cột (muốn đổi thứ tự cột thật sự thì sửa
`sheet_schema.py` rồi chạy lại script thiết lập).

Xem đầy đủ 20 cột + giải thích trong `sheet_schema.py` (`COLUMNS`), hoặc mở
sheet thật và rê chuột vào từng ô header. Tóm tắt nhanh: `Enabled` (checkbox
bật/tắt dòng), `Name`, 3 cột tool tự ghi (`Status`/`Updated At`/`New Link`),
`My Listing URL`/`Compare URL`, `Stock`, `Price Min`/`Price Max`,
`Discount`/`Round Decimals`/`Always Undercut` (checkbox), `Min Purchase
Base`/`Min Purchase Step`, 3 cột lọc đối thủ (`Min Competitor Stock`, `Min
Competitor Ratings`, `Min Feedback %`), `Seller Blacklist`, và `Allow
Recreate on Rate Limit` (checkbox).

**Đã bỏ (theo yêu cầu 2026-09-13, không dùng tới hiện tại):** lọc đối thủ
theo thời gian giao hàng (`Max Guaranteed/Average Delivery`), lọc theo từ
khoá tiêu đề (`Exclude/Require Keywords`), và toàn bộ logic tự đổi thời
gian giao hàng theo tồn kho (`Stock Limit`, `Delivery (In Stock)`/`Delivery
(Out of Stock)`). Cả cột sheet lẫn code liên quan (`filter_competitors`'s
guaranteed_time/average_time/keys_exclude/keys_include params,
`pricing.decide_stock_and_delivery`, `models.StockDecision`) đã bị xoá khỏi
code hiện tại — cần lại thì khôi phục từ lịch sử git (xem
eldorado_repricer_project.md để biết đúng commit/thời điểm).

## Khác gì so với bản gốc (`C:\Users\Admin\Downloads\src`)

- **Bỏ hẳn G2G/FunPay** — chỉ còn so giá với đối thủ khác trên chính
  Eldorado.
- **Bỏ HWID/license gate, tự update GitHub, báo Discord** — chỉ còn ý nghĩa
  khi phân phối cho người khác dùng.
- **Bỏ hẳn cơ chế tham chiếu chéo sheet khác** (IDSHEET/SHEET/CELL của bản
  gốc) — mọi giá trị (STOCK/PRICE_MIN/PRICE_MAX...) nhập thẳng trong dòng sản
  phẩm, đơn giản hơn nhiều và không cần đọc thêm sheet nào khác nữa.
- **`asyncio` thay `ThreadPoolExecutor`** + **1 Service Account duy nhất**
  (không xoay vòng nhiều SA theo thread) — vì đã bỏ tham chiếu chéo nên áp
  lực quota Sheets cũng giảm hẳn, không cần nhiều SA nữa.
- **`Decimal` thay `float`** cho mọi phép tính giá — tránh sai số dấu phẩy
  động ở nhiều chữ số thập phân.
- **Luôn làm tròn giá XUỐNG** (giữ đúng hướng bản gốc đã làm đúng) — không
  bao giờ vô tình đẩy giá cao hơn giá đối thủ vừa tính (bug đã gặp và sửa ở 1
  tool sibling dùng chung logic tương tự cho G2G).
- **Đơn giản hoá logic đồng bộ stock**: luôn đồng bộ đúng số `STOCK` từ
  sheet lên Eldorado khi khác giá trị hiện tại (bỏ ngưỡng "né spam cập nhật
  nhỏ lẻ" `STOCK_MIN_UP`/`ISUPDATE_STOCK` của bản gốc — nếu cần lại, báo để
  thêm).
- Có `timeout` rõ ràng cho mọi request, và có unit test (`tests/`) — bản gốc
  không có test nào.

## Xử lý lỗi 429 (quá tải — "Whoa! Calm down, cowboy!")

Khi endpoint đổi giá/update của Eldorado trả về 429, tool tự **xoá offer cũ
+ tạo offer mới với GIÁ MỚI luôn** (chỉ áp dụng nếu bật `Allow Recreate on
Rate Limit`) — copy nguyên Description/thời gian giao/thuộc tính của offer
cũ. Theo xác nhận trực tiếp: **tạo offer mới không bao giờ bị 429** (chỉ 2
endpoint đổi giá/update mới bị) nên chỉ cần đúng 1 lần tạo là xong, KHÔNG có
retry loop nhiều lần trong `writer.py`.

**Quan trọng:** vì offer mới có ID khác hẳn offer cũ, cột `My Listing URL`
trên sheet sẽ được **tự động ghi đè sang link mới** ngay khi tạo lại thành
công (`sheets_client.write_result`) — nếu không làm vậy, chu kỳ chạy TIẾP
THEO sẽ vẫn cố đọc offer CŨ (đã bị xoá) và báo lỗi "Không tìm thấy ID sản
phẩm" thay vì tiếp tục theo dõi đúng offer. Nếu 1 chu kỳ sau đó LẠI gặp 429,
tool sẽ lặp lại đúng quy trình này (xoá + tạo lại lần nữa) — không giới hạn
số lần lặp lại giữa CÁC CHU KỲ khác nhau, chỉ không lặp lại NHIỀU LẦN trong
CÙNG 1 lần gọi `apply_update`.

Xem `tests/test_writer.py` cho bộ test giả lập đầy đủ luồng này (không thể
test an toàn bằng cách cố tình làm tài khoản thật bị 429).

## Lưu ý QUAN TRỌNG về để trống Price Min (đã xác nhận qua 2 lần review độc lập)

Để trống `Price Min` ở 1 sản phẩm gây ra **2 hệ quả**, không chỉ 1:

1. Tool coi MỌI đối thủ là "hợp lệ" để tính giá — kể cả seller có giá cực
   thấp bất thường (xem `tests/test_product_pipeline.py`
   `test_process_product_floor_none_disables_floor_filtering_entirely`).
2. **Nghiêm trọng hơn:** toàn bộ nhánh bám giá đối thủ trong
   `pricing.calculate_new_price` bị BỎ QUA — giá sẽ đứng yên hoặc nhảy thẳng
   lên `Price Max` (nếu có), dù tool vẫn tìm thấy và hiển thị tên đối thủ rẻ
   nhất trong log/Status (dễ gây hiểu lầm là đã bám giá theo họ, nhưng thực
   ra KHÔNG).

**Luôn điền `Price Min`** cho mọi sản phẩm muốn tool tự bám giá đối thủ —
nếu thực sự không muốn giới hạn giá sàn, điền `0` (không phải để trống).
