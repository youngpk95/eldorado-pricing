HƯỚNG DẪN — Credential Google Service Account

1. Vào Google Cloud Console, tải file .json của Service Account (đã share
   quyền Editor vào sheet cấu hình chính, và Viewer vào mọi sheet ngoài nếu
   dùng tính năng "Price Min từ sheet khác").
2. Bỏ NGUYÊN VĂN file .json đó vào folder này (service_account/), giữ
   nguyên tên file, KHÔNG cần mở ra sửa/convert gì cả.
3. Chỉ được để ĐÚNG 1 file .json trong folder này — nếu có nhiều hơn 1, tool
   sẽ báo lỗi rõ ràng khi khởi động, yêu cầu xoá bớt.

Không cần điền gì vào .env cho bước này (folder này thay thế hoàn toàn cho
việc dán nội dung JSON thành 1 dòng vào biến GOOGLE_SERVICE_ACCOUNT_JSON của
.env — cách cũ vẫn còn hoạt động nếu bạn thích, nhưng không cần dùng nữa).

Mọi file .json trong folder này KHÔNG bị commit lên git (xem .gitignore) —
đây là secret thật, không phải code.
