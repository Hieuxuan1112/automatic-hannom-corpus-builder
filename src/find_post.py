# -*- coding: utf-8 -*-
"""
find_post.py — TRUY NGUOC 1 post Facebook trong data_V11 / dataset_merged.

Dung khi: thay 1 post tren Facebook, muon biet no co duoc cao + xu ly khong.

Cach dung:
  venv\\Scripts\\python.exe find_post.py <link-post  HOAC  id-so  HOAC  Uzpf...> [--open]

  --open : mo anh thu phap (viewer mac dinh) + mo post Facebook tren trinh duyet.

Vi du:
  python find_post.py https://www.facebook.com/groups/1792625541124212/posts/2717200715333352/
  python find_post.py 2717200715333352 --open
"""
import io, sys, json, re, base64
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from pathlib import Path

DATA_V11 = Path("data_V11")
MERGED   = Path("dataset_merged")


def parse_arg(arg):
    """Tra ve post_id_numeric tu link / id / Uzpf."""
    arg = arg.strip()
    # Uzpf base64 -> decode
    if arg.startswith("Uzpf"):
        try:
            s = base64.b64decode(arg + "=" * (-len(arg) % 4)).decode("utf-8", "replace")
            for p in s.split(":"):
                if p.isdigit() and len(p) > 8:
                    return p
        except Exception:
            pass
    # tu link: lay so dai nhat sau /posts/ /permalink/ hoac so dai bat ky
    m = re.search(r"(?:posts|permalink)/(\d{8,})", arg)
    if m:
        return m.group(1)
    nums = re.findall(r"\d{8,}", arg)
    # bo group_id neu lan vao
    nums = [n for n in nums if n != "1792625541124212"]
    return nums[-1] if nums else None


def search_index(target):
    """Tra cuu nhanh qua post_index.json (neu da build_index)."""
    p = Path("post_index.json")
    if not p.exists():
        return None
    idx = json.loads(p.read_text(encoding="utf-8"))
    rec = idx.get(str(target))
    if not rec:
        return {"data_V11": None, "dataset_merged": None}
    hits = {"data_V11": None, "dataset_merged": None}
    if rec.get("v11_folder"):
        vp = DATA_V11 / rec["v11_folder"] / "info.json"
        hits["data_V11"] = (rec["v11_folder"], json.loads(vp.read_text(encoding="utf-8")) if vp.exists() else rec)
    if rec.get("merged_folder"):
        mp = MERGED / rec["merged_folder"] / "post_info.json"
        hits["dataset_merged"] = (rec["merged_folder"], json.loads(mp.read_text(encoding="utf-8")) if mp.exists() else {})
    return hits


def search(target):
    cached = search_index(target)
    if cached is not None:
        return cached
    hits = {"data_V11": None, "dataset_merged": None}
    # data_V11: doc info.json (da enrich co post_id_numeric)
    for d in DATA_V11.iterdir():
        if not d.is_dir():
            continue
        ij = d / "info.json"
        if not ij.exists():
            continue
        try:
            j = json.loads(ij.read_text(encoding="utf-8"))
        except Exception:
            continue
        if str(j.get("post_id_numeric")) == target:
            hits["data_V11"] = (d.name, j)
            break
    # dataset_merged: block facebook.post_id_numeric
    for d in MERGED.iterdir():
        if not d.is_dir():
            continue
        pj = d / "post_info.json"
        if not pj.exists():
            continue
        try:
            j = json.loads(pj.read_text(encoding="utf-8"))
        except Exception:
            continue
        fb = j.get("facebook") or {}
        if str(fb.get("post_id_numeric")) == target:
            hits["dataset_merged"] = (d.name, j)
            break
    return hits


def open_viewer(target):
    """Bat cua so GUI post_viewer.py (anh + label + caption + metadata)."""
    import subprocess
    try:
        subprocess.Popen([sys.executable, "post_viewer.py", str(target)])
        print(f"\n🖼  Dang mo cua so xem post (post_viewer.py) ...")
    except Exception as e:
        print(f"\n(Khong mo duoc viewer: {e})")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    do_open = ("--open" in sys.argv) or ("-o" in sys.argv)
    if not args:
        print(__doc__)
        return
    target = parse_arg(args[0])
    if not target:
        print("Khong tach duoc id tu input."); return
    print(f"Tim post_id = {target}\n")
    hits = search(target)

    post_url = None
    if hits["data_V11"]:
        name, j = hits["data_V11"]
        post_url = j.get("post_url")
        print(f"✅ CO TRONG data_V11  ->  folder: {name}")
        print(f"   tac gia : {j.get('author_url')}")
        print(f"   gio cao : {j.get('scrape_time')}")
        print(f"   so anh  : {j.get('img_count')}")
    else:
        print("❌ KHONG thay trong data_V11 (co the bi loc luc cao, vd thieu chu Han / blacklist).")

    print()
    if hits["dataset_merged"]:
        name, j = hits["dataset_merged"]
        fb = j.get("facebook", {})
        post_url = post_url or fb.get("post_url")
        print(f"✅ DA XU LY trong dataset_merged  ->  folder: {name}")
        print(f"   noi nguoc bang: {fb.get('resolved_by')} (v11_folder={fb.get('v11_folder')})")
        print(f"   so cot   : {j.get('columns')}  | visible_ratio: {j.get('visible_ratio')}")
        print(f"   LABEL:")
        for line in (j.get("label") or "").split("\n"):
            print(f"      {line}")
    else:
        print("❌ KHONG co trong dataset_merged (bi loai o buoc loc chat luong, hoac chua xu ly).")

    # Mo cua so GUI neu co --open
    if do_open:
        if hits["dataset_merged"] or hits["data_V11"]:
            open_viewer(target)
        else:
            print("\n(Khong co gi de mo — post khong nam trong data.)")


if __name__ == "__main__":
    main()
