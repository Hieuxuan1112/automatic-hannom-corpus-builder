# -*- coding: utf-8 -*-
"""
enrich_metadata.py — Bo sung metadata suy ra duoc vao JSON cua data da cao.
KHONG fetch lai Facebook. Chi THEM key (additive, idempotent), khong xoa gi.

Nguon thong tin:
  - post_id dang Uzpf... (base64) -> decode ra author_id + post_id_numeric -> link
  - history_v11.db -> scrape_time (gio cao) cho tung post_id
  - dataset_merged: noi nguoc ve data_V11 qua (a) source-index, (b) caption match

Chay:  venv\\Scripts\\python.exe enrich_metadata.py
"""
import io, sys, json, base64, sqlite3
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from pathlib import Path

GROUP_ID   = "1792625541124212"
GROUP_NAME = "詩情畫意筆墨結緣"
DB_PATH    = "history_v11.db"
DATA_V11   = Path("data_V11")
MERGED     = Path("dataset_merged")


def decode_pid(uzpf):
    """Uzpf base64 -> (author_id, post_id_numeric) hoac (None, None)."""
    try:
        s = base64.b64decode(uzpf + "=" * (-len(uzpf) % 4)).decode("utf-8", "replace")
        # dang: S:_I{author}:VK:{postid}
        author = post = None
        for part in s.split(":"):
            if part.startswith("_I"):
                author = part[2:]
            elif part.isdigit() and len(part) > 8:
                post = part
        return author, post
    except Exception:
        return None, None


def build_fb_block(uzpf, scrape_time, extra=None):
    author, post = decode_pid(uzpf)
    blk = {
        "group_id": GROUP_ID,
        "group_name": GROUP_NAME,
        "post_id_fb": uzpf,
        "post_id_numeric": post,
        "author_id": author,
        "post_url": f"https://www.facebook.com/groups/{GROUP_ID}/posts/{post}/" if post else None,
        "author_url": f"https://www.facebook.com/{author}" if author else None,
        "scrape_time": scrape_time,
    }
    if extra:
        blk.update(extra)
    return blk


def load_scrape_times():
    conn = sqlite3.connect(DB_PATH)
    d = dict(conn.execute("SELECT post_id, timestamp FROM scraped_posts"))
    conn.close()
    return d


def enrich_v11(scrape_times):
    folders = [d for d in DATA_V11.iterdir() if d.is_dir() and (d / "info.json").exists()]
    n = ok = err = 0
    pid_to_folder = {}      # post_id_numeric -> folder name (de tra cuu)
    caption_to_folder = {}  # caption -> folder (cho merged caption-match)
    for d in folders:
        ij = d / "info.json"
        try:
            j = json.loads(ij.read_text(encoding="utf-8", errors="replace"))
            uzpf = j.get("post_id", "")
            blk = build_fb_block(uzpf, scrape_times.get(uzpf))
            # them key vao info.json (giu nguyen post_id, img_count, image_urls)
            for k, v in blk.items():
                j[k] = v
            ij.write_text(json.dumps(j, ensure_ascii=False, indent=2), encoding="utf-8")
            n += 1
            if blk["post_id_numeric"]:
                ok += 1
                pid_to_folder[blk["post_id_numeric"]] = d.name
            cap = ""
            if (d / "metadata.txt").exists():
                cap = (d / "metadata.txt").read_text(encoding="utf-8", errors="replace").strip()
            if cap:
                # neu trung caption -> de None (khong unique, khong dung de match)
                caption_to_folder[cap] = None if cap in caption_to_folder else d.name
        except Exception as e:
            err += 1
            print(f"  [V11][LOI] {d.name}: {type(e).__name__}: {e}", flush=True)
        if (n + err) % 5000 == 0:
            print(f"  [V11] {n+err}/{len(folders)} ...", flush=True)
    print(f"[V11] enrich {n} post | giai ma id OK {ok} | loi {err}")
    return pid_to_folder, caption_to_folder


def enrich_merged(scrape_times, v11_pid_by_folder, caption_to_folder):
    """v11_pid_by_folder: {v11_folder_name -> (uzpf, post_id_numeric)}"""
    folders = [d for d in MERGED.iterdir() if d.is_dir() and (d / "post_info.json").exists()]
    by_index = by_caption = unresolved = err = 0
    for d in folders:
        pj = d / "post_info.json"
        try:
            j = json.loads(pj.read_text(encoding="utf-8", errors="replace"))
            src = j.get("source", "") or ""
            uzpf = None
            v11_folder = None
            resolved_by = "unresolved"
            # (a) source co dang ".../INDEX" -> data_V11/INDEX
            if "/" in src:
                idx = src.split("/")[-1]
                if idx in v11_pid_by_folder:
                    uzpf = v11_pid_by_folder[idx][0]
                    v11_folder = idx
                    resolved_by = "source_index"
            # (b) caption match
            if uzpf is None:
                cap = ""
                if (d / "metadata.txt").exists():
                    cap = (d / "metadata.txt").read_text(encoding="utf-8", errors="replace").strip()
                f = caption_to_folder.get(cap)
                if f:
                    uzpf = v11_pid_by_folder.get(f, (None,))[0]
                    v11_folder = f
                    resolved_by = "caption_match"
            if uzpf:
                st = scrape_times.get(uzpf)
                blk = build_fb_block(uzpf, st, {"v11_folder": v11_folder, "resolved_by": resolved_by})
                if resolved_by == "source_index":
                    by_index += 1
                else:
                    by_caption += 1
            else:
                blk = {"resolved_by": "unresolved", "post_id_fb": None, "post_id_numeric": None,
                       "author_id": None, "post_url": None, "author_url": None,
                       "group_id": GROUP_ID, "group_name": GROUP_NAME, "scrape_time": None,
                       "v11_folder": None}
                unresolved += 1
            j["facebook"] = blk
            pj.write_text(json.dumps(j, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            err += 1
            print(f"  [MERGED][LOI] {d.name}: {type(e).__name__}: {e}", flush=True)
    print(f"[MERGED] {len(folders)} post | source_index {by_index} | caption_match {by_caption} | unresolved {unresolved} | loi {err}")


def main():
    print("Doc history_v11.db ...")
    scrape_times = load_scrape_times()
    print(f"  {len(scrape_times)} post_id co scrape_time")

    print("Enrich data_V11 ...")
    pid_to_folder, caption_to_folder = enrich_v11(scrape_times)

    # build {v11_folder -> (uzpf, numeric)} de merged tra cuu
    v11_pid_by_folder = {}
    for d in DATA_V11.iterdir():
        if d.is_dir() and (d / "info.json").exists():
            uzpf = json.loads((d / "info.json").read_text(encoding="utf-8")).get("post_id", "")
            _, num = decode_pid(uzpf)
            v11_pid_by_folder[d.name] = (uzpf, num)

    print("Enrich dataset_merged ...")
    enrich_merged(scrape_times, v11_pid_by_folder, caption_to_folder)
    print("XONG.")


if __name__ == "__main__":
    main()
