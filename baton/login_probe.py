from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

REPO = os.environ["GITHUB_REPOSITORY"]
PR = int(os.environ["PR_NUMBER"])
RUN_ID = os.environ["RUN_ID"]
TOKEN = os.environ["GH_TOKEN"]
EMAIL = os.environ.get("CHATGPT_LOGIN_EMAIL", "evanbrown3000@gmail.com")
API = "https://api.github.com"
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}
STATUS = Path("/tmp/chatgpt-login-probe.json")
SCREENSHOT = Path("/tmp/chatgpt-login-probe.png")


def api(method: str, path: str, **kwargs):
    response = requests.request(method, API + path, headers=HEADERS, timeout=40, **kwargs)
    response.raise_for_status()
    return response.json() if response.content else {}


def comment(value: dict):
    body = "### ChatGPT cloud login state\n\n```json\n" + json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n```"
    api("POST", f"/repos/{REPO}/issues/{PR}/comments", json={"body": body})


def normalized(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def visible_texts(page, role: str, limit: int = 40) -> list[str]:
    rows: list[str] = []
    try:
        locator = page.get_by_role(role)
        for index in range(min(locator.count(), limit)):
            item = locator.nth(index)
            if not item.is_visible():
                continue
            try:
                text = normalized(item.inner_text(timeout=500))
            except Exception:
                text = ""
            if text:
                rows.append(text.replace(EMAIL, "<EMAIL>")[:300])
    except Exception:
        pass
    return rows


def snapshot(page, stage: str) -> dict:
    body = ""
    try:
        body = normalized(page.locator("body").inner_text(timeout=4000))
    except Exception:
        pass
    body = body.replace(EMAIL, "<EMAIL>")
    inputs: list[dict[str, str]] = []
    try:
        locator = page.locator("input")
        for index in range(min(locator.count(), 40)):
            item = locator.nth(index)
            if not item.is_visible():
                continue
            inputs.append(
                {
                    "type": str(item.get_attribute("type") or ""),
                    "name": str(item.get_attribute("name") or ""),
                    "autocomplete": str(item.get_attribute("autocomplete") or ""),
                    "inputmode": str(item.get_attribute("inputmode") or ""),
                    "placeholder": str(item.get_attribute("placeholder") or ""),
                    "aria_label": str(item.get_attribute("aria-label") or ""),
                }
            )
    except Exception:
        pass
    return {
        "stage": stage,
        "page_url": page.url,
        "page_title": page.title(),
        "body_excerpt": body[:5000],
        "visible_inputs": inputs,
        "visible_buttons": visible_texts(page, "button"),
        "visible_links": visible_texts(page, "link"),
    }


status = {
    "schema": "cognilode.chatgpt_cloud_login_probe.v2",
    "run_id": RUN_ID,
    "host_used": False,
    "codex_used": False,
    "scheduled_tasks_used": False,
    "work_mode_used": False,
    "stages": [],
}
page = None
browser = None

try:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled", "--disable-dev-shm-usage"],
        )
        context = browser.new_context(viewport={"width": 1440, "height": 1000}, locale="en-US")
        page = context.new_page()
        page.goto("https://chatgpt.com/auth/login", wait_until="domcontentloaded", timeout=60000)
        time.sleep(6)
        status["stages"].append(snapshot(page, "initial_auth_login"))

        email = page.locator('input[type="email"],input[name="email"]')
        if not email.count() or not email.first.is_visible():
            try:
                button = page.get_by_role("button", name=re.compile(r"^log in$", re.I))
                if button.count() and button.first.is_visible():
                    button.first.click(timeout=3000)
                    time.sleep(5)
                    status["stages"].append(snapshot(page, "after_log_in_button"))
            except Exception as exc:
                status["log_in_button_error"] = f"{type(exc).__name__}: {exc}"
            email = page.locator('input[type="email"],input[name="email"]')

        if email.count() and email.first.is_visible():
            email.first.fill(EMAIL)
            button = page.get_by_role("button", name=re.compile(r"continue|next", re.I))
            if button.count() and button.first.is_visible():
                button.first.click(timeout=3000)
                time.sleep(10)
                status["stages"].append(snapshot(page, "after_email_continue"))
            else:
                status["continue_control_missing"] = True
        else:
            status["email_control_missing"] = True

        if page is not None:
            page.screenshot(path=str(SCREENSHOT), full_page=True)
        browser.close()
except Exception as exc:
    status["probe_error"] = f"{type(exc).__name__}: {exc}"
    try:
        if page is not None:
            status["stages"].append(snapshot(page, "exception_state"))
            page.screenshot(path=str(SCREENSHOT), full_page=True)
    except Exception:
        pass
finally:
    STATUS.write_text(json.dumps(status, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    comment(status)
    print(json.dumps(status, sort_keys=True))
