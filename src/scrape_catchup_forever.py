# -*- coding: utf-8 -*-
"""
scrape_catchup_forever.py — Chay facebook_scraper_catchup.py LAP LAI cho toi khi CATCH-UP XONG.
- Moi session resume tu checkpoint_catchup.txt (dao sau dan) -> khong mat tien do.
- DUNG HAN khi 1 session bao "300 bai trung lien tiep" (= cham vung da cao = catch-up xong).
- Neu session crash (Edge chet) / feed im -> cho 10s roi restart tiep.
- KHONG dung checkpoint_v11.txt (frontier sau) -> an toan.
Chay: venv\\Scripts\\python.exe scrape_catchup_forever.py
"""
import subprocess, time, sys, os
from pathlib import Path
from datetime import datetime

PY = str(Path("venv/Scripts/python.exe").resolve())
SCRIPT = "facebook_scraper_catchup.py"
DONE_MARK = "bài trùng liên tiếp"   # MAX_DUPES_STOP -> catch-up xong

def main():
    session = 0
    while True:
        session += 1
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        log = f"run_catchup_auto_{session:02d}_{ts}.txt"
        print(f"\n{'='*60}\n[SESSION {session}] {ts} -> log: {log}\n{'='*60}", flush=True)
        with open(log, "w", encoding="utf-8") as f:
            proc = subprocess.run([PY, "-u", SCRIPT], stdout=f, stderr=subprocess.STDOUT)
        try:
            text = Path(log).read_text(encoding="utf-8", errors="replace")
        except Exception:
            text = ""
        npost = text.count("[+] POST")
        if DONE_MARK in text:
            print(f"[SESSION {session}] ✅ CATCH-UP XONG — chạm vùng đã cào (300 trùng). Dừng vòng lặp.", flush=True)
            print(f"  Session này cào thêm ~{npost} dòng POST.", flush=True)
            break
        print(f"[SESSION {session}] ⚠ kết thúc không phải do dupe (crash/feed im). ~{npost} POST. Restart sau 10s...", flush=True)
        time.sleep(10)
    print("\n🎉 CATCH-UP HOÀN TẤT. Nhớ chạy rename_new.py để đánh số folder mới.", flush=True)

if __name__ == "__main__":
    main()
