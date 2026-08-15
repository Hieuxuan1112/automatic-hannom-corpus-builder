# -*- coding: utf-8 -*-
"""Chuan bi 2348 post catch-up (data_V11 folder 53056-55403) cho pipeline cong-loc.
Chay YOLO lay cot chu + dung post_info.json tuong thich -> dataset_new/.
Resumable: bo qua post da co post_info.json."""
import io, sys, json
if (sys.stdout is not None and hasattr(sys.stdout, "buffer")
        and (getattr(sys.stdout, "encoding", "") or "").lower() not in ("utf-8", "utf8")):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
import shutil
from pathlib import Path
from ultralytics import YOLO

SRC   = Path("data_V11")
OUT   = Path("dataset_new")
LO, HI = 53056, 55403
YOLO_MODEL_PATH = "runs/detect/calligraphy_det_v1/weights/best.pt"
model = YOLO(YOLO_MODEL_PATH)

def yolo_layout(img):
    res = model.predict(source=str(img), conf=0.25, verbose=False)
    cols, sigs = [], []
    for box in res[0].boxes:
        cls  = int(box.cls[0]); xyxy = box.xyxy[0].tolist()
        entry = {"bbox":xyxy, "class":cls, "conf":float(box.conf[0]),
                 "center_x":(xyxy[0]+xyxy[2])/2, "height":xyxy[3]-xyxy[1]}
        (cols if cls==0 else sigs if cls==1 else []).append(entry)
    cols = sorted(cols, key=lambda x:x["center_x"], reverse=True)
    return cols, sigs

def fb_block(info):
    return {k:info.get(k) for k in ("post_url","author_url","author_name","author_id",
            "publish_time","scrape_time","post_id_fb","post_id_numeric","group_id","group_name")}

OUT.mkdir(exist_ok=True)
folders = sorted([d for d in SRC.iterdir() if d.is_dir() and d.name.isdigit()
                  and LO <= int(d.name) <= HI], key=lambda d:int(d.name))
print(f"Chuan bi {len(folders)} post moi...", flush=True)
done = skip = 0
for i, d in enumerate(folders):
    name = d.name
    dest = OUT / name
    if (dest/"post_info.json").exists():
        skip += 1; continue
    imgs = sorted((d/"images").glob("*.jpg")) or sorted((d/"images").glob("*"))
    if not imgs: continue
    cap = (d/"metadata.txt").read_text(encoding="utf-8", errors="replace") if (d/"metadata.txt").exists() else ""
    try:
        info = json.loads((d/"info.json").read_text(encoding="utf-8", errors="replace"))
    except Exception:
        info = {}
    cols, sigs = yolo_layout(imgs[0])
    dest.mkdir(exist_ok=True); (dest/"images").mkdir(exist_ok=True)
    shutil.copy2(imgs[0], dest/"images"/imgs[0].name)
    (dest/"metadata.txt").write_text(cap, encoding="utf-8")
    (dest/"post_info.json").write_text(json.dumps({
        "post_id":name, "original_metadata":cap,
        "yolo_columns":cols, "yolo_signatures":sigs,
        "facebook":fb_block(info), "source":"catchup_2026",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    done += 1
    if done % 100 == 0:
        print(f"  {i+1}/{len(folders)} (moi {done}, bo qua {skip})", flush=True)
print(f"[XONG] tao {done} post, bo qua {skip}. -> {OUT}/", flush=True)
