# -*- coding: utf-8 -*-
"""
batch_api_gate.py — PIPELINE CONG-LOC qua BATCH API (giam 50%) — BAN TICH HOP FIX.
  gemini-3.1-flash-lite OCR (prompt fix thu tu)  [Batch API]
  -> gate partial_ratio vs caption (norm = opencc t2s + bang di the VAR)
  -> caption da loc ngoac chu thich + marker khong co tren anh
  -> DAT (>=75): label = substring VERBATIM tu caption (noi cua so cuu chu bien),
     chia cot = clean_columns(YOLO) + allocate theo chieu cao
  -> KHONG DAT: loai (ghi rejected)
Output: dataset_gate/ (noi tiep) | resumable qua STATE_FILE
Chay:  venv\\Scripts\\python.exe -u batch_api_gate.py
"""
import io, sys, json, base64, time, re, shutil
if (sys.stdout is not None and hasattr(sys.stdout, "buffer")
        and (getattr(sys.stdout, "encoding", "") or "").lower() not in ("utf-8", "utf8")):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from pathlib import Path
from google import genai
from opencc import OpenCC
from rapidfuzz import fuzz
from fix_columns import clean_columns

API_KEY    = "YOUR_GEMINI_API_KEY_HERE"
MODEL      = "gemini-3.1-flash-lite"
SRC        = Path("dataset_merged")
OUT        = Path("dataset_gate")
STATE_FILE = Path("batch_gate_state2.json")     # state RIENG cho dot nay
REJECT     = Path("dataset_gate_rejected2.json")
OFFSET     = 4000            # folder 4000-9000 (5000 post chua qua pipeline moi)
LIMIT      = 5000
THRESHOLD  = 75
CHUNK      = 80
POLL_SEC   = 30

# Cho phep chay nhieu khoang doc lap: SRC OFFSET LIMIT STATE_FILE REJECT
if len(sys.argv) >= 6:
    SRC        = Path(sys.argv[1])
    OFFSET     = int(sys.argv[2])
    LIMIT      = int(sys.argv[3])
    STATE_FILE = Path(sys.argv[4])
    REJECT     = Path(sys.argv[5])

_t2s = OpenCC("t2s")
client = genai.Client(api_key=API_KEY)
DROP = ("根據","圖片","錄入","如下","由右","由左","由上","由下","書法內容","以下")
VAR = {"懐":"懷","歳":"歲","増":"增","戦":"戰","経":"經","様":"樣","変":"變",
       "辺":"邊","売":"賣","読":"讀","聴":"聽","桜":"櫻","対":"對","仏":"佛",
       "図":"圖","国":"國","広":"廣","応":"應","円":"圓","栄":"榮","険":"險"}
ANNOT_BR = re.compile(r"[（(【「『〔\[]\s*(上聯|下聯|橫批|横批|上联|下联|横聯|對聯|对联|注)\s*[）)】」』〕\]]")
MARKERS  = ["上聯","下聯","橫批","横批","上联","下联","横聯"]

PROMPT = """Ảnh là thư pháp chữ Hán. Caption bên dưới người đăng gõ (chỉ để THAM KHẢO).
Ghi lại những chữ XUẤT HIỆN TRONG ẢNH (phần chữ chính), MỖI CỘT 1 DÒNG.
THỨ TỰ ĐỌC (RẤT QUAN TRỌNG): đọc mỗi cột DỌC từ TRÊN xuống DƯỚI; các cột từ PHẢI sang TRÁI (cột phải nhất = dòng 1). Câu đối 2 tấm: vế PHẢI là dòng 1. KHÔNG đảo phải-trái.
- Theo ẢNH về thể chữ (phồn/giản); nếu ảnh khác caption thì theo ẢNH.
- Nếu có LẠC KHOẢN (chữ ký/đề tên/ngày nhỏ), ghi DÒNG CUỐI dạng 【落款：...】.
- CHỈ trả chữ Hán, không giải thích, không câu dẫn.

CAPTION:
{cap}"""

def img_b64(p): return base64.b64encode(Path(p).read_bytes()).decode()
def fcn(t): return "".join(re.findall(r"[一-鿿]", (t or "").replace("\n","")))
def norm(s): return _t2s.convert("".join(VAR.get(c, c) for c in s))
def sortstr(s): return "".join(sorted(s))
def strip_lac(text):
    lines=[l.strip() for l in (text or "").split("\n") if l.strip()]
    return [l for l in lines if not any(k in l for k in DROP) and not ("落款" in l or l.startswith("【"))]
def log(m): print(m, flush=True)

def extract_label_flat(ocr_flat, cap_o):
    """Align + noi cua so cuu chu bien. Tra (flat, score) | (None,None) neu norm doi do dai."""
    if not ocr_flat or not cap_o: return "", 0.0
    on, cn = norm(ocr_flat), norm(cap_o)
    if len(cn) != len(cap_o): return None, None
    al = fuzz.partial_ratio_alignment(on, cn)
    score = round(al.score, 1)
    ds, de = al.dest_start, al.dest_end
    best = cap_o[ds:de]
    target = len(ocr_flat)
    if len(best) < target:
        deficit = target - len(best)
        best_sc = fuzz.ratio(sortstr(norm(best)), sortstr(on))
        for L in range(deficit + 1):
            R = deficit - L
            ns, ne = max(0, ds - L), min(len(cap_o), de + R)
            cand = cap_o[ns:ne]
            sc = fuzz.ratio(sortstr(norm(cand)), sortstr(on))
            if sc > best_sc: best_sc, best = sc, cand
    return best, score

def allocate(flat, cols):
    if not cols or not flat: return [flat] if flat else []
    n=len(flat); th=sum(c.get("height",1) for c in cols) or 1
    cnt=[int(round((c.get("height",1)/th)*n)) for c in cols]
    d=n-sum(cnt)
    if d and cnt: cnt[cnt.index(max(cnt))]+=d
    out,s=[],0
    for c in cnt: out.append(flat[s:s+c]); s+=c
    return [x for x in out if x]

# ---------- PHASE 1: submit (build + gui TUNG CHUNK, do ton RAM) ----------
def submit():
    folders = sorted([d for d in SRC.iterdir() if d.is_dir()])[OFFSET:OFFSET+LIMIT]
    metas = []
    for d in folders:
        imgs = sorted((d/"images").glob("*.jpg")) or sorted((d/"images").glob("*"))
        if not imgs: continue
        metas.append((d.name, imgs[0]))
    log(f"[SUBMIT] {len(metas)} post (folder {OFFSET}-{OFFSET+LIMIT}), ~{(len(metas)+CHUNK-1)//CHUNK} job...")
    jobs, order = [], []
    for i in range(0, len(metas), CHUNK):
        batch = metas[i:i+CHUNK]
        srcs = []
        for name, img in batch:
            cap = json.loads((SRC/name/"post_info.json").read_text(encoding="utf-8",errors="replace")).get("original_metadata","")
            srcs.append({"contents":[{"parts":[
                {"inline_data":{"mime_type":"image/jpeg","data":img_b64(img)}},
                {"text":PROMPT.format(cap=cap)}],"role":"user"}],"config":{"temperature":0}})
        job = client.batches.create(model=MODEL, src=srcs)
        jobs.append({"name":job.name,"count":len(batch)})
        order.extend(n for n,_ in batch)
        if (i//CHUNK) % 5 == 0:
            log(f"  gui job {i//CHUNK + 1}/{(len(metas)+CHUNK-1)//CHUNK}")
        STATE_FILE.write_text(json.dumps({"jobs":jobs,"order":order},ensure_ascii=False),encoding="utf-8")
    log(f"[SUBMIT] Xong {len(jobs)} job. Bat dau POLL...")

# ---------- PHASE 2+3: poll + collect ----------
def poll_collect():
    st=json.loads(STATE_FILE.read_text(encoding="utf-8"))
    jobs,order=st["jobs"],st["order"]
    while True:
        texts=[]; ndone=0; alldone=True
        for j in jobs:
            try:
                b=client.batches.get(name=j["name"])
            except Exception as e:
                # loi tam thoi (503...) -> coi nhu job chua xong, poll lai vong sau
                log(f"  (poll loi tam thoi: {str(e)[:60]} — thu lai vong sau)")
                alldone=False; texts.extend([""]*j["count"]); continue
            s=str(b.state)
            if "SUCCEEDED" in s:
                ndone+=1
                resps=b.dest.inlined_responses if (b.dest and b.dest.inlined_responses) else []
                out=[]
                for r in resps:
                    try: out.append(r.response.candidates[0].content.parts[0].text)
                    except Exception: out.append("")
                while len(out)<j["count"]: out.append("")
                texts.extend(out[:j["count"]])
            elif any(x in s for x in ("FAILED","EXPIRED","CANCELLED")):
                ndone+=1; texts.extend([""]*j["count"]); log(f"  ⚠ job {s.split('.')[-1]}")
            else:
                alldone=False; texts.extend([""]*j["count"])
        log(f"[POLL] {ndone}/{len(jobs)} job xong")
        if alldone: break
        time.sleep(POLL_SEC)

    log("[COLLECT] Gate + fix + chia cot + ghi output...")
    OUT.mkdir(exist_ok=True)
    npass=0; rejected=[]
    for name,txt in zip(order,texts):
        d=SRC/name
        sj=json.loads((d/"post_info.json").read_text(encoding="utf-8",errors="replace"))
        cap_raw=sj.get("original_metadata","")
        ocr_lines=strip_lac(txt); ocr="\n".join(ocr_lines)
        ocr_flat=fcn(ocr)
        # fix ngoac chu thich + marker khong co tren anh
        cap2=ANNOT_BR.sub("", cap_raw)
        for m in MARKERS:
            if m in cap2 and m not in ocr_flat:
                cap2=cap2.replace(m,"")
        cap_o=fcn(cap2)
        flat,score=extract_label_flat(ocr_flat,cap_o)
        if flat is None: flat,score="",0.0
        if score>=THRESHOLD and flat:
            cols=sj.get("yolo_columns",[])
            ccols=clean_columns(cols, sj.get("yolo_signatures",[]))
            lines=allocate(flat,ccols)
            label="\n".join(lines)
            dest=OUT/name; dest.mkdir(exist_ok=True); (dest/"images").mkdir(exist_ok=True)
            imgs=sorted((d/"images").glob("*.jpg")) or sorted((d/"images").glob("*"))
            if imgs: shutil.copy2(imgs[0],dest/"images"/imgs[0].name)
            (dest/"metadata.txt").write_text(cap_raw,encoding="utf-8")
            (dest/"label.txt").write_text(label,encoding="utf-8")
            (dest/"post_info.json").write_text(json.dumps({
                "post_id":name,"status":"SUCCESS","model":MODEL,"batch_api":True,
                "label":label,"label_source":"caption_verbatim","split_by":"YOLO_clean",
                "columns":len(lines),"leven_score":score,"ocr_gemini":ocr,
                "original_metadata":cap_raw,
                "yolo_columns":cols,"yolo_columns_clean":ccols,
                "yolo_signatures":sj.get("yolo_signatures",[]),
                "facebook":sj.get("facebook",{}),
                "fixes_applied":["dedup_columns","strip_annot_brackets","boundary_expand_variant","strip_unbracketed_marker"],
            },ensure_ascii=False,indent=2),encoding="utf-8")
            npass+=1
        else:
            rejected.append({"folder":name,"score":score,"ocr":ocr,"caption":cap_raw})
    REJECT.write_text(json.dumps(rejected,ensure_ascii=False,indent=2),encoding="utf-8")
    log(f"[DONE] ĐẠT {npass}/{len(order)} -> {OUT}/  |  KHÔNG ĐẠT {len(rejected)} -> {REJECT}")

def main():
    if not STATE_FILE.exists():
        log(f"=== BATCH API GATE v2 | model={MODEL} | folder {OFFSET}-{OFFSET+LIMIT} | ngưỡng {THRESHOLD}% | fix tích hợp ===")
        submit()
    else:
        log("[RESUME] Da co state -> poll tiep.")
    poll_collect()

if __name__ == "__main__":
    main()
