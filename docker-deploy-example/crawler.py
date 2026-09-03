# -*- coding: utf-8 -*-
"""
Bo cao Facebook cho Docker — ban ON DINH LAU DAI.
Chi lay caption (label) + anh, luu ra JSON. Giu dung cach tiep can va cac co che
chong sap cua he thong chinh:
  - Chan bat (intercept) response GraphQL (khong parse HTML).
  - Don DOM dinh ky   -> khong phinh bo nho khi cao lau (tranh tran RAM).
  - Con tro checkpoint -> dung/crash roi chay lai la nhay tiep vao vung cu.
  - Khu trung lap SQLite ben vung -> khong tai lai bai da co.
  - Tu khoi dong lai   -> phien trinh duyet chet thi tu dung lai va cao tiep.

Toan bo trang thai (checkpoint, db, cookies, ket qua) nam trong OUTPUT_DIR — mount
volume tu ngoai vao de song sot qua cac lan khoi dong lai container.

Cau hinh qua bien moi truong:
  GROUP_URL       URL group (mac dinh: group thu phap trong de tai)
  TARGET_POSTS    So bai roi dung (mac dinh 0 = cao vo han toi het)
  OUTPUT_DIR      Thu muc trang thai + ket qua (mac dinh /data)
  HEADLESS        "1" chay an trinh duyet (mac dinh 1)
  MAX_DUPES_STOP  Dung han khi gap N bai trung lien tiep (mac dinh 300 = cham vung da cao)
  MAX_RESTARTS    So lan tu khoi dong lai toi da (mac dinh 0 = khong gioi han)
"""
import asyncio, json, os, re, sys, base64, sqlite3, time, urllib.parse
from pathlib import Path
import aiohttp
from playwright.async_api import async_playwright

if getattr(sys.stdout, "encoding", "").lower() not in ("utf-8", "utf8"):
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass

# ---------------- Cau hinh ----------------
GROUP_URL      = os.getenv("GROUP_URL",
    "https://www.facebook.com/groups/1792625541124212?sorting_setting=CHRONOLOGICAL")
TARGET_POSTS   = int(os.getenv("TARGET_POSTS", "0"))     # 0 = vo han
OUTPUT_DIR     = Path(os.getenv("OUTPUT_DIR", "/data"))
HEADLESS       = os.getenv("HEADLESS", "1") != "0"
MAX_DUPES_STOP = int(os.getenv("MAX_DUPES_STOP", "300"))
MAX_RESTARTS   = int(os.getenv("MAX_RESTARTS", "0"))     # 0 = khong gioi han

COOKIES_FILE      = OUTPUT_DIR / "cookies.json"
DB_PATH           = OUTPUT_DIR / "history.db"
CHECKPOINT_FILE   = OUTPUT_DIR / "checkpoint.txt"
POSTS_JSONL       = OUTPUT_DIR / "posts.jsonl"

SCROLL_PAUSE      = 3.0
SCROLL_PAUSE_FAST = 0.8
FEED_SILENCE_STOP = 120     # feed im qua N giay -> khoi dong lai phien
MIN_CHINESE_CHARS = 4
DOM_PRUNE_EVERY   = 50      # don DOM moi N buoc cuon

# Blacklist caption: loai bai tap dinh ky / quang cao (giong he thong chinh)
SKIP_PATTERNS = [
    r"週[一二三四五六日末].{0,6}[課練]",
    r"毎日一字",
    r"每日一字",
    r"老師.*作品|國畫.*詩意|買鴻鈞",
    r"讀字時光",
]

GROUP_ID = re.search(r"/groups/(\d+)", GROUP_URL)
GROUP_ID = GROUP_ID.group(1) if GROUP_ID else ""


# ---------------- CSDL khu trung lap ----------------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS scraped (post_id TEXT PRIMARY KEY)")
    conn.commit()
    return conn

def is_scraped(conn, pid):
    return conn.execute("SELECT 1 FROM scraped WHERE post_id=?", (pid,)).fetchone() is not None

def mark_scraped(conn, pid):
    conn.execute("INSERT OR IGNORE INTO scraped (post_id) VALUES (?)", (pid,))
    conn.commit()


# ---------------- Trich xuat ----------------
def filter_chinese(text):
    return "".join(re.findall(r"[一-鿿㐀-䶿]+", text or ""))

def should_skip_caption(caption):
    """Tra ve ly do bo qua, hoac None neu caption dat (giong he thong chinh)."""
    if len(filter_chinese(caption)) < MIN_CHINESE_CHARS:
        return "thieu chu Han"
    for pat in SKIP_PATTERNS:
        if re.search(pat, caption):
            return "blacklist"
    return None

def extract_post_info(node):
    caption = ""
    try:
        caption = (node.get("comet_sections", {}).get("content", {})
                   .get("story", {}).get("message", {}).get("text", "")) or ""
    except Exception:
        pass
    images = set()
    attachments = (node.get("comet_sections", {}).get("content", {})
                   .get("story", {}).get("attachments", []))
    def find_images(obj):
        if isinstance(obj, dict):
            uri = obj.get("uri")
            if isinstance(uri, str) and ("scontent" in uri or "fbcdn" in uri) \
                    and (".jpg" in uri or ".png" in uri or "jpg" in uri):
                if obj.get("width", 0) > 400:
                    images.add(uri)
            for v in obj.values():
                find_images(v)
        elif isinstance(obj, list):
            for it in obj:
                find_images(it)
    find_images(attachments)
    return caption, list(images)

def get_node_from_id(obj, target_id):
    if isinstance(obj, dict):
        node = obj.get("node")
        if isinstance(node, dict) and str(node.get("id")) == str(target_id):
            return node
        for v in obj.values():
            r = get_node_from_id(v, target_id)
            if r: return r
    elif isinstance(obj, list):
        for it in obj:
            r = get_node_from_id(it, target_id)
            if r: return r
    return None

def search_post_ids(obj, found=None):
    if found is None:
        found = set()
    if isinstance(obj, dict):
        node = obj.get("node")
        if isinstance(node, dict) and "comet_sections" in node and node.get("id"):
            found.add(str(node["id"]))
        for v in obj.values():
            search_post_ids(v, found)
    elif isinstance(obj, list):
        for it in obj:
            search_post_ids(it, found)
    return found

def extract_cursor(obj):
    if isinstance(obj, dict):
        pi = obj.get("page_info")
        if isinstance(pi, dict) and pi.get("end_cursor"):
            return pi["end_cursor"]
        for v in obj.values():
            r = extract_cursor(v)
            if r: return r
    elif isinstance(obj, list):
        for it in obj:
            r = extract_cursor(it)
            if r: return r
    return None

def post_url_of(pid):
    try:
        s = base64.b64decode(pid + "=" * (-len(pid) % 4)).decode("utf-8", "replace")
        for part in s.split(":"):
            if part.isdigit() and len(part) > 8:
                return f"https://www.facebook.com/groups/{GROUP_ID}/posts/{part}/"
    except Exception:
        pass
    return None


# ---------------- Tai anh ----------------
async def download_images(urls, folder):
    saved = []
    async with aiohttp.ClientSession() as session:
        for i, url in enumerate(urls):
            path = folder / f"image_{i:03d}.jpg"
            for attempt in range(3):
                try:
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as r:
                        if r.status == 200:
                            path.write_bytes(await r.read())
                            saved.append(path.name)
                            break
                except Exception:
                    if attempt < 2:
                        await asyncio.sleep(2 ** attempt)
    return saved


# ---------------- Trang thai mot phien ----------------
class State:
    def __init__(self, saved_cursor):
        self.seen = set()               # id da xu ly trong phien nay
        self.dupe_streak = 0
        self.total = 0
        self.stop = False               # dung han (het feed / du target)
        self.tasks = []
        self.last_response_t = time.time()
        self.saved_cursor = saved_cursor
        self.should_inject = bool(saved_cursor)
        self.has_injected  = not bool(saved_cursor)


async def save_post(conn, pid, caption, image_urls, state):
    folder = OUTPUT_DIR / pid.replace(":", "_").replace("=", "_")
    folder.mkdir(parents=True, exist_ok=True)
    files = await download_images(image_urls, folder)
    if not files:
        return
    (folder / "label.txt").write_text(caption, encoding="utf-8")
    with open(POSTS_JSONL, "a", encoding="utf-8") as f:      # noi tiep, khong ghi de
        json.dump({"post_id": pid, "label": caption,
                   "images": files, "post_url": post_url_of(pid)},
                  f, ensure_ascii=False)
        f.write("\n")
    state.total += 1
    print(f"  [+] {state.total}  {pid}  | {len(files)} anh | "
          f"{filter_chinese(caption)[:20]!r}", flush=True)
    if TARGET_POSTS and state.total >= TARGET_POSTS:
        print(f"🎯 Du {TARGET_POSTS} bai — dung.", flush=True)
        state.stop = True


async def handle_response(response, state, conn):
    if state.stop:
        return
    req = response.request
    if req.method != "POST" or "graphql" not in response.url or response.status != 200:
        return
    if not (req.post_data and "GroupsCometFeedRegularStoriesPaginationQuery" in req.post_data):
        return
    try:
        body = await response.text()
    except Exception:
        return
    state.last_response_t = time.time()

    parsed_lines, best_cursor = [], None
    for line in body.split("\n"):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        parsed_lines.append(obj)
        c = extract_cursor(obj)
        if c:
            best_cursor = c

    for obj in parsed_lines:
        for pid in search_post_ids(obj):
            if state.stop or pid in state.seen:
                continue
            state.seen.add(pid)

            if is_scraped(conn, pid):                # da cao o lan truoc
                state.dupe_streak += 1
                if state.dupe_streak >= MAX_DUPES_STOP:
                    print(f"🚨 {MAX_DUPES_STOP} bai trung lien tiep — het feed / cham vung da cao. Dung.",
                          flush=True)
                    state.stop = True
                continue

            state.dupe_streak = 0
            if best_cursor:                          # luu checkpoint lien tuc
                CHECKPOINT_FILE.write_text(best_cursor)

            node = get_node_from_id(obj, pid) or {}
            caption, images = extract_post_info(node)
            mark_scraped(conn, pid)
            if not images or should_skip_caption(caption):
                continue
            state.tasks.append(asyncio.create_task(save_post(conn, pid, caption, images, state)))


async def run_session(conn):
    """Chay 1 phien trinh duyet. Tra ve True neu dung han, False neu can khoi dong lai."""
    saved_cursor = CHECKPOINT_FILE.read_text().strip() if CHECKPOINT_FILE.exists() else None
    state = State(saved_cursor)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS, args=["--no-sandbox"])
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        if COOKIES_FILE.exists():
            await context.add_cookies(json.load(open(COOKIES_FILE)))
            print(f"🍪 Da nap cookies tu {COOKIES_FILE}", flush=True)
        page = await context.new_page()

        async def intercept_route(route):
            req = route.request
            if req.resource_type in ("image", "media", "font"):   # bo tai tai nguyen nang
                await route.abort(); return
            if ("graphql" in req.url and req.method == "POST" and req.post_data
                    and "GroupsCometFeedRegularStoriesPaginationQuery" in req.post_data
                    and state.should_inject and not state.has_injected):
                try:
                    parsed = urllib.parse.parse_qs(req.post_data)
                    if "variables" in parsed:
                        vj = json.loads(parsed["variables"][0])
                        if "cursor" in vj:
                            vj["cursor"] = state.saved_cursor
                            parsed["variables"] = [json.dumps(vj)]
                            state.should_inject = False; state.has_injected = True
                            print("⚡ Da chen cursor — nhay vao vung cu.", flush=True)
                            await route.continue_(post_data=urllib.parse.urlencode(parsed, doseq=True))
                            return
                except Exception:
                    state.should_inject = False; state.has_injected = True
            await route.continue_()

        await page.route("**/*", intercept_route)
        page.on("response", lambda r: asyncio.create_task(handle_response(r, state, conn)))

        print(f"🌐 Mo group: {GROUP_URL}", flush=True)
        await page.goto(GROUP_URL, timeout=60000)
        if not COOKIES_FILE.exists() and not HEADLESS:
            print("⏳ Doi 90s de dang nhap thu cong...", flush=True)
            await asyncio.sleep(90)
            json.dump(await context.cookies(), open(COOKIES_FILE, "w"))

        print("🔥 Bat dau cuon...", flush=True)
        step = 0
        try:
            while not state.stop:
                if time.time() - state.last_response_t > FEED_SILENCE_STOP and state.has_injected:
                    print(f"📭 Feed im {FEED_SILENCE_STOP}s — khoi dong lai phien de cao tiep.",
                          flush=True)
                    await browser.close()
                    return False                       # -> khoi dong lai
                await page.keyboard.press("PageDown")
                await page.keyboard.press("PageDown")
                await asyncio.sleep(SCROLL_PAUSE_FAST if state.dupe_streak > 5 else SCROLL_PAUSE)
                step += 1
                if step % DOM_PRUNE_EVERY == 0:        # DON DOM -> khong tran bo nho
                    await page.evaluate("""() => {
                        const f = document.querySelectorAll('div[role="feed"] > div');
                        for (let i = 0; i < f.length - 20; i++) f[i].remove();
                    }""")
                    state.tasks = [t for t in state.tasks if not t.done()]
                    print(f"  [step {step}] tong={state.total} dupe_streak={state.dupe_streak} "
                          f"dl={len(state.tasks)}", flush=True)
        except Exception as e:
            print(f"⚠ Loi phien ({str(e)[:80]}) — khoi dong lai.", flush=True)
            try: await browser.close()
            except Exception: pass
            return False                               # -> khoi dong lai

        pending = [t for t in state.tasks if not t.done()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        await browser.close()
    return True                                        # dung han


async def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = init_db()
    restarts = 0
    while True:
        done = await run_session(conn)
        if done:
            break
        restarts += 1
        if MAX_RESTARTS and restarts >= MAX_RESTARTS:
            print(f"⏹ Da khoi dong lai {restarts} lan — dung.", flush=True)
            break
        print(f"🔄 Khoi dong lai lan {restarts} (cho 5s)...", flush=True)
        await asyncio.sleep(5)
    total = sum(1 for _ in open(POSTS_JSONL, encoding="utf-8")) if POSTS_JSONL.exists() else 0
    print("=" * 50, flush=True)
    print(f"🛑 XONG — tong cong {total} bai -> {POSTS_JSONL}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
