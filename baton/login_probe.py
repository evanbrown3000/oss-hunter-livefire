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


def visible_texts(page, role: str, limit: int = 30) -> list[str]:
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
                rows.append(text[:240])
    except Exception:
        pass
    return rows


status = {
    "schema": "cognilode.chatgpt_cloud_login_probe.v1",
    "run_id": RUN_ID,
    "host_used": False,
    "codex_used": False,
    "scheduled_tasks_used": False,
    "work_mode_used": False,
}

try:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled", "--disable-dev-shm-usage"],
        )
        context = browser.new_context(viewport={"width": 1440, "height": 1000}, locale="en-US")
        page = context.new_page()
        page.goto("https://chatgpt.com/auth/login", wait_until="domcontentloaded", timeout=60000)
        time.sleep(3)
        try:
            button = page.get_by_role("button", name=re.compile(r"^log in$", re.I))
            if button.count() and button.first.is_visible():
                button.first.click(timeout=2500)
                time.sleep(2)
        except Exception:
            pass
        email = page.locator('input[type="email"],input[name="email"]')
        if not email.count() or not email.first.is_visible():
            raise RuntimeError("LOGIN_EMAIL_FIELD_NOT_FOUND")
        email.first.fill(EMAIL)
        button = page.get_by_role("button", name=re.compile(r"continue|next", re.I))
        if not button.count() or not button.first.is_visible():
            raise RuntimeError("LOGIN_CONTINUE_NOT_FOUND")
        button.first.click()
        time.sleep(8)

        body = ""
        try:
            body = normalized(page.locator("body").inner_text(timeout=4000))
        except Exception:
            pass
        body = body.replace(EMAIL, "<EMAIL>")
        inputs: list[dict[str, str]] = []
        locator = page.locator("input")
        for index in range(min(locator.count(), 30)):
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
        status.update(
            {
                "page_url": page.url,
                "page_title": page.title(),
                "body_excerpt": body[:4000],
                "visible_inputs": inputs,
                "visible_buttons": visible_texts(page, "button"),
                "visible_links": visible_texts(page, "link"),
            }
        )
        page.screenshot(path=str(SCREENSHOT), full_page=True)
        browser.close()
except Exception as exc:
    status["probe_error"] = f"{type(exc).__name__}: {exc}"
finally:
    STATUS.write_text(json.dumps(status, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    comment(status)
    print(json.dumps(status, sort_keys=True))
