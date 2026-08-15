# -*- coding: utf-8 -*-
"""
build_index.py — Dung 1 lan, tao file tra cuu nhanh sau khi da enrich_metadata.

Xuat ra:
  post_index.json  -> {post_id_numeric: {...}}  (find_post.py dung, tra cuu O(1))
  post_index.csv   -> bang de NGUOI doc / mo bang Excel
                      cot: post_id_numeric, author_id, post_url,
                           v11_folder, merged_folder, scrape_time, has_label

Chay:  venv\\Scripts\\python.exe build_index.py
"""
import io, sys, json, csv
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from pathlib import Path

DATA_V11 = Path("data_V11")
MERGED   = Path("dataset_merged")


def main():
    idx = {}   # post_id_numeric -> record

    # data_V11 (da enrich)
    n = 0
    for d in DATA_V11.iterdir():
        if not d.is_dir():
            continue
        ij = d / "info.json"
        if not ij.exists():
            continue
        try:
            j = json.loads(ij.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        num = j.get("post_id_numeric")
        if not num:
            continue
        idx[str(num)] = {
            "post_id_numeric": str(num),
            "author_id": j.get("author_id"),
            "post_url": j.get("post_url"),
            "scrape_time": j.get("scrape_time"),
            "v11_folder": d.name,
            "merged_folder": None,
            "has_label": False,
        }
        n += 1
        if n % 10000 == 0:
            print(f"  [V11] {n} ...", flush=True)
    print(f"[V11] {n} post vao index")

    # dataset_merged (da enrich, block facebook)
    m = 0
    for d in MERGED.iterdir():
        if not d.is_dir():
            continue
        pj = d / "post_info.json"
        if not pj.exists():
            continue
        try:
            j = json.loads(pj.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        fb = j.get("facebook") or {}
        num = fb.get("post_id_numeric")
        if not num:
            continue
        num = str(num)
        rec = idx.get(num)
        if rec is None:
            rec = {
                "post_id_numeric": num,
                "author_id": fb.get("author_id"),
                "post_url": fb.get("post_url"),
                "scrape_time": fb.get("scrape_time"),
                "v11_folder": fb.get("v11_folder"),
                "merged_folder": None,
                "has_label": False,
            }
            idx[num] = rec
        rec["merged_folder"] = d.name
        rec["has_label"] = bool((j.get("label") or "").strip())
        m += 1
    print(f"[MERGED] {m} post co id -> da gan merged_folder")

    Path("post_index.json").write_text(json.dumps(idx, ensure_ascii=False), encoding="utf-8")

    with open("post_index.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["post_id_numeric", "author_id", "post_url",
                    "v11_folder", "merged_folder", "scrape_time", "has_label"])
        for r in idx.values():
            w.writerow([r["post_id_numeric"], r["author_id"], r["post_url"],
                        r["v11_folder"], r["merged_folder"], r["scrape_time"], r["has_label"]])

    n_merged = sum(1 for r in idx.values() if r["merged_folder"])
    print(f"\nTONG index: {len(idx)} post")
    print(f"  - co trong dataset_merged: {n_merged}")
    print(f"  - chi co trong data_V11  : {len(idx) - n_merged}")
    print("Da xuat: post_index.json , post_index.csv")


if __name__ == "__main__":
    main()
