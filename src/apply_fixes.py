# -*- coding: utf-8 -*-
"""
apply_fixes.py — Ap 3 fix vao toan bo dataset_gate (KHONG goi API, co backup):
  #1 Khu cot YOLO trung/long/lac-khoan  (fix_columns.clean_columns)
  #2 Loai ngoac CHU THICH (上聯/下聯/橫批...) khoi caption truoc khi align
  #3 Noi cua so + nhan dien di the (懐/歳/増...) de cuu chu bi hut o bien
Ghi lai: label.txt + post_info.json (label, columns, leven_score, yolo_columns_clean)
Backup trong json: label_v1, leven_score_v1, columns_v1.
Chay: venv\\Scripts\\python.exe -u apply_fixes.py
"""
import io, sys, json, re
if (sys.stdout is not None and hasattr(sys.stdout, "buffer")
        and (getattr(sys.stdout, "encoding", "") or "").lower() not in ("utf-8", "utf8")):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from pathlib import Path
from opencc import OpenCC
from rapidfuzz import fuzz
from fix_columns import clean_columns

GATE = Path("dataset_gate")
_t2s = OpenCC("t2s")

# di the (shinjitai/viet tat) -> phon the, de norm khi so khop
VAR = {"懐":"懷","歳":"歲","増":"增","戦":"戰","経":"經","様":"樣","変":"變",
       "辺":"邊","売":"賣","読":"讀","聴":"聽","桜":"櫻","対":"對","仏":"佛",
       "図":"圖","国":"國","広":"廣","応":"應","円":"圓","栄":"榮","険":"險"}

# ngoac chu thich: chi bo span NGAN chua tu khoa bo cuc cau doi
ANNOT = re.compile(r"[（(【「『〔\[]\s*(上聯|下聯|橫批|横批|上联|下联|横聯|對聯|对联|注)\s*[）)】」』〕\]]")

def fcn(t): return "".join(re.findall(r"[一-鿿]", (t or "").replace("\n", "")))
def norm(s): return _t2s.convert("".join(VAR.get(c, c) for c in s))
def sortstr(s): return "".join(sorted(s))

def strip_annot(caption):
    return ANNOT.sub("", caption or "")

def counts_of(lines): return [len(fcn(l)) for l in lines if fcn(l)]

def extract_label_flat(ocr_flat, cap_o):
    """Align OCR vs caption -> substring verbatim; noi cua so neu hut chu bien."""
    if not ocr_flat or not cap_o:
        return "", 0.0
    on, cn = norm(ocr_flat), norm(cap_o)
    if len(cn) != len(cap_o):          # norm lam doi do dai -> khong map index duoc
        return None, None               # bao hieu: giu label cu
    al = fuzz.partial_ratio_alignment(on, cn)
    score = round(al.score, 1)
    ds, de = al.dest_start, al.dest_end
    best = cap_o[ds:de]
    target = len(ocr_flat)
    if len(best) < target:              # fix #3: noi cua so
        deficit = target - len(best)
        best_sc = fuzz.ratio(sortstr(norm(best)), sortstr(on))
        for L in range(deficit + 1):
            R = deficit - L
            ns, ne = max(0, ds - L), min(len(cap_o), de + R)
            cand = cap_o[ns:ne]
            sc = fuzz.ratio(sortstr(norm(cand)), sortstr(on))
            if sc > best_sc:
                best_sc, best = sc, cand
    return best, score

def allocate(flat, cols):
    if not cols or not flat: return [flat] if flat else []
    n = len(flat); th = sum(c.get("height", 1) for c in cols) or 1
    cnt = [int(round((c.get("height", 1) / th) * n)) for c in cols]
    d = n - sum(cnt)
    if d and cnt: cnt[cnt.index(max(cnt))] += d
    out, s = [], 0
    for c in cnt: out.append(flat[s:s+c]); s += c
    return [x for x in out if x]

def main():
    n = 0; lbl_changed = 0; col_changed = 0; char_gained = 0; annot_hit = 0; skipped = 0
    for d in sorted(GATE.iterdir()):
        if not d.is_dir(): continue
        pj = d / "post_info.json"
        if not pj.exists(): continue
        j = json.loads(pj.read_text(encoding="utf-8"))
        n += 1
        old_label = j.get("label", "")
        cap_raw = j.get("original_metadata", "")
        ocr = j.get("ocr_gemini", "")
        # fix #2
        cap2 = strip_annot(cap_raw)
        if cap2 != cap_raw: annot_hit += 1
        # fix #3 (re-align + expand)
        flat, score = extract_label_flat(fcn(ocr), fcn(cap2))
        if flat is None:
            flat = fcn(old_label); score = j.get("leven_score")
            skipped += 1
        if len(flat) > len(fcn(old_label)): char_gained += 1
        # fix #1
        cols = j.get("yolo_columns", [])
        ccols = clean_columns(cols, j.get("yolo_signatures", []))
        if len(ccols) != len(cols): col_changed += 1
        lines = allocate(flat, ccols)
        label = "\n".join(lines)
        if label != old_label: lbl_changed += 1
        # backup mot lan (khong ghi de backup neu chay lai)
        j.setdefault("label_v1", old_label)
        j.setdefault("leven_score_v1", j.get("leven_score"))
        j.setdefault("columns_v1", j.get("columns"))
        j["label"] = label
        j["columns"] = len(lines)
        j["leven_score"] = score
        j["yolo_columns_clean"] = ccols
        j["fixes_applied"] = ["dedup_columns", "strip_annot_brackets", "boundary_expand_variant"]
        (d / "label.txt").write_text(label, encoding="utf-8")
        pj.write_text(json.dumps(j, ensure_ascii=False, indent=2), encoding="utf-8")
        if n % 400 == 0: print(f"  {n} post...", flush=True)
    print(f"\nXONG {n} post:")
    print(f"  label thay đổi          : {lbl_changed}")
    print(f"  cột YOLO được khử       : {col_changed}")
    print(f"  label được THÊM chữ biên: {char_gained}")
    print(f"  caption có ngoặc chú thích bị lọc: {annot_hit}")
    print(f"  post giữ label cũ (norm đổi độ dài): {skipped}")

if __name__ == "__main__":
    main()
