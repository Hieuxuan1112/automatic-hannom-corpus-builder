# Installation Guide

**Project:** Automatic Retrieval and Construction of a Han-Nom Corpus from the Internet

Python is interpreted — no build step. Run every command from the `src` directory with the
virtual environment activated (`venv\Scripts\activate`).

## 1. Development environment
- OS: Windows 10/11 (the code uses Windows-style paths).
- Python 3.10 or newer.
- Internet connection (Google Gemini API + Facebook crawling).

## 2. Create a virtual environment and install libraries
From a terminal in the `src` directory:

```bash
python -m venv venv
venv\Scripts\activate
pip install google-genai rapidfuzz opencc-python-reimplemented pillow numpy playwright aiohttp ultralytics
playwright install msedge
```

Library roles:
- **google-genai** — Gemini API (OCR + Batch API).
- **rapidfuzz** — Levenshtein distance / partial-ratio alignment.
- **opencc-python-reimplemented** — Traditional↔Simplified normalization before matching.
- **pillow, numpy** — image handling.
- **playwright, aiohttp** — Facebook crawling (GraphQL interception, image download).
- **ultralytics** — YOLO text-column detection (required by `prep_new_posts.py`; not needed
  if you only process posts that already have stored column coordinates).

## 3. Configure the API key
Files that call the Gemini API (`batch_api_gate.py`, `pipeline_v15.py`) contain:

```python
API_KEY = "YOUR_GEMINI_API_KEY_HERE"
```

Replace this string with your own Gemini API key (create one free at
https://aistudio.google.com/apikey).

## 4. YOLO model
The trained text-column detection weights ship with the repo at:
```
src/runs/detect/calligraphy_det_v1/weights/best.pt
```
No retraining required.

## 5. Facebook login (only for crawling)
On the first crawl the program opens a browser and waits 90 seconds for you to log in to
Facebook manually; the session is saved to `cookies.json` for later runs. For security,
`cookies.json` is **not** included in this repository.
