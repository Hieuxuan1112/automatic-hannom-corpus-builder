#!/usr/bin/env python3
"""
V15: Tối ưu từ V14
  Fix 1: Mọi fail/exception đều được ghi → không re-process
  Fix 2: Call 1 prompt: calligraphy check độc lập với caption (giảm bias)
  Fix 3: Call 2 prompt: HIGH confidence only → giảm over-counting
  Fix 4: RateLimiter riêng cho từng model (flash-lite vs flash)
  Fix 5: YOLO cache từ yolo_prescan.py → skip YOLO, pre-filter 0-cột
"""

import os, sys, json, re, time, threading, traceback, shutil
os.environ["FLAGS_use_mkldnn"] = "0"
os.environ["MKLDNN_ENABLED"] = "0"
os.environ["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] = "python"

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from concurrent.futures import ThreadPoolExecutor

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

import google.generativeai as genai
from ultralytics import YOLO
from opencc import OpenCC

_cc = OpenCC("t2s")

# === CONFIGURATION ===
GEMINI_API_KEY      = "YOUR_GEMINI_API_KEY_HERE"
YOLO_MODEL_PATH     = "runs/detect/calligraphy_det_v1/weights/best.pt"
DATA_ROOT           = "data_V3"
DEBUG_DIR           = "results_test/v15_debug"
OUTPUT_DATASET_DIR  = "datamau_v15"
RESULTS_FILE        = "v15_results.json"
ALL_RESULTS_FILE    = "v15_all.json"
YOLO_CACHE_FILE     = "yolo_cache_v3.json"   # từ yolo_prescan.py
LIMIT               = 50
MAX_WORKERS         = 10

# Rate limit riêng cho từng model (Fix 4)
RPM_LITE            = 60    # gemini-2.5-flash-lite
RPM_STRONG          = 60    # gemini-2.5-flash-lite (Call 2 — task đơn giản, đủ dùng)

GEMINI_MODEL        = "gemini-2.5-flash-lite"
GEMINI_MODEL_STRONG = "gemini-2.5-flash-lite"

# Thresholds
CHAR_IN_CAPTION_MIN = 0.75
MIN_LABEL_CHARS     = 4
VISIBLE_HIGH_MIN    = 0.20   # >= 20% HIGH-confidence chars (Fix 3: strict hơn raw count)

# === INITIALIZE ===
genai.configure(api_key=GEMINI_API_KEY)
MODEL_VISION = genai.GenerativeModel(
    GEMINI_MODEL,
    generation_config=genai.types.GenerationConfig(temperature=0)
)
MODEL_VISION_STRONG = genai.GenerativeModel(
    GEMINI_MODEL_STRONG,
    generation_config=genai.types.GenerationConfig(temperature=0)
)

print(f"Loading YOLO: {YOLO_MODEL_PATH}")
yolo_model = YOLO(YOLO_MODEL_PATH)
yolo_model.fuse()
yolo_lock = threading.Lock()

# YOLO cache (Fix 5)
_yolo_cache = {}


# === RATE LIMITERS (Fix 4) ===
class RateLimiter:
    def __init__(self, rpm):
        self.min_interval = 60.0 / rpm
        self.last_call    = 0
        self.lock         = threading.Lock()

    def wait(self):
        with self.lock:
            elapsed = time.time() - self.last_call
            if elapsed < self.min_interval:
                time.sleep(self.min_interval - elapsed)
            self.last_call = time.time()


_limiter_lite   = RateLimiter(RPM_LITE)
_limiter_strong = RateLimiter(RPM_STRONG)


# ============================
# HELPERS
# ============================
def filter_chinese(text):
    if not text:
        return ""
    return "".join(re.findall(r"[一-鿿㐀-䶿]+", text))


def get_yolo_layout(image_path):
    with yolo_lock:
        results = yolo_model.predict(source=image_path, conf=0.25, verbose=False)
    cols, sigs = [], []
    for box in results[0].boxes:
        cls  = int(box.cls[0])
        xyxy = box.xyxy[0].tolist()
        conf = float(box.conf[0])
        entry = {
            "bbox":     xyxy,
            "class":    cls,
            "conf":     conf,
            "center_x": (xyxy[0] + xyxy[2]) / 2,
            "height":   xyxy[3] - xyxy[1],
        }
        if cls == 0:   cols.append(entry)
        elif cls == 1: sigs.append(entry)
    cols = sorted(cols, key=lambda x: x["center_x"], reverse=True)
    return cols, sigs


def analyze_with_gemini(image_path, caption):
    """
    Call 1 (flash-lite): Confirm calligraphy + Clean caption.
    Fix 2: Caption đặt CUỐI prompt, TASK 1 không tham chiếu caption
    → giảm bias khi Gemini judge ảnh.
    """
    _limiter_lite.wait()
    try:
        img = Image.open(image_path)
        prompt = f"""Analyze this image and the caption below. Complete TWO independent tasks.

TASK 1 — IMAGE ONLY (do not use the caption for this task):
Look at the image. Does it contain Chinese calligraphy brushwork written with ink brush?
Answer YES or NO.

TASK 2 — CAPTION TEXT ONLY (do not read characters from the image):
Extract only the poem, couplet, or prose text from the caption.
Remove ALL of: hashtags (#), calligraphy style labels (隸書 楷書 行書 草書 篆書 魏碑 etc.),
author/artist names, announcements, dates, admin text, single chars in brackets like 「毅」.
If the caption contains no poem text, write NONE.

Reply in this EXACT format (two lines only):
HAS_CALLIGRAPHY: YES
CLEAN_TEXT: 弘毅寬厚

CAPTION:
{caption}"""

        response   = MODEL_VISION.generate_content([prompt, img])
        text       = response.text.strip()
        m_cal      = re.search(r"HAS_CALLIGRAPHY:\s*(YES|NO)", text, re.I)
        m_clean    = re.search(r"CLEAN_TEXT:\s*(.+)", text)
        has_cal    = bool(m_cal and m_cal.group(1).upper() == "YES")
        clean_text = filter_chinese(m_clean.group(1)) if m_clean else ""
        return has_cal, clean_text
    except Exception:
        return False, ""


def validate_visible_with_gemini(image_path, clean_text):
    """
    Call 2 (flash): Đếm chữ nhìn thấy trong ảnh.
    Fix 3: HIGH confidence only → giảm over-counting do hint bias.
    """
    _limiter_strong.wait()
    try:
        img       = Image.open(image_path)
        char_list = "  ".join(clean_text)
        prompt    = f"""Look carefully at this Chinese calligraphy image.

These {len(clean_text)} characters should appear somewhere in the image:
[ {char_list} ]

For each character, assess your confidence that you can CLEARLY see it in the brushwork:
- HIGH: you can clearly and confidently identify this character in the image
- LOW: you think it might be there but are uncertain
- NO: you cannot find this character, or the image does not match

Count ONLY the HIGH confidence characters (ones you are certain about).

Reply in this EXACT format:
HIGH_COUNT: <number you are HIGH confidence about>
TOTAL: {len(clean_text)}"""

        response = MODEL_VISION_STRONG.generate_content([prompt, img])
        text     = response.text.strip()
        m        = re.search(r"HIGH_COUNT:\s*(\d+)", text)
        if m:
            high    = int(m.group(1))
            ratio   = high / len(clean_text)
            return ratio, high
        return 0.0, 0
    except Exception:
        return 0.0, 0


def verify_label(clean_text, caption):
    """Pure Python: opencc-normalized hallucination check + min length."""
    if not clean_text:
        return False, "empty"
    clean_cap = filter_chinese(caption)
    if not clean_cap:
        return False, "caption has no CJK"
    norm_text = _cc.convert(clean_text)
    norm_cap  = _cc.convert(clean_cap)
    cap_chars = set(norm_cap)
    valid     = sum(1 for c in norm_text if c in cap_chars)
    ratio     = valid / len(norm_text)
    if ratio < CHAR_IN_CAPTION_MIN:
        return False, f"hallucination ({ratio:.0%} in caption)"
    if len(clean_text) < MIN_LABEL_CHARS:
        return False, f"too short ({len(clean_text)} chars)"
    return True, "ok"


def allocate_characters(poem, columns):
    if not columns or not poem:
        return [], []
    n_chars  = len(poem)
    total_h  = sum(c["height"] for c in columns)
    counts   = [int(round((c["height"] / total_h) * n_chars)) for c in columns]
    diff     = n_chars - sum(counts)
    if diff != 0:
        counts[counts.index(max(counts))] += diff
    lines, start = [], 0
    for count in counts:
        lines.append(poem[start:start + count])
        start += count
    return lines, counts


def create_annotated_image(image_path, columns, signatures, counts, output_path):
    img  = Image.open(image_path).convert("RGB")
    draw = ImageDraw.Draw(img)
    try:
        font       = ImageFont.truetype("arial.ttf", 30)
        font_small = ImageFont.truetype("arial.ttf", 20)
    except Exception:
        font = font_small = ImageFont.load_default()
    for i, col in enumerate(columns):
        bbox = col["bbox"]
        draw.rectangle(bbox, outline="lime", width=4)
        c = counts[i] if i < len(counts) else 0
        draw.text((bbox[0], bbox[1] - 38), f"C{i+1}:{c}", fill="lime", font=font)
    for sig in signatures:
        bbox = sig["bbox"]
        draw.rectangle(bbox, outline="red", width=3)
        draw.text((bbox[0], bbox[1] - 25), "SIG", fill="red", font=font_small)
    img.save(output_path)


# ============================
# CORE ENGINE
# ============================
def process_post(idx, folder, total_posts):
    post_id = folder.name
    caption = ""

    # Fix 1: _fail định nghĩa TRƯỚC try → exception cũng dùng được
    def _fail(reason):
        print(f"  X [{idx+1:>4}/{total_posts}] {post_id}: {reason}")
        return {"post_id": post_id, "status": "FAIL", "reason": reason,
                "original_metadata": caption}

    try:
        meta_file = folder / "metadata.txt"
        caption   = meta_file.read_text(encoding="utf-8").strip() if meta_file.exists() else ""

        # Lấy ảnh
        all_imgs = []
        if (folder / "images").exists():
            all_imgs = sorted([
                f for f in (folder / "images").iterdir()
                if f.suffix.lower() in {".jpg", ".jpeg", ".png"}
            ])
        if not all_imgs:
            return _fail("no images")   # Fix 1: record thay vì return None

        # 1. YOLO — dùng cache nếu có (Fix 5), fallback live nếu không
        cached = _yolo_cache.get(post_id)
        if cached is not None:
            if cached["num_columns"] == 0:
                return _fail("No columns (YOLO)")
            img_path   = folder / cached["best_image"]
            columns    = cached["columns"]
            signatures = cached["signatures"]
        else:
            img_path = None
            columns, signatures = [], []
            for f in all_imgs:
                cols, sigs = get_yolo_layout(str(f))
                if len(cols) > len(columns):
                    columns, signatures, img_path = cols, sigs, f
            if not columns:
                return _fail("No columns (YOLO)")

        # 1b. Sanity check: số cột không được vượt quá số chữ Hán trong caption
        #     Nếu vượt → YOLO đang detect sai (bảng tập viết, grid, ảnh in nhỏ...)
        caption_cn = filter_chinese(caption)
        if len(columns) > max(len(caption_cn), 1):
            return _fail(f"Too many cols ({len(columns)}) for {len(caption_cn)} CN chars")

        # 1c. Column aspect ratio: cột thư pháp dọc phải cao hơn rộng (H/W >= 2.6)
        #     H/W thấp → chữ ngang, chữ nghệ thuật, sơn tường → loại
        #     Threshold 2.6: ranh giới giữa 2.59 (xác nhận ngang) và 2.75 (xác nhận dọc)
        hw_ratios = [c["height"] / max(c["bbox"][2] - c["bbox"][0], 1) for c in columns]
        avg_hw = sum(hw_ratios) / len(hw_ratios)
        if avg_hw < 2.6:
            return _fail(f"Horizontal/artistic layout (H/W={avg_hw:.2f})")

        # 2. Gemini Call 1: confirm + clean (Fix 2: prompt mới)
        has_cal, clean_text = analyze_with_gemini(str(img_path), caption)
        if not has_cal:
            return _fail("Not calligraphy")
        if not clean_text:
            return _fail("No text in caption")

        # 3. Verify Python (0 API call)
        ok, reason = verify_label(clean_text, caption)
        if not ok:
            return _fail(reason)

        # 3b. Cột nhiều hơn chữ trong clean_text → ảnh chứa nhiều text hơn caption biết
        #     (poster ghi "抄X詩" nhưng ảnh có cả bài dài → label sẽ thiếu/sai)
        if len(columns) > len(clean_text):
            return _fail(f"More cols ({len(columns)}) than label chars ({len(clean_text)})")

        # 4. Gemini Call 2: validate HIGH-confidence visible (Fix 3)
        vis_ratio, vis_count = validate_visible_with_gemini(str(img_path), clean_text)
        if vis_ratio < VISIBLE_HIGH_MIN:
            return _fail(f"Low visible ({vis_count}/{len(clean_text)}={vis_ratio:.0%})")

        # 5. Density Check
        img         = Image.open(img_path)
        img_h       = img.height
        min_char_h  = img_h / 150
        total_col_h = sum(c["height"] for c in columns)
        avg_char_h  = total_col_h / len(clean_text)
        if avg_char_h < min_char_h:
            return _fail(f"Too dense (H={avg_char_h:.1f})")

        # 6. Allocate → label
        label_lines, char_counts = allocate_characters(clean_text, columns)
        full_label = "\n".join(label_lines)

        # 7. Export
        dest = Path(OUTPUT_DATASET_DIR) / post_id
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "images").mkdir(exist_ok=True)
        shutil.copy2(img_path, dest / "images")
        (dest / "label.txt").write_text(full_label, encoding="utf-8")
        if meta_file.exists():
            shutil.copy2(meta_file, dest / "metadata.txt")

        debug_path = os.path.join(DEBUG_DIR, f"{post_id}_annotated.jpg")
        create_annotated_image(str(img_path), columns, signatures, char_counts, debug_path)

        clean_cap = filter_chinese(caption)
        coverage  = len(clean_text) / len(clean_cap) if clean_cap else 0

        res = {
            "post_id":           post_id,
            "status":            "SUCCESS",
            "columns":           len(columns),
            "char_counts":       char_counts,
            "clean_text":        clean_text,
            "label":             full_label,
            "original_metadata": caption,
            "avg_char_h":        avg_char_h,
            "coverage":          round(coverage, 3),
            "visible_ratio":     round(vis_ratio, 3),
        }
        (dest / "post_info.json").write_text(
            json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        print(f"  V [{idx+1:>4}/{total_posts}] {post_id}: PASS | "
              f"{len(clean_text)}chars | high={vis_ratio:.0%} | H={avg_char_h:.1f}")
        return res

    except Exception as e:
        # Fix 1: exception cũng được record → không re-process
        print(f"  ! [{idx+1:>4}/{total_posts}] {folder.name}: exception {type(e).__name__}")
        traceback.print_exc()
        return _fail(f"exception: {type(e).__name__}")


def main():
    global _yolo_cache

    print("=" * 70)
    print(f"V15 PIPELINE | call1={GEMINI_MODEL}({RPM_LITE}rpm) | "
          f"call2={GEMINI_MODEL_STRONG}({RPM_STRONG}rpm) | workers={MAX_WORKERS}")
    print(f"  Input : {DATA_ROOT}")
    print(f"  Output: {OUTPUT_DATASET_DIR}")
    print(f"  Thresholds: char_in_cap>={CHAR_IN_CAPTION_MIN:.0%} | high_visible>={VISIBLE_HIGH_MIN:.0%}")
    print("=" * 70)

    # Load YOLO cache (Fix 5)
    if os.path.exists(YOLO_CACHE_FILE):
        with open(YOLO_CACHE_FILE, encoding="utf-8") as f:
            _yolo_cache = json.load(f)
        cached_with_cols = sum(1 for v in _yolo_cache.values() if v and v["num_columns"] > 0)
        cached_no_cols   = sum(1 for v in _yolo_cache.values() if v and v["num_columns"] == 0)
        print(f"YOLO cache: {len(_yolo_cache)} posts "
              f"({cached_with_cols} có cột, {cached_no_cols} không cột sẽ skip)")
    else:
        print("Không có YOLO cache — sẽ chạy YOLO live (chậm hơn).")
        print("Gợi ý: chạy yolo_prescan.py trước để tăng tốc.\n")

    posts_dir    = Path(DATA_ROOT)
    post_folders = sorted([
        d for d in posts_dir.iterdir()
        if d.is_dir() and d.name.startswith("post_")
    ])

    os.makedirs(DEBUG_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DATASET_DIR, exist_ok=True)

    # Resume
    results        = []
    processed_pids = set()
    if os.path.exists(RESULTS_FILE):
        try:
            with open(RESULTS_FILE, encoding="utf-8") as f:
                results = json.load(f)
        except Exception:
            pass
    if os.path.exists(ALL_RESULTS_FILE):
        try:
            with open(ALL_RESULTS_FILE, encoding="utf-8") as f:
                all_done = json.load(f)
            processed_pids = {r["post_id"] for r in all_done}
            print(f"Resume: {len(processed_pids)} posts đã xử lý (pass+fail).")
        except Exception:
            pass

    # Pre-filter: bỏ qua đã xử lý
    post_folders = [f for f in post_folders if f.name not in processed_pids]

    # Pre-filter: bỏ qua 0-cột từ cache (không tốn Gemini) — vẫn record fail
    # Ghi nhận các post 0-cột chưa processed vào all_results
    zero_col_fails = []
    if _yolo_cache:
        remaining = []
        for folder in post_folders:
            cached = _yolo_cache.get(folder.name)
            if cached is not None and cached["num_columns"] == 0:
                zero_col_fails.append({
                    "post_id": folder.name,
                    "status":  "FAIL",
                    "reason":  "No columns (YOLO)",
                    "original_metadata": "",
                })
            else:
                remaining.append(folder)
        if zero_col_fails:
            print(f"Pre-filter: {len(zero_col_fails)} post 0-cột → bỏ qua (no Gemini call)")
        post_folders = remaining

    if LIMIT > 0:
        post_folders = post_folders[:LIMIT]

    total = len(post_folders)
    if total == 0 and not zero_col_fails:
        print("Tất cả đã xử lý xong.")
        return

    if total > 0:
        print(f"Bắt đầu: {total} posts | ~{total*2} Gemini calls\n")

    new_all = list(zero_col_fails)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [
            executor.submit(process_post, i, folder, total)
            for i, folder in enumerate(post_folders)
        ]
        for future in futures:
            res = future.result()
            if res:
                new_all.append(res)

    new_pass = [r for r in new_all if r.get("status") == "SUCCESS"]

    results.extend(new_pass)
    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    all_prev, prev_pids = [], set()
    if os.path.exists(ALL_RESULTS_FILE):
        try:
            with open(ALL_RESULTS_FILE, encoding="utf-8") as f:
                all_prev = json.load(f)
            prev_pids = {r["post_id"] for r in all_prev}
        except Exception:
            pass
    all_prev.extend([r for r in new_all if r["post_id"] not in prev_pids])
    with open(ALL_RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(all_prev, f, ensure_ascii=False, indent=2)

    pass_rate = len(new_pass) / max(total, 1) * 100
    print(f"\nKết quả: {len(new_pass)}/{total} PASS ({pass_rate:.1f}%)")
    print(f"Tổng dataset: {len(results)} posts")
    print(f"Saved: {RESULTS_FILE} | {ALL_RESULTS_FILE}")


if __name__ == "__main__":
    main()
