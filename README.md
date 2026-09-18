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
   - **Service account:** bỏ NGUYÊN VĂN file `.json` tải từ Google Cloud
     Console vào folder `service_account/` (giữ nguyên tên file, không cần
     sửa/convert gì) — xem `service_account/README.txt`. Không cần điền gì
     vào `.env` cho bước này (`GOOGLE_SERVICE_ACCOUNT_JSON` chỉ dùng nếu
     thật sự muốn cách cũ: dán JSON thành 1 dòng hoặc ghi đường dẫn file).
     Service account phải có quyền Editor vào sheet cấu hình chính; nếu dùng
     Price Min/Price Max từ sheet khác — xem mục "Price Min/Price Max từ
     sheet khác" bên dưới — cũng phải share quyền Viewer cho đúng email
     service account vào TỪNG sheet ngoài đó.
   - `SHEET_CONFIG_ID`/`CONFIG_RANGE`: giữ nguyên từ bản gốc (`config.ini`
     cũ, mục `[Sheets]`) nếu muốn dùng lại đúng sheet đang có.
   - `GIT_BRANCH`/`UPDATE_CHECK_INTERVAL_SECONDS`: xem mục "Tự cập nhật từ
     GitHub" bên dưới — có giá trị mặc định hợp lý, thường không cần sửa.

## Chạy

```
python main.py
```

Mặc định `DRY_RUN=true` — chỉ tính toán + ghi log/note lên sheet, **KHÔNG ghi
giá thật lên Eldorado**. Xem vài chu kỳ chạy, thấy giá tính ra hợp lý rồi mới
đổi `DRY_RUN=false` trong `.env`.

## Xem lại log sau khi chạy không giám sát (qua đêm, tắt máy...)

Mỗi lần chạy, tool ghi log ra **CẢ console lẫn file** `logs/eldorado_repricer.log`
(tự xoay vòng khi quá 5MB, giữ tối đa 5 file cũ) — đóng terminal hay tắt máy
không làm mất lịch sử, quay lại lúc nào cũng xem được. Cách xem nhanh riêng
phần LỖI (không cần đọc hết cả file dài):

```
python scripts/show_errors.py
```

File log nằm trong `.gitignore` (không commit lên git) vì đây là dữ liệu vận
hành, không phải code.

## Test

```
pip install pytest pytest-asyncio
pytest tests/ -v
```

## Price Min/Price Max từ sheet khác (không dùng IMPORTRANGE)

Nếu Price Min và/hoặc Price Max thật sự nằm ở 1 Google Sheet KHÁC (không
phải sheet cấu hình chính), điền các cột `Link Sheet` / `Name Sheet` /
`Cell Min` / `Cell Max` ở cuối bảng cho dòng đó — tool sẽ tự đọc TRỰC TIẾP
qua Google Sheets API (không phải công thức `IMPORTRANGE`, nên không bị
delay/lag). Để trống thì hành vi y hệt như trước: dùng thẳng cột `Price
Min`/`Price Max` tại chỗ.

- `Link Sheet`: URL đầy đủ (hoặc thẳng spreadsheet ID) của sheet khác đó.
- `Name Sheet`: tên tab (sheet name) chứa Price Min/Price Max bên trong file
  đó — dùng chung cho cả Min lẫn Max (2 giá trị nằm trong CÙNG 1 sheet
  ngoài, chỉ khác ô).
- `Cell Min`: ô chứa giá Price Min, vd `B5`. Để trống nếu dòng này chỉ cần
  lấy Price Max từ sheet ngoài.
- `Cell Max`: ô chứa giá Price Max, vd `B6`. Để trống nếu dòng này chỉ cần
  lấy Price Min từ sheet ngoài.

Bật external cho Min và Max **độc lập nhau** — 1 dòng có thể chỉ bật 1
trong 2 (điền `Cell Min`, để trống `Cell Max`, hoặc ngược lại), hoặc cả hai.

**Bắt buộc:** sheet ngoài đó phải được **share quyền Viewer** cho đúng email
service account đang dùng (`client_email` trong file `.json` ở
`service_account/`), nếu không tool sẽ không đọc được.

Tool gom đọc **1 lần cho cả chu kỳ** (không đọc riêng từng dòng/từng giá
trị) để tránh tốn quota. Nếu đọc lỗi (chưa share quyền, sai tên tab, sai
ô...), tool tự **fallback về giá trị cột `Price Min`/`Price Max` tại chỗ**
(nếu có) để dòng không bị treo hoàn toàn, đồng thời ghi cảnh báo rõ ràng vào
`Status` để biết mà sửa — Min và Max fallback ĐỘC LẬP nhau, 1 cái lỗi không
kéo cái kia theo.

## Tự cập nhật từ GitHub

Sau mỗi chu kỳ chạy (điểm an toàn — không có sản phẩm nào đang xử lý dở), tool
tự `git fetch` kiểm tra nhánh `GIT_BRANCH` (mặc định `master`) trên remote
`origin`; nếu có commit mới, tool tự `git pull --ff-only` rồi tự khởi động
lại process để dùng ngay code mới — không cần tắt/mở tay. Tần suất kiểm tra
theo `UPDATE_CHECK_INTERVAL_SECONDS` (mặc định 300s = 5 phút), không kiểm
tra mỗi chu kỳ để đỡ tốn lệnh `git fetch`.

Không cần điền `GITHUB_TOKEN` gì trong `.env` — tool dùng lại chính git
credential đã cache sẵn trên máy (từ lần `git push` thủ công đầu tiên lên
repo GitHub, xem `updater.py`). Nếu `git pull` thất bại (vd có thay đổi cục
bộ chưa commit, mất mạng, lịch sử đã phân nhánh...), tool chỉ log lỗi và thử
lại ở chu kỳ kiểm tra sau, KHÔNG bao giờ tự restart khi chưa chắc code mới
đã pull thành công trọn vẹn (dùng `--ff-only`, không bao giờ tự tạo merge
commit hay đè conflict âm thầm).

## Cấu trúc cột sheet (schema PHẲNG — 25 cột)

Không còn tham chiếu chéo tràn lan sang sheet khác như thiết kế ban đầu của
bản gốc — mọi giá trị nằm THẲNG trong dòng sản phẩm (ngoại lệ duy nhất: 4
cột `Link Sheet`/`Name Sheet`/`Cell Min`/`Cell Max` để đọc riêng Price
Min/Price Max từ sheet khác qua Sheets API, xem mục "Price Min/Price Max từ
sheet khác" bên trên — vẫn CHỦ ĐỘNG điền mới kích hoạt, khác cơ chế tham
chiếu chéo toàn diện đã bỏ). Định
nghĩa DUY NHẤT nằm trong `sheet_schema.py` (tên cột nội bộ + nhãn hiển thị
tiếng Anh ngắn gọn ở dòng 1 + chú thích chi tiết gắn vào từng ô header —
nhân viên rê chuột vào ô header trên Google Sheets sẽ thấy giải thích đầy
đủ). Chạy `python scripts/setup_sheet_headers.py` để tự động thiết lập đúng
25 cột này cho 1 tab mới — script sẽ:
1. XOÁ SẠCH dữ liệu cũ trong tab (chỉ chạy khi chắc chắn muốn reset),
2. Ghi nhãn + chú thích cho dòng 1,
3. Đặt **checkbox thật** (Data Validation kiểu BOOLEAN) cho cột `Enabled`
   (dòng 2-1000) — nhân viên tích/bỏ tích thay vì gõ tay `1`.

**Quan trọng (đổi từ 2026-09-14):** code đọc/ghi theo ĐÚNG TÊN header ở dòng
1 (phải khớp CHÍNH XÁC label trong `sheet_schema.COLUMNS`, vd `Price Min`,
`Stock`...), KHÔNG còn theo vị trí/thứ tự cột nữa — nên bạn có thể tự chèn,
xoá, hoặc đổi thứ tự cột bất kỳ đâu trên sheet mà KHÔNG sợ hỏng tool. Ngược
lại: **không được tự đổi tên header** ở dòng 1 (hoặc gõ sai chính tả khi tự
thêm cột) — sai tên sẽ khiến tool báo lỗi rõ ràng ngay khi đọc sheet (thiếu
cột bắt buộc), thay vì đọc nhầm dữ liệu cột khác một cách âm thầm như bug
thật đã gặp trước đây (chèn 4 cột `Link Sheet`/`Name Sheet`/`Cell Min`/`Cell
Max` vào giữa bảng làm lệch mọi cột phía sau).

Xem đầy đủ 25 cột + giải thích trong `sheet_schema.py` (`COLUMNS`), hoặc mở
sheet thật và rê chuột vào từng ô header. Tóm tắt nhanh: `Enabled` (checkbox
bật/tắt dòng), `Name`, 3 cột tool tự ghi (`Status`/`Updated At`/`New Link`),
`My Listing URL`/`Compare URL`, `Stock`, `Min Stock Update`, `Price Min`/`Price Max`,
`Discount`/`Round Decimals`/`Always Undercut` (checkbox), `Min Purchase
Base`/`Min Purchase Step`, 3 cột lọc đối thủ (`Min Competitor Stock`, `Min
Competitor Ratings`, `Min Feedback %`), `Seller Blacklist`, `Exclude
Keywords`/`Require Keywords` (lọc đối thủ theo từ khoá trong tiêu đề offer),
`Allow Recreate on Rate Limit` (checkbox), `Relax (seconds)`, và 4 cột `Link
Sheet`/`Name Sheet`/`Cell Min`/`Cell Max` (đọc Price Min/Price Max từ sheet
khác, để trống nếu không cần).

**Lưu ý về `Min Stock Update`:** để trống thì tool luôn đồng bộ `Stock` mỗi
khi khác số lượng đang live trên listing (hành vi mặc định). Điền số vào
đây thì tool CHỈ thực sự ghi update stock lên Eldorado khi stock đang live
xuống DƯỚI số này — còn >= số này thì bỏ qua phần stock (Price/Min Purchase
Base vẫn tính/update độc lập, không bị ảnh hưởng). Port lại từ ngưỡng
`STOCK_MIN_UP` của tool cũ, dùng để tránh gọi API update thừa khi stock vẫn
còn dư dả.

**Lưu ý về `Relax (seconds)`:** đây là cấu hình cho CẢ VÒNG CHẠY (nghỉ sau
khi xong HẾT sản phẩm đang bật), không phải riêng 1 sản phẩm — nếu nhiều
sản phẩm đang bật có số khác nhau, tool tự lấy số LỚN NHẤT
(`main._compute_loop_delay`). Để trống ở mọi dòng thì dùng mặc định
`LOOP_DELAY_SECONDS` trong `.env`.

**Đã bỏ (theo yêu cầu 2026-09-13, không dùng tới hiện tại):** lọc đối thủ
theo thời gian giao hàng (`Max Guaranteed/Average Delivery`), và toàn bộ
logic tự đổi thời gian giao hàng theo tồn kho (`Stock Limit`, `Delivery (In
Stock)`/`Delivery (Out of Stock)`). Cả cột sheet lẫn code liên quan
(`filter_competitors`'s guaranteed_time/average_time params,
`pricing.decide_stock_and_delivery`, `models.StockDecision`) đã bị xoá khỏi
code hiện tại — cần lại thì khôi phục từ lịch sử git (xem
eldorado_repricer_project.md để biết đúng commit/thời điểm). Lọc theo từ
khoá tiêu đề (`Exclude/Require Keywords`) đã được **thêm lại** ngày
2026-09-15 — xem cột `Exclude Keywords`/`Require Keywords` ở trên.

## Khác gì so với bản gốc (`C:\Users\Admin\Downloads\src`)

- **Bỏ hẳn G2G/FunPay** — chỉ còn so giá với đối thủ khác trên chính
  Eldorado.
- **Bỏ HWID/license gate, báo Discord** — chỉ còn ý nghĩa khi phân phối cho
  người khác dùng. **Tự update GitHub thì đã thêm lại** (khác cách làm bản
  gốc: dùng `git pull --ff-only` + tự restart thay vì tải/thay thế file
  `.exe` — xem mục "Tự cập nhật từ GitHub" bên trên) vì tool giờ chạy như
  script Python, không phải file `.exe` đóng gói phân phối.
- **Bỏ cơ chế tham chiếu chéo TOÀN DIỆN sang sheet khác** (IDSHEET/SHEET/CELL
  áp dụng cho MỌI cột của bản gốc) — mọi giá trị (STOCK...) nhập thẳng trong
  dòng sản phẩm. **Ngoại lệ đã thêm lại sau đó:** riêng Price Min/Price Max
  có thể đọc từ sheet khác qua 4 cột `Link Sheet`/`Name Sheet`/`Cell
  Min`/`Cell Max` (xem mục "Price Min/Price Max từ sheet khác" bên trên) —
  đọc thẳng qua Sheets API, không phải qua `IMPORTRANGE` như cách làm tay
  trước đó, nên không bị lag.
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
