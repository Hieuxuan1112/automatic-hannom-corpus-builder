# Automatic Han-Nom Corpus Builder

> *Automatic Retrieval and Construction of a Han-Nom Calligraphy Corpus from the Internet*

An end-to-end system that **automatically crawls Han (Chinese) calligraphy images from
social media** and **produces accurate OCR labels** to build a corpus for research and for
training handwriting-recognition models. This is an undergraduate thesis project in
Knowledge Engineering at the University of Science, VNU-HCM.

> ⚠️ Work in progress — the corpus and pipeline are still being improved.

---

## Problem

Han-Nom calligraphy (couplets, horizontal boards, poems) is shared in large volumes on
social media, but it is **scattered and has no machine-readable labels**, while general OCR
systems perform poorly on running/cursive scripts (connected strokes, many variant forms).
The project exploits one key observation: **posters usually retype the text of the image in
their caption** — a ready source of parallel image ↔ text data that lets us generate labels
without hiring experts to transcribe every sample by hand.

## System architecture

```
    Facebook  ──►  (1) Crawler (Playwright + GraphQL interception)
                        state cursor · SQLite dedup · auto-recovery
                              │
                              ▼
                   (2) Enrich source-tracing metadata
                              │
                              ▼
    image + caption  ──►  (3) LABELING GATE PIPELINE
        │
        ├─ Gemini 3.1 Flash-Lite reads the image (Batch API, 50% cheaper) — as an ANCHOR
        ├─ Levenshtein / partial-ratio alignment (Traditional↔Simplified + variant normalize)
        │     └─ score ≥ 75 → PASS   |   < 75 → reject (caption does not match image)
        ├─ Label = VERBATIM substring of the caption (never text produced by the OCR)
        └─ Line segmentation by physical column via YOLO + de-duplication post-processing
                              │
                              ▼
                   Labeled corpus + source traceability
```

**Core labeling principle:** every label is taken **100% verbatim** from the poster's
caption; the OCR output is only an anchor used to verify and align — never the source of the
label's characters. This eliminates model hallucination from the labels.

## Results

| Metric | Value |
|---|---|
| Raw posts collected | 55,404 |
| Posts sent through the labeling pipeline | 13,071 |
| **Passing samples (final corpus)** | **10,184** (77.9%) |
| Mean Levenshtein similarity | **98.21%** (median 100%) |
| Distinct Han characters | 5,592 |

## Tech stack

`Python` · `Playwright` (GraphQL interception) · `Google Gemini API` (Batch API) ·
`YOLO / Ultralytics` (text-column detection) · `RapidFuzz` (string alignment) ·
`OpenCC` (Traditional/Simplified normalization) · `SQLite` · `aiohttp`

## Source layout

```
src/
├── facebook_scraper_v11.py        Main crawler (deep crawl into the past)
├── facebook_scraper_catchup.py    Catch-up crawler for new posts (separate cursor)
├── scrape_forever.py / scrape_catchup_forever.py   Auto-restart wrappers
├── rename_new.py                  Number the new post folders
├── prep_new_posts.py              Run YOLO + build metadata for new posts
├── batch_api_gate.py              Labeling gate pipeline (Batch API)
├── fix_columns.py                 Post-process: remove overlapping/nested YOLO boxes
├── apply_fixes.py                 Re-apply post-processing (no API calls)
├── pipeline_v15.py                Load YOLO + allocate characters by column height
├── enrich_metadata.py             Decode post_id → source link, author
├── build_index.py                 Build the traceability index
├── find_post.py                   Reverse-lookup a post by link/id
└── runs/detect/.../best.pt        Trained YOLO weights
```

See **[INSTALL.md](INSTALL.md)** and **[USAGE.md](USAGE.md)** to set up and run.

A Dockerized, long-running variant of the crawler (for unattended deployment on a headless
server) is in **[docker-deploy-example/](docker-deploy-example/)**.

## Quick install

```bash
python -m venv venv && venv\Scripts\activate
pip install google-genai rapidfuzz opencc-python-reimplemented pillow numpy playwright aiohttp ultralytics
playwright install msedge
```
Put your Gemini API key in the `API_KEY` variable of `batch_api_gate.py` /
`pipeline_v15.py` (replace `YOUR_GEMINI_API_KEY_HERE`).

## Notes

- This repo does **not** include raw data, the corpus, login cookies, or state-cursor files
  (see `.gitignore`) — for size and security reasons.
- Data is collected from a public community group, reading only public content for academic
  research purposes.

## Author

Ngo Xuan Hieu — Faculty of Information Technology, University of Science (VNU-HCM).
Advisors: Assoc. Prof. Dinh Dien, Dr. Luong An Vinh.
