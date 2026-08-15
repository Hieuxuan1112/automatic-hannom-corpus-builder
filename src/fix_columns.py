# -*- coding: utf-8 -*-
"""
fix_columns.py — Khu box cot YOLO trung lap/long nhau + cot de len lac khoan.
  clean_columns(cols, sigs) -> cols_sach
Quy tac:
  (1) SUPER BOX: box chua >=2 box cot khac (moi box >=70% nam trong no) -> bo (box rac bao ca anh).
  (2) NMS long nhau: duyet theo dien tich GIAM dan; box nao bi box da-giu che >=60% dien tich -> bo (ban sao long).
  (3) Cot de len lac khoan: >=60% dien tich cot nam trong 1 box yolo_signatures -> bo.
Chay truc tiep de DO LAI tren 100 post (SEED 100, y het test_feedback100).
"""
import io, sys, json, re, random
if (sys.stdout is not None and hasattr(sys.stdout, "buffer")
        and (getattr(sys.stdout, "encoding", "") or "").lower() not in ("utf-8", "utf8")):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from pathlib import Path

def _inter(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    return ix * iy

def _area(a):
    return max(a[2] - a[0], 0) * max(a[3] - a[1], 0)

def clean_columns(cols, sigs):
    if not cols:
        return cols
    boxes = [c["bbox"] for c in cols]
    keep = list(range(len(cols)))
    # (1) super box: chua >=2 box cot khac -> bo
    drop = set()
    for i in keep:
        contained = sum(1 for k in keep if k != i and _area(boxes[k]) > 0
                        and _inter(boxes[i], boxes[k]) / _area(boxes[k]) >= 0.7)
        if contained >= 2:
            drop.add(i)
    keep = [i for i in keep if i not in drop]
    # (2) NMS long nhau (dien tich giam dan, giu box lon; nguong cao de khong khu cot that hoi de nhau)
    keep.sort(key=lambda i: -_area(boxes[i]))
    kept = []
    for i in keep:
        a = _area(boxes[i])
        if a and all(_inter(boxes[i], boxes[k]) / a < 0.75 for k in kept):
            kept.append(i)
    # (3) de len lac khoan — CHI tin box lac khoan NHO (cao < 50% cot cao nhat);
    #     box sig khong lo la detection rac, bo qua
    max_h = max((c.get("height", 0) for c in cols), default=0) or 1
    small_sigs = [s for s in (sigs or [])
                  if (s["bbox"][3] - s["bbox"][1]) < 0.5 * max_h]
    out = []
    for i in kept:
        a = _area(boxes[i])
        if a and any(_inter(boxes[i], s["bbox"]) / a >= 0.6 for s in small_sigs):
            continue
        out.append(i)
    # guard: khong bao gio tra ve 0 cot
    if not out:
        return cols
    out.sort()  # giu thu tu goc (da sort phai->trai)
    return [cols[i] for i in out]

# ================= DO LAI tren 100 post =================
if __name__ == "__main__":
    def fcn(t): return "".join(re.findall(r"[一-鿿]", (t or "").replace("\n","")))
    def counts_of(text): return [len(fcn(l)) for l in (text or "").split("\n") if fcn(l)]
    def alloc(flat, cols):
        if not cols or not flat: return [flat] if flat else []
        n=len(flat); th=sum(c.get("height",1) for c in cols) or 1
        cnt=[int(round((c.get("height",1)/th)*n)) for c in cols]
        d=n-sum(cnt)
        if d and cnt: cnt[cnt.index(max(cnt))]+=d
        out,s=[],0
        for c in cnt: out.append(flat[s:s+c]); s+=c
        return [x for x in out if x]

    folders=[d for d in sorted(Path("dataset_gate").iterdir()) if d.is_dir()]
    random.seed(100)
    pick=random.sample(folders,100)

    changed=0; extra_before=0; extra_after=0; orphan_before=0; orphan_after=0
    def orphan(lines):
        L=[len(x) for x in lines if x]
        return bool(L) and min(L)==1 and max(L)>=3
    egs=[]
    for d in pick:
        j=json.loads((d/"post_info.json").read_text(encoding="utf-8"))
        cols=j.get("yolo_columns",[]); sigs=j.get("yolo_signatures",[])
        flat=fcn(j.get("label","")); g_ncol=len(counts_of(j.get("ocr_gemini","")))
        cc=clean_columns(cols,sigs)
        if len(cc)!=len(cols):
            changed+=1
            if len(egs)<8: egs.append((d.name,len(cols),len(cc),g_ncol))
        if g_ncol>0:
            if len(cols)>g_ncol: extra_before+=1
            if len(cc)>g_ncol: extra_after+=1
        if flat:
            if orphan(alloc(flat,cols)): orphan_before+=1
            if orphan(alloc(flat,cc)): orphan_after+=1

    print("=== ĐO LẠI 100 post (SEED 100 — cùng bộ với lần trước) ===")
    print(f"Post có cột bị khử (trùng/lồng/lạc khoản): {changed}/100")
    print(f"TEST1  YOLO dư cột so Gemini : {extra_before} -> {extra_after}  (giảm {extra_before-extra_after})")
    print(f"Chữ mồ côi (allocate lỗi)    : {orphan_before} -> {orphan_after}  (giảm {orphan_before-orphan_after})")
    print("\nVí dụ post được sửa (cột: trước -> sau | Gemini đọc):")
    for f,b,a,g in egs: print(f"  {f}: {b} -> {a} cột | Gemini {g}")

    # kiem tra 2 post user xac nhan yolo_wrong
    print("\n=== 2 post bạn xác nhận YOLO sai ===")
    for f in ["06700","08798"]:
        j=json.loads((Path('dataset_gate')/f/'post_info.json').read_text(encoding='utf-8'))
        flat=fcn(j.get("label",""))
        cc=clean_columns(j["yolo_columns"],j["yolo_signatures"])
        print(f"  {f}: {len(j['yolo_columns'])} -> {len(cc)} cột")
        print(f"    label cũ : {' / '.join(alloc(flat,j['yolo_columns']))}")
        print(f"    label MỚI: {' / '.join(alloc(flat,cc))}")
