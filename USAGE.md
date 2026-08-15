# Usage Guide

**Project:** Automatic Retrieval and Construction of a Han-Nom Corpus from the Internet

Run every command from the `src` directory with the virtual environment activated
(`venv\Scripts\activate`).

## Overall flow
```
 (1) Crawl Facebook   ->  data_V11/<post>/ (images + caption + info.json)
 (2) Enrich source-tracing metadata
 (3) Prepare new posts ->  dataset_new/   (run YOLO, build post_info.json)
 (4) Labeling gate pipeline  ->  dataset_gate/
```

## 1. Crawl data (Playwright + GraphQL interception)
```bash
# Deep crawl into the past (uses the checkpoint_v11.txt cursor, auto-recovers):
python scrape_forever.py

# Catch-up crawl for new posts (separate cursor, stops after 300 consecutive duplicates):
python scrape_catchup_forever.py

# Number the newly crawled post folders:
python rename_new.py
```
Each post is a folder `data_V11/<n>/` containing `images/`, `metadata.txt` (the original
caption) and `info.json`. Crawled IDs are de-duplicated with SQLite (`history_v11.db`).

## 2. Enrich source-tracing metadata
```bash
python enrich_metadata.py     # decode post_id into source link, author, crawl time
python build_index.py         # build the post_index.csv index
python find_post.py <link-or-id>   # reverse-lookup a single post
```

## 3. Prepare new posts before labeling
```bash
python -u prep_new_posts.py
```
Newly crawled posts have only `info.json` and `metadata.txt`, without column coordinates.
This step runs YOLO on each image to obtain text-column and signature boxes, and builds a
`post_info.json` in the schema the labeling pipeline expects (writing the caption into
`original_metadata`), producing `dataset_new/`. Adjust `LO` and `HI` inside the file to pick
the folder range. The step skips already-processed posts, so it is safe to re-run.

## 4. Labeling gate pipeline (Gemini 3.1 Flash-Lite, Batch API — 50% cheaper)
Run with default range from the file:
```bash
python -u batch_api_gate.py
```
Or pass a range with its own state file, to run several ranges in parallel:
```bash
python -u batch_api_gate.py <SRC> <OFFSET> <LIMIT> <STATE> <REJECT>
```
Example — the three runs that reproduce the reported results:
```bash
python -u batch_api_gate.py dataset_merged 0    2000 stateA.json rejA.json
python -u batch_api_gate.py dataset_merged 9000 2000 stateB.json rejB.json
python -u batch_api_gate.py dataset_new    0    3000 stateC.json rejC.json
```
Internally: send image + caption to the Batch API → Gemini reads (anchor) → gate scoring with
Levenshtein / partial-ratio (threshold 75, with Traditional/Simplified and variant
normalization) → PASS: label cut verbatim from the caption, lines split by cleaned YOLO
columns → written to `dataset_gate/`. Rejected posts go to a `rejected` file for review.
Progress is stored in a state file; re-running the same command resumes without re-sending
requests (no extra cost).

Re-apply post-processing on existing data (no API calls):
```bash
python -u apply_fixes.py     # remove overlapping/nested YOLO boxes, strip annotation
                             # brackets, rescue boundary characters
```

## Notes
- Labeling principle: labels are taken 100% verbatim from the poster's caption; the Gemini
  OCR output is used only as a verification/alignment anchor.
- `checkpoint*.txt` and `history*.db` produced during crawling must **not** be deleted
  (losing the cursor means re-crawling from scratch).
