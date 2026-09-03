# Image chinh thuc cua Playwright: da co san Chromium + moi thu vien he thong
FROM mcr.microsoft.com/playwright/python:v1.48.0-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY crawler.py login.py .

# Trang thai + ket qua ghi ra /data (mount volume de song sot qua cac lan restart)
ENV OUTPUT_DIR=/data \
    TARGET_POSTS=0 \
    HEADLESS=1 \
    MAX_DUPES_STOP=300

CMD ["python", "-u", "crawler.py"]
