"""
Chay scraper lien tuc den khi nguoi dung Ctrl+C.
Moi session: Facebook cho scroll den gioi han -> tu dong start session moi -> tiep tuc.
Checkpoint duoc luu sau moi session -> moi session nhan dung cho can cao.
"""
import subprocess, sys, io, time, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from pathlib import Path
from datetime import datetime

SCRAPER    = "facebook_scraper_v11.py"
RENAME_PY  = "rename_new.py"
LOG_PREFIX = "run_v11_auto"
PYTHON     = str(Path("venv/Scripts/python.exe").resolve())

session_num = 1

print("=" * 60)
print("SCRAPE FOREVER — Ctrl+C de dung")
print("Moi session: chay den khi Facebook dong feed -> tu dong restart")
print("=" * 60)

while True:
    ts      = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_out = f"{LOG_PREFIX}_{session_num:02d}_{ts}.txt"
    log_err = f"{LOG_PREFIX}_{session_num:02d}_{ts}_err.txt"

    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] SESSION {session_num} bat dau -> log: {log_out}")

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    try:
        with open(log_out, "w", encoding="utf-8") as fout, \
             open(log_err, "w", encoding="utf-8") as ferr:
            proc = subprocess.run(
                [PYTHON, "-u", SCRAPER],
                stdout=fout, stderr=ferr,
                env=env
            )
    except KeyboardInterrupt:
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Nguoi dung dung. Session {session_num} bi huy.")
        break

    # Doc dong cuoi log de bao cao
    try:
        lines = Path(log_out).read_text(encoding="utf-8", errors="replace").splitlines()
        for ln in reversed(lines):
            if "Da luu" in ln or "XONG" in ln or "STOP" in ln or "posts" in ln.lower():
                print(f"  -> {ln.strip()}")
                break
    except:
        pass

    # Rename folders moi
    try:
        subprocess.run([PYTHON, RENAME_PY], capture_output=True, env=env)
        total = sum(1 for _ in Path("data_V11").iterdir() if _.is_dir())
        print(f"  -> data_V11 tong: {total} posts")
    except:
        pass

    session_num += 1

    # Cho 10s giua cac session (de Facebook reset session state)
    print(f"  Cho 10s truoc khi start session {session_num}...")
    try:
        time.sleep(10)
    except KeyboardInterrupt:
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Dung.")
        break

print("\nKet thuc. Xem checkpoint_v11.txt de resume lan sau.")
