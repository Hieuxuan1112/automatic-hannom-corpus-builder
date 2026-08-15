import asyncio
import json
import sqlite3
import os
import re
import time
import base64
import aiohttp
import sys
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from playwright.async_api import async_playwright

GROUP_ID = "1792625541124212"

if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

# ==========================================
# CONFIGURATION
# ==========================================
# === CHE DO CATCH-UP: cao tu DAU feed (post moi nhat) xuong toi post da cao gan nhat, roi DUNG ===
# - KHONG inject checkpoint sau (bat dau tu top), history_v11.db tu bo trung -> 300 trung lien tiep thi dung.
# - Ghi checkpoint sang FILE RIENG -> checkpoint_v11.txt (frontier sau) NGUYEN VEN tuyet doi.
GROUP_URL        = "https://www.facebook.com/groups/1792625541124212?sorting_setting=CHRONOLOGICAL"
DATA_DIR         = Path("data_V11")
DB_PATH          = "history_v11.db"
JSONL_PATH       = DATA_DIR / "posts.jsonl"
CHECKPOINT_FILE  = "checkpoint_catchup.txt"   # FILE RIENG (khong dung checkpoint_v11.txt sau)

SCROLL_PAUSE        = 3.0    # Giây/bước khi đang gặp post mới
SCROLL_PAUSE_FAST   = 0.8    # Giây/bước khi dupe streak > 5
MAX_DUPES_STOP      = 300    # Dừng nếu 300 trùng liên tiếp (feed hết / cursor hết hạn)
FEED_SILENCE_STOP   = 120    # Dừng nếu không có GraphQL response trong N giây
TARGET_POSTS_LIMIT  = 0      # 0 = không giới hạn
MIN_CHINESE_CHARS   = 4
MAX_RUN_HOURS       = 24     # catch-up: chay toi khi tu dung o 300 dupe (khong cap thoi gian thuc te)

SKIP_PATTERNS = [
    r"週[一二三四五六日末].{0,6}[課練]",
    r"毎日一字",
    r"每日一字",
    r"老師.*作品|國畫.*詩意|買鴻鈞",
    r"讀字時光",
]

# ==========================================
# HELPERS
# ==========================================
def filter_chinese(text):
    if not text:
        return ""
    return "".join(re.findall(r"[一-鿿㐀-䶿]+", text))


def should_skip_caption(caption):
    cn = filter_chinese(caption)
    if len(cn) < MIN_CHINESE_CHARS:
        return f"thiếu chữ Hán ({len(cn)})"
    for pat in SKIP_PATTERNS:
        if re.search(pat, caption):
            return f"blacklist ({pat[:20]!r})"
    return None


class ScraperState:
    def __init__(self):
        self.total_scraped   = 0
        self.total_skipped   = 0
        self.should_stop     = False
        self.seen_in_session = set()
        self.dupe_streak     = 0
        self.download_tasks  = []
        self.last_response_t = time.time()

        # Đọc checkpoint (= vị trí cũ nhất đã cào lần trước)
        self.saved_cursor = None
        if os.path.exists(CHECKPOINT_FILE):
            with open(CHECKPOINT_FILE, "r") as f:
                self.saved_cursor = f.read().strip() or None

        # Nếu có checkpoint → inject ngay từ request đầu tiên (không đợi dupe)
        self.should_inject = bool(self.saved_cursor)
        self.has_injected  = not bool(self.saved_cursor)   # nếu không có cursor → bỏ qua phase inject

# ==========================================
# DATABASE
# ==========================================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS scraped_posts (
            post_id   TEXT PRIMARY KEY,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    return conn

def is_scraped(conn, post_id: str) -> bool:
    c = conn.cursor()
    c.execute('SELECT 1 FROM scraped_posts WHERE post_id = ?', (post_id,))
    return c.fetchone() is not None

def mark_scraped(conn, post_id: str):
    c = conn.cursor()
    c.execute('INSERT OR IGNORE INTO scraped_posts (post_id) VALUES (?)', (post_id,))
    conn.commit()

# ==========================================
# EXTRACTORS
# ==========================================
def extract_post_info(node):
    caption = ""
    try:
        caption = node.get("comet_sections", {}).get("content", {}).get("story", {}).get("message", {}).get("text", "")
    except:
        pass

    images = set()
    attachments = node.get("comet_sections", {}).get("content", {}).get("story", {}).get("attachments", [])

    def find_images(obj):
        if isinstance(obj, dict):
            if "uri" in obj and isinstance(obj.get("uri"), str):
                uri = obj["uri"]
                if ("scontent" in uri or "fbcdn" in uri) and (".png" in uri or ".jpg" in uri or "jpg" in uri):
                    if "height" in obj and "width" in obj and obj.get("width", 0) > 400:
                        images.add(uri)
            for v in obj.values():
                find_images(v)
        elif isinstance(obj, list):
            for item in obj:
                find_images(item)

    find_images(attachments)
    return caption, list(images)


def _deep_first(obj, key):
    """Tra ve gia tri DAU TIEN cua `key` o bat ky dau trong cay JSON."""
    if isinstance(obj, dict):
        if key in obj and obj[key] not in (None, "", [], {}):
            return obj[key]
        for v in obj.values():
            r = _deep_first(v, key)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for it in obj:
            r = _deep_first(it, key)
            if r is not None:
                return r
    return None


def _find_actor(obj):
    """Tim dict tac gia (User/Page) co id + name."""
    if isinstance(obj, dict):
        if obj.get("__typename") in ("User", "Page") and obj.get("id") and obj.get("name"):
            return {"author_id": str(obj["id"]), "author_name": obj["name"]}
        for v in obj.values():
            r = _find_actor(v)
            if r:
                return r
    elif isinstance(obj, list):
        for it in obj:
            r = _find_actor(it)
            if r:
                return r
    return None


def decode_pid(uzpf):
    """Uzpf base64 -> (author_id, post_id_numeric)."""
    try:
        s = base64.b64decode(uzpf + "=" * (-len(uzpf) % 4)).decode("utf-8", "replace")
        author = post = None
        for part in s.split(":"):
            if part.startswith("_I"):
                author = part[2:]
            elif part.isdigit() and len(part) > 8:
                post = part
        return author, post
    except Exception:
        return None, None


def extract_rich_meta(node, pid):
    """Best-effort: gom toi da metadata tu GraphQL node (cho lan cao moi)."""
    author_id, post_num = decode_pid(pid)
    actor = _find_actor(node) or {}
    ct = _deep_first(node, "creation_time")
    publish_iso = None
    if isinstance(ct, (int, float)):
        try:
            publish_iso = datetime.fromtimestamp(ct, tz=timezone.utc).isoformat()
        except Exception:
            pass
    url = _deep_first(node, "wwwURL") or _deep_first(node, "url")
    if post_num and (not url or "/posts/" not in str(url)):
        url = f"https://www.facebook.com/groups/{GROUP_ID}/posts/{post_num}/"
    return {
        "group_id": GROUP_ID,
        "group_name": "詩情畫意筆墨結緣",
        "post_id_fb": pid,
        "post_id_numeric": post_num,
        "author_id": actor.get("author_id") or author_id,
        "author_name": actor.get("author_name"),
        "post_url": url,
        "author_url": f"https://www.facebook.com/{actor.get('author_id') or author_id}" if (actor.get('author_id') or author_id) else None,
        "creation_time_epoch": ct if isinstance(ct, (int, float)) else None,
        "publish_time": publish_iso,
    }


def get_node_from_id(obj, target_id):
    if isinstance(obj, dict):
        if "node" in obj and isinstance(obj["node"], dict):
            if str(obj["node"].get("id")) == str(target_id):
                return obj["node"]
        for v in obj.values():
            res = get_node_from_id(v, target_id)
            if res:
                return res
    elif isinstance(obj, list):
        for item in obj:
            res = get_node_from_id(item, target_id)
            if res:
                return res
    return None


async def download_image(session, url, path):
    for attempt in range(3):
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                if resp.status == 200:
                    with open(path, 'wb') as f:
                        f.write(await resp.read())
                    return True
        except:
            pass
        if attempt < 2:
            await asyncio.sleep(2 ** attempt)
    return False


async def fetch_all(imgs, img_dir):
    async with aiohttp.ClientSession() as session:
        tasks = [
            download_image(session, url, img_dir / f"image_{i:03d}.jpg")
            for i, url in enumerate(imgs)
        ]
        results = await asyncio.gather(*tasks)
    return sum(results)


def extract_cursor(obj):
    if isinstance(obj, dict):
        if "page_info" in obj and isinstance(obj["page_info"], dict):
            if "end_cursor" in obj["page_info"] and obj["page_info"]["end_cursor"]:
                return obj["page_info"]["end_cursor"]
        for v in obj.values():
            res = extract_cursor(v)
            if res:
                return res
    elif isinstance(obj, list):
        for item in obj:
            res = extract_cursor(item)
            if res:
                return res
    return None


def search_post_ids_in_json(obj, found=None):
    if found is None:
        found = set()
    if isinstance(obj, dict):
        if "post_id" in obj and str(obj["post_id"]).isdigit():
            found.add(str(obj["post_id"]))
        if "node" in obj and isinstance(obj["node"], dict):
            node = obj["node"]
            if "comet_sections" in node and "id" in node:
                found.add(str(node["id"]))
        for v in obj.values():
            search_post_ids_in_json(v, found)
    elif isinstance(obj, list):
        for item in obj:
            search_post_ids_in_json(item, found)
    return found

# ==========================================
# NETWORK INTERCEPTOR
# ==========================================
async def handle_response(response, state, conn):
    if state.should_stop:
        return

    req = response.request
    if not (req.method == "POST" and "graphql" in response.url):
        return
    if not (req.post_data and "GroupsCometFeedRegularStoriesPaginationQuery" in req.post_data):
        return
    if response.status != 200:
        return

    try:
        body_text = await response.text()
    except:
        return

    state.last_response_t = time.time()   # feed vẫn đang hoạt động

    # Parse toàn bộ lines trước, collect cursor tốt nhất từ TOÀN BỘ response
    # (cursor và post_ids thường nằm ở các dòng JSON khác nhau)
    parsed_lines = []
    best_cursor  = None
    for line in body_text.split('\n'):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
            parsed_lines.append(obj)
            c = extract_cursor(obj)
            if c:
                best_cursor = c
        except:
            pass

    for body_json in parsed_lines:
        try:
            post_ids = search_post_ids_in_json(body_json)
            for pid in post_ids:
                if state.should_stop:
                    break
                if pid in state.seen_in_session:
                    continue
                state.seen_in_session.add(pid)

                if is_scraped(conn, pid):
                    state.dupe_streak += 1
                    if state.dupe_streak >= MAX_DUPES_STOP:
                        print(f"🚨 [STOP] {MAX_DUPES_STOP} bài trùng liên tiếp — feed hết / cursor hết hạn.")
                        state.should_stop = True
                    continue

                # ── Post mới chưa cào ──
                state.dupe_streak = 0

                # Lưu checkpoint dùng cursor tốt nhất của cả response
                if best_cursor:
                    with open(CHECKPOINT_FILE, "w") as f:
                        f.write(best_cursor)

                state.total_scraped += 1
                node = get_node_from_id(body_json, pid) or {}
                caption, images = extract_post_info(node)

                if not images:
                    mark_scraped(conn, pid)
                    state.total_scraped -= 1
                    continue

                skip_reason = should_skip_caption(caption)
                if skip_reason:
                    mark_scraped(conn, pid)
                    state.total_scraped -= 1
                    state.total_skipped += 1
                    print(f"  [SKIP] {skip_reason}: {pid} | {caption[:50]!r}")
                    continue

                print(f"  [+] POST {state.total_scraped}: {pid} | {len(images)} ảnh | {filter_chinese(caption)[:20]!r}")
                mark_scraped(conn, pid)

                if TARGET_POSTS_LIMIT and state.total_scraped >= TARGET_POSTS_LIMIT:
                    print(f"\n🎯 Đã đủ {TARGET_POSTS_LIMIT} posts!")
                    state.should_stop = True

                meta = extract_rich_meta(node, pid)
                meta["scrape_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                with open(JSONL_PATH, "a", encoding="utf-8") as f:
                    json.dump({"post_id": pid, "caption": caption,
                               "image_urls": images, "img_count": len(images), **meta},
                              f, ensure_ascii=False)
                    f.write("\n")

                safe_pid = pid.replace(":", "_").replace("=", "_")
                post_dir = DATA_DIR / f"post_{safe_pid}"
                post_dir.mkdir(parents=True, exist_ok=True)
                (post_dir / "images").mkdir(exist_ok=True)
                with open(post_dir / "metadata.txt", "w", encoding="utf-8") as f:
                    f.write(caption)
                with open(post_dir / "info.json", "w", encoding="utf-8") as f:
                    json.dump({"post_id": pid, "img_count": len(images),
                               "image_urls": images, **meta}, f, ensure_ascii=False, indent=2)

                task = asyncio.create_task(fetch_all(images, post_dir / "images"))
                state.download_tasks.append(task)

        except Exception as e:
            with open("v11_errors.log", "a", encoding="utf-8") as ef:
                import traceback
                ef.write(f"[ERROR] {type(e).__name__}: {e}\n{traceback.format_exc()}---\n")
    # end for body_json in parsed_lines

# ==========================================
# MAIN ROUTINE
# ==========================================
async def run_scraper():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn       = init_db()
    state      = ScraperState()
    start_time = time.time()

    session_type = "⏩ Tiếp tục từ checkpoint (inject cursor ngay)" if state.saved_cursor else "🆕 Lần đầu — cào từ mới nhất đến cũ nhất"

    print("=" * 60)
    print(f"🚀 FB SCRAPER V11 | {session_type}")
    print(f"   Output   : {DATA_DIR}")
    print(f"   DB       : {DB_PATH}")
    print(f"   Thời gian: tối đa {MAX_RUN_HOURS} tiếng")
    print(f"   Stop nếu : {MAX_DUPES_STOP} trùng liên tiếp  |  feed im {FEED_SILENCE_STOP}s")
    print("=" * 60)
    if state.saved_cursor:
        print(f"📍 Checkpoint: {state.saved_cursor[:40]}...")
        print(f"   → Inject cursor ngay request đầu → nhảy thẳng vào vùng post cũ")
    else:
        print("📍 Chưa có checkpoint → cào từ top feed xuống, lưu checkpoint sau")
    print()

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            channel="msedge",
            headless=False,
            args=["--disable-blink-features=AutomationControlled"]
        )
        context = await browser.new_context(
            viewport={'width': 1280, 'height': 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0"
        )

        if os.path.exists("cookies.json"):
            with open("cookies.json", "r") as f:
                await context.add_cookies(json.load(f))
            print("🍪 Loaded cookies.json")

        page = await context.new_page()

        async def intercept_route(route):
            req = route.request
            if req.resource_type in ["image", "media", "font"]:
                await route.abort()
                return
            if ("graphql" in req.url and req.method == "POST"):
                post_data = req.post_data
                if (post_data
                        and "GroupsCometFeedRegularStoriesPaginationQuery" in post_data
                        and state.should_inject
                        and not state.has_injected):
                    try:
                        parsed = urllib.parse.parse_qs(post_data)
                        if "variables" in parsed:
                            vars_json = json.loads(parsed["variables"][0])
                            if "cursor" in vars_json:
                                vars_json["cursor"] = state.saved_cursor
                                parsed["variables"] = [json.dumps(vars_json)]
                                new_post_data = urllib.parse.urlencode(parsed, doseq=True)
                                state.should_inject = False
                                state.has_injected  = True
                                print("⚡ Cursor injected — nhảy thẳng vào post cũ!\n")
                                await route.continue_(post_data=new_post_data)
                                return
                    except Exception as e:
                        print(f"Lỗi inject: {e}")
                        state.should_inject = False
                        state.has_injected  = True
            await route.continue_()

        await page.route("**/*", intercept_route)
        page.on("response", lambda r: asyncio.create_task(handle_response(r, state, conn)))

        print("🌐 Đang mở trang Group...")
        await page.goto(GROUP_URL)

        if not os.path.exists("cookies.json"):
            print("⏳ Đợi 90s — hãy đăng nhập thủ công...")
            await asyncio.sleep(90)

        print("\n🔥 Bắt đầu scroll...\n")
        step = 0
        while True:
            if state.should_stop:
                break

            elapsed_h = (time.time() - start_time) / 3600
            if elapsed_h >= MAX_RUN_HOURS:
                print(f"\n⏰ Đã đủ {MAX_RUN_HOURS} tiếng.")
                break

            # Dừng nếu feed im lặng quá lâu (Facebook đóng pagination)
            silent_sec = time.time() - state.last_response_t
            if silent_sec > FEED_SILENCE_STOP and state.has_injected:
                print(f"\n📭 Feed im {silent_sec:.0f}s — Facebook đóng pagination. Dừng.")
                break

            await page.keyboard.press("PageDown")
            await page.keyboard.press("PageDown")

            # Fast scroll khi đang ở vùng trùng
            await asyncio.sleep(SCROLL_PAUSE_FAST if state.dupe_streak > 5 else SCROLL_PAUSE)

            step += 1
            if step % 50 == 0:
                await page.evaluate("""() => {
                    const feeds = document.querySelectorAll('div[role="feed"] > div');
                    if (feeds.length > 20) {
                        for (let i = 0; i < feeds.length - 20; i++) feeds[i].remove();
                    }
                }""")
                state.download_tasks = [t for t in state.download_tasks if not t.done()]
                elapsed_h = (time.time() - start_time) / 3600
                silent_sec = time.time() - state.last_response_t
                if state.dupe_streak > 5:
                    phase = f"🔁 dupe zone (streak={state.dupe_streak})"
                else:
                    phase = "🆕 post mới"
                print(f"  [step {step} | {elapsed_h:.1f}h] scraped={state.total_scraped} skip={state.total_skipped} | {phase} | silent={silent_sec:.0f}s | dl={len(state.download_tasks)}")

        pending = [t for t in state.download_tasks if not t.done()]
        if pending:
            print(f"\n⏳ Đợi {len(pending)} ảnh tải xong...")
            await asyncio.gather(*pending, return_exceptions=True)
            print("✅ Xong.")

        elapsed_h = (time.time() - start_time) / 3600
        print("\n" + "=" * 60)
        print(f"🛑 XONG sau {elapsed_h:.2f} tiếng")
        print(f"   Đã lưu : {state.total_scraped} posts  →  {DATA_DIR}/")
        print(f"   Đã lọc : {state.total_skipped} posts")
        if os.path.exists(CHECKPOINT_FILE):
            with open(CHECKPOINT_FILE) as f:
                ck = f.read().strip()
            print(f"   Checkpoint: {ck[:40]}... (dùng lần sau)")
        print("=" * 60)

        current_cookies = await context.cookies()
        with open("cookies.json", "w") as f:
            json.dump(current_cookies, f)

        await browser.close()
        conn.close()


if __name__ == "__main__":
    asyncio.run(run_scraper())
