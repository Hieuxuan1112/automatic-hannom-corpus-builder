# -*- coding: utf-8 -*-
"""
Cong cu tao cookies.json cho bo cao. Ho tro 2 che do:

--- CACH A: dang nhap tay tren may co man hinh (an toan nhat) ---
    python login.py
  Trinh duyet mo ra -> dang nhap Facebook -> quay lai nhan ENTER -> luu cookies.json.
  Chep cookies.json vao thu muc data/ cua may chu.

--- CACH B: dang nhap tu dong headless (chay duoc tren server khong man hinh) ---
    FB_EMAIL=... FB_PASSWORD=... HEADLESS=1 python login.py
  Script tu dien email/mat khau va luu cookies.json. LUU Y: tai khoan moi + IP server
  la thuong bi Facebook doi captcha/xac minh -> co the that bai. Neu that bai, dung
  Cach A tren may ca nhan roi copy cookies.json len server.

Bien moi truong:
    FB_EMAIL, FB_PASSWORD   thong tin dang nhap (chi Cach B)
    HEADLESS                "1" chay an trinh duyet (mac dinh: 0 = hien de dang nhap tay)
    COOKIES_OUT             ten file cookies (mac dinh cookies.json)
    LOGIN_WAIT              so giay cho sau khi bam dang nhap (mac dinh 25)
"""
import asyncio, json, os, sys
from playwright.async_api import async_playwright

OUT       = os.getenv("COOKIES_OUT", "cookies.json")
HEADLESS  = os.getenv("HEADLESS", "0") == "1"
EMAIL     = os.getenv("FB_EMAIL")
PASSWORD  = os.getenv("FB_PASSWORD")
LOGIN_WAIT = int(os.getenv("LOGIN_WAIT", "25"))


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS, args=["--no-sandbox"])
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        page = await context.new_page()
        await page.goto("https://www.facebook.com/login", timeout=60000)

        if EMAIL and PASSWORD:
            # --- CACH B: dang nhap tu dong ---
            print(">>> Dang nhap tu dong (headless) bang FB_EMAIL/FB_PASSWORD...", flush=True)
            try:
                await page.fill("input[name='email']", EMAIL)
                await page.fill("input[name='pass']", PASSWORD)
                await page.press("input[name='pass']", "Enter")   # submit form, khong phu thuoc selector nut
            except Exception as e:
                print(f"[LOI] Khong dien duoc form dang nhap: {e}", flush=True)
                debug_path = os.path.splitext(OUT)[0] + "_debug.png"
                try:
                    await page.screenshot(path=debug_path, full_page=True)
                    print(f"[DEBUG] Da chup man hinh luc loi -> {os.path.abspath(debug_path)}", flush=True)
                except Exception:
                    pass
            await page.wait_for_timeout(LOGIN_WAIT * 1000)   # cho FB xu ly / chuyen trang
            # kiem tra co dang nhap duoc khong (c_user = da login)
            cookies = await context.cookies()
            if not any(c["name"] == "c_user" for c in cookies):
                print("[CANH BAO] Chua thay cookie 'c_user' — co the bi captcha/xac minh "
                      "hoac sai mat khau. Hay dung CACH A tren may ca nhan.", flush=True)
        else:
            # --- CACH A: dang nhap tay ---
            if HEADLESS:
                print("[LOI] Che do headless nhung khong co FB_EMAIL/FB_PASSWORD. "
                      "Bo HEADLESS de dang nhap tay, hoac cung cap thong tin dang nhap.", flush=True)
                await browser.close(); sys.exit(1)
            print("\n>>> Dang nhap Facebook trong cua so trinh duyet vua mo.", flush=True)
            print(">>> Dang nhap xong, quay lai day va nhan ENTER...", flush=True)
            await asyncio.get_event_loop().run_in_executor(None, input)

        cookies = await context.cookies()
        with open(OUT, "w") as f:
            json.dump(cookies, f)
        ok = any(c["name"] == "c_user" for c in cookies)
        print(f"\n[{'OK' if ok else 'CHUA CHAC'}] Da luu {len(cookies)} cookie -> "
              f"{os.path.abspath(OUT)}"
              + ("" if ok else "  (chua thay c_user — kiem tra lai dang nhap)"), flush=True)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
