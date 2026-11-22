"""Regenerate the README screenshots (docs only; not part of the app or CI).

  pip install playwright             # uses the installed Edge or Chrome, no browser download needed
  PARTPILOT_DEMO_PASSWORD=... python docs/make_screenshots.py

Starts the Streamlit UI in offline mode on a spare port, logs in as several demo users, asks one question each and
saves PNGs to docs/screenshots/. The demo accounts must exist (python -m partpilot.auth init config/accounts.example.csv)."""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "screenshots")
PW = os.environ["PARTPILOT_DEMO_PASSWORD"]


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait(url, secs=300):
    t0 = time.time()
    while time.time() - t0 < secs:
        try:
            urllib.request.urlopen(url + "/_stcore/health", timeout=2)
            return
        except OSError:
            time.sleep(1)
    raise RuntimeError("server did not start")


def login(page, url, user):
    page.goto(url, wait_until="domcontentloaded")
    page.get_by_label("Username").fill(user)
    page.locator("input[type=password]").fill(PW)
    page.get_by_role("button", name="Log in").click()
    page.get_by_text("signed in as").wait_for()


def ask(page, q):
    box = page.get_by_placeholder("Ask about BOMs")
    box.fill(q)
    box.press("Enter")
    page.get_by_text("Basis:").last.wait_for()
    time.sleep(3)


def main():
    os.makedirs(OUT, exist_ok=True)
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    env = dict(os.environ, PARTPILOT_LLM_PROVIDER=os.getenv("PARTPILOT_LLM_PROVIDER", "offline"),
               PARTPILOT_AUDIT=os.path.join(ROOT, "state", "audit.jsonl"))
    srv = subprocess.Popen([sys.executable, "-m", "streamlit", "run", "streamlit_app.py", "--server.port", str(port), "--server.address", "127.0.0.1",
                            "--server.headless", "true", "--browser.gatherUsageStats", "false"], cwd=ROOT, env=env)
    try:
        wait(url)
        with sync_playwright() as p:
            browser = None
            for channel in ("msedge", "chrome", None):
                try:
                    browser = p.chromium.launch(channel=channel) if channel else p.chromium.launch()
                    break
                except Exception:
                    continue
            page = browser.new_page(viewport={"width": 1400, "height": 900}, device_scale_factor=1)
            page.set_default_timeout(180000)
            page.goto(url, wait_until="domcontentloaded")
            page.get_by_role("button", name="Log in").wait_for()
            time.sleep(1)
            page.screenshot(path=os.path.join(OUT, "01_login.png"))

            login(page, url, "planner")
            ask(page, "How many BRD-UNO3 can North Ridge build on 2027-03-15 using stock, open POs and approved substitutes?")
            page.locator("details summary").last.click()  # expand the tool-call expander
            time.sleep(2)
            page.screenshot(path=os.path.join(OUT, "02_planner_chat.png"))

            login(page, url, "lead_nrg")
            ask(page, "How much RP2040 is in stock at Elmstead (ELM)?")
            page.screenshot(path=os.path.join(OUT, "03_plant_lead_denied.png"))

            login(page, url, "eng_semis")
            ask(page, "Can the LM1117MPX-5.0 replace the NCP1117ST50T3G on the UNO board ASM-11000?")
            page.screenshot(path=os.path.join(OUT, "04_engineer_substitute.png"))
            page.get_by_text("Review", exact=True).click()
            page.get_by_text("SUB-0017").first.wait_for()
            time.sleep(3)
            page.screenshot(path=os.path.join(OUT, "05_engineer_review.png"))

            login(page, url, "eng_passives")
            page.get_by_text("Review", exact=True).click()
            page.get_by_text("NOTE TO THE ASSISTANT").wait_for()
            time.sleep(3)
            page.screenshot(path=os.path.join(OUT, "06_review_injection_remark.png"), full_page=True)

            login(page, url, "auditor")
            page.get_by_text("Audit log").wait_for()
            time.sleep(1)
            page.screenshot(path=os.path.join(OUT, "07_auditor.png"))
            page.get_by_text("Data quality", exact=True).click()
            page.get_by_text("data cleaning report").wait_for()
            time.sleep(1)
            page.screenshot(path=os.path.join(OUT, "08_data_quality.png"))
            browser.close()
    finally:
        srv.terminate()
    print("saved to", OUT)


if __name__ == "__main__":
    main()
