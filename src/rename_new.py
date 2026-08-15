import os
from pathlib import Path

DATA_DIR = Path("D:/Facebook-crappe/data_V11")

# Tim so lon nhat trong cac folder da danh so
max_num = 0
for d in DATA_DIR.iterdir():
    if d.is_dir() and d.name.isdigit():
        max_num = max(max_num, int(d.name))

print(f"Max numbered folder: {max_num}")

# Tim cac folder dang co ten post_Uzpf...
new_folders = sorted(
    [d for d in DATA_DIR.iterdir() if d.is_dir() and d.name.startswith("post_")],
    key=lambda d: d.stat().st_ctime
)

print(f"Folders to rename: {len(new_folders)}")

idx = max_num + 1
for f in new_folders:
    new_name = str(idx).zfill(4) if idx < 10000 else str(idx)
    dest = DATA_DIR / new_name
    f.rename(dest)
    idx += 1

print(f"Renamed {len(new_folders)} folders: {max_num+1} to {idx-1}")
print(f"Total folders now: {sum(1 for d in DATA_DIR.iterdir() if d.is_dir())}")
