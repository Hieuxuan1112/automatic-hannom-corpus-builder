# Docker deployment example

A **self-contained, long-running variant** of the crawler in [`../src`](../src), packaged for
a headless server via Docker. It keeps the same crawling approach and resilience mechanisms
as the main system (GraphQL interception, DOM pruning, checkpoint cursor, SQLite dedup,
auto-restart) but only extracts caption + images (no OCR/labeling gate), so it can run
unattended on a machine with no display.

## Resilience mechanisms for long crawls
| Mechanism | Effect |
|---|---|
| **Periodic DOM pruning** (old feed nodes removed every 50 scroll steps) | No memory growth — stable over multi-hour runs |
| **Checkpoint cursor** (written continuously to `/data/checkpoint.txt`) | A stop/crash + restart resumes exactly where it left off |
| **SQLite dedup** (`/data/history.db`) | Never re-downloads a post already seen, across runs |
| **Auto-restart** (feed silence or session crash → new browser, same state) | A dead Facebook session doesn't stop the crawl |
| **Image/media/font requests blocked in-browser** | Lighter bandwidth, faster and more stable scrolling |
| Results appended to `posts.jsonl` | No full-file rewrite per post — fast even at scale |

All state lives under `/data` (a mounted volume), so a container restart never loses progress.

## Files
| File | Role |
|---|---|
| `crawler.py` | Main crawl script |
| `login.py` | Generates `cookies.json` — interactive (headed) or automated (headless) |
| `Dockerfile` | Image build (based on Playwright's official image, Chromium preinstalled) |
| `docker-compose.yml` | Run configuration (volume, env vars, restart policy) |

## Run with Docker Compose
```bash
docker compose build

# Log in (produces /data/cookies.json)
docker compose run --rm \
  -e FB_EMAIL=you@example.com -e FB_PASSWORD=yourpass \
  -e COOKIES_OUT=/data/cookies.json \
  crawler python -u login.py

# Crawl indefinitely, in the background, auto-restarting
docker compose up -d --build
docker compose logs -f
```

Output in `./data/`:
```
data/
├── posts.jsonl             # one line per post: {post_id, label, images, post_url}
├── history.db              # dedup database (do not delete)
├── checkpoint.txt          # crawl cursor (do not delete)
└── <post_id>/
    ├── label.txt
    └── image_000.jpg ...
```

## Configuration (environment variables)
| Variable | Default | Meaning |
|---|---|---|
| `GROUP_URL` | thư pháp group | Group URL to crawl |
| `TARGET_POSTS` | `0` | `0` = crawl indefinitely until the feed is exhausted; set a number to stop after N posts |
| `MAX_DUPES_STOP` | `300` | Stop after N consecutive already-seen posts (reached the previously-crawled region / end of feed) |
| `HEADLESS` | `1` | Run the browser headless (required on a server) |
| `MAX_RESTARTS` | `0` | `0` = unlimited auto-restarts |

## Login
Two ways to produce `cookies.json` (see `login.py`):
- **Manual** (safest): `python login.py` opens a visible browser, log in by hand, press Enter.
- **Automated / headless**: `FB_EMAIL=... FB_PASSWORD=... HEADLESS=1 python login.py`. A new
  account logging in from an unfamiliar server IP is often challenged with a captcha/verification
  step by Facebook and may fail — if so, fall back to the manual method on a personal machine and
  copy `cookies.json` to the server.

`cookies.json`, `history.db`, and `checkpoint.txt` are **not** included in this repository —
they hold session/state data and are excluded via `.gitignore`.
