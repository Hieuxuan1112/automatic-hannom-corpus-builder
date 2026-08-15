# Tìm kiếm và Xây dựng Tự động Kho Ngữ liệu Hán Nôm từ Internet

> *Automatic Retrieval and Construction of a Han-Nom Calligraphy Corpus from the Internet*

Hệ thống tự động **thu thập ảnh thư pháp chữ Hán từ mạng xã hội** và **gán nhãn OCR
chuẩn xác** để xây dựng một kho ngữ liệu (corpus) phục vụ nghiên cứu và huấn luyện mô
hình nhận dạng chữ Hán viết tay. Đây là đề tài khóa luận tốt nghiệp ngành Công nghệ tri
thức — Trường ĐH Khoa học Tự nhiên, ĐHQG TP.HCM.

> ⚠️ Dự án đang phát triển — kho ngữ liệu và pipeline còn được tiếp tục cải thiện.

---

## Bài toán

Văn bản Hán Nôm dạng thư pháp (câu đối, hoành phi, thơ) được chia sẻ rất nhiều trên
mạng xã hội nhưng **phân tán và không có nhãn máy đọc được**, trong khi các hệ OCR phổ
thông nhận dạng kém chữ viết tay thể Hành/Thảo (nét dính liền, nhiều dị thể). Dự án khai
thác một đặc điểm: **người đăng thường gõ lại nội dung chữ trong ảnh ở phần chú thích** —
đây là nguồn "dữ liệu song hành" ảnh ↔ văn bản có sẵn để sinh nhãn mà không cần chuyên
gia gõ tay từng mẫu.

## Kiến trúc hệ thống

```
    Facebook  ──►  (1) Bộ cào dữ liệu (Playwright + chặn bắt GraphQL)
                        con trỏ trạng thái · khử trùng lặp SQLite · tự khôi phục
                              │
                              ▼
                   (2) Bổ sung siêu dữ liệu truy vết nguồn gốc
                              │
                              ▼
    ảnh + chú thích  ──►  (3) PIPELINE CỔNG LỌC GÁN NHÃN
        │
        ├─ Gemini 3.1 Flash-Lite đọc ảnh (Batch API, giảm 50% chi phí) — làm MỐC xác thực
        ├─ Gióng hàng Levenshtein / partial-ratio (chuẩn hoá phồn·giản + chữ dị thể)
        │     └─ điểm ≥ 75 → ĐẠT   |   < 75 → loại (chú thích không khớp ảnh)
        ├─ Nhãn = cắt NGUYÊN VĂN từ chú thích (không dùng chữ do OCR sinh ra)
        └─ Phân dòng theo cột vật lý bằng YOLO + hậu xử lý khử hộp trùng
                              │
                              ▼
                   Kho ngữ liệu có nhãn + truy vết nguồn
```

**Nguyên tắc gán nhãn cốt lõi:** nhãn lấy **100% nguyên văn** từ chú thích của người
đăng; kết quả OCR chỉ dùng làm mốc để xác thực và gióng hàng, không bao giờ là nguồn chữ
của nhãn — nhờ đó tránh được hiện tượng mô hình "bịa" chữ.

## Kết quả

| Chỉ số | Giá trị |
|---|---|
| Bài đăng thô thu thập | 55.404 |
| Bài đưa qua pipeline gán nhãn | 13.071 |
| **Mẫu đạt chuẩn (kho ngữ liệu)** | **10.184** (77,9%) |
| Độ tương đồng Levenshtein trung bình | **98,21%** (trung vị 100%) |
| Số ký tự Hán khác nhau | 5.592 |

## Công nghệ

`Python` · `Playwright` (chặn bắt GraphQL) · `Google Gemini API` (Batch API) ·
`YOLO / Ultralytics` (phát hiện cột chữ) · `RapidFuzz` (gióng hàng chuỗi) ·
`OpenCC` (chuẩn hoá phồn·giản) · `SQLite` · `aiohttp`

## Cấu trúc mã nguồn

```
src/
├── facebook_scraper_v11.py        Bộ cào chính (cào sâu về quá khứ)
├── facebook_scraper_catchup.py    Bộ cào bù bài mới (con trỏ riêng)
├── scrape_forever.py / scrape_catchup_forever.py   Lớp tự khởi động lại
├── rename_new.py                  Đánh số thư mục bài mới
├── prep_new_posts.py              Chạy YOLO + dựng metadata cho bài mới
├── batch_api_gate.py              Pipeline cổng lọc gán nhãn (Batch API)
├── fix_columns.py                 Hậu xử lý khử hộp cột YOLO trùng/lồng
├── apply_fixes.py                 Áp lại hậu xử lý (không gọi API)
├── pipeline_v15.py                Nạp YOLO + phân bổ ký tự theo chiều cao cột
├── enrich_metadata.py             Giải mã post_id → link nguồn, tác giả
├── build_index.py                 Tạo bảng chỉ mục truy vết
├── find_post.py                   Tra ngược một bài từ link/id
└── runs/detect/.../best.pt        Trọng số YOLO đã huấn luyện
```

Xem **`src/HuongDanCaiDat.txt`** và **`src/HuongDanSuDung.txt`** để cài đặt và chạy.

## Cài đặt nhanh

```bash
python -m venv venv && venv\Scripts\activate
pip install google-genai rapidfuzz opencc-python-reimplemented pillow numpy playwright aiohttp ultralytics
playwright install msedge
```
Đặt API key Gemini vào biến `API_KEY` trong `batch_api_gate.py` / `pipeline_v15.py`
(thay chuỗi `YOUR_GEMINI_API_KEY_HERE`).

## Ghi chú

- Repo này **không kèm** dữ liệu thô, kho ngữ liệu, cookies đăng nhập hay các tệp con trỏ
  trạng thái (xem `.gitignore`) — vì lý do dung lượng và bảo mật.
- Dữ liệu thu thập từ nhóm cộng đồng công khai, chỉ đọc nội dung công khai phục vụ mục
  đích nghiên cứu học thuật.

## Tác giả

Ngô Xuân Hiếu — Khoa Công nghệ Thông tin, Trường ĐH Khoa học Tự nhiên (ĐHQG-HCM).
GVHD: PGS.TS. Đinh Điền, TS. Lương An Vinh.
