#!/usr/bin/env python3
"""Lean roundtrip launcher using the native ChatGPT email/password path first.

This is a materially different authentication hypothesis from the Google-first
legacy helper. It remains outside the measured prompting window and emits only
bounded hash/status receipts to the owning PR.
"""
from __future__ import annotations

import importlib
import re
import time
from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError, Page

import chatgpt_exact_selfprop_cycle_v3 as legacy_module
import chatgpt_lean_roundtrip_v1 as lean

legacy = importlib.reload(legacy_module)
_original_strict_me = legacy.strict_me


def strict_me_with_navigation_retry(page: Page):
    last: Exception | None = None
    for _ in range(20):
        try:
            return _original_strict_me(page)
        except PlaywrightError as exc:
            last = exc
            text = str(exc).lower()
            if "execution context was destroyed" not in text and "navigation" not in text:
                raise
            try:
                page.wait_for_load_state("domcontentloaded", timeout=10_000)
            except Exception:
                pass
            time.sleep(0.75)
    raise RuntimeError(
        f"STRICT_ME_NAVIGATION_RACE:{type(last).__name__ if last else 'unknown'}"
    )


legacy.strict_me = strict_me_with_navigation_retry


def stage(proof_id: str, name: str, page: Page, **fields) -> None:
    payload = {
        "proof_id": proof_id,
        "run_id": legacy.RUN_ID,
        "stage": name,
        "host": urlparse(str(page.url or "")).hostname or "",
        "body_sha256": legacy.sha(legacy.body_text(page))[:20],
        "work_mode": False,
        "codex": 0,
        "scheduled_tasks": 0,
        **fields,
    }
    legacy.add_comment("LEAN_NATIVE_AUTH_STAGE_V1 " + __import__("json").dumps(payload, sort_keys=True))


def native_login(
    page: Page,
    private_key,
    proof_id: str,
    key_sha: str,
    email: str,
    password: str,
    recovery_email: str,
):
    page.goto("https://chatgpt.com/auth/login", wait_until="domcontentloaded", timeout=90_000)
    time.sleep(3)
    ok, receipt = legacy.strict_me(page)
    if ok:
        receipt.update({"password_submitted": False, "recovery_submitted": False, "otp_submitted": False})
        return receipt

    legacy.click_role(page, r"^log in$|^sign in$")
    time.sleep(2)
    stage(proof_id, "login_surface", page)

    email_submitted = False
    password_submitted = False
    otp_submitted = False
    recovery_submitted = False
    native_selector_clicked = False
    deadline = time.time() + 300

    while time.time() < deadline:
        ok, receipt = legacy.strict_me(page)
        if ok:
            receipt.update(
                {
                    "password_submitted": password_submitted,
                    "recovery_submitted": recovery_submitted,
                    "otp_submitted": otp_submitted,
                    "native_email_path": True,
                }
            )
            stage(proof_id, "authenticated", page, password_submitted=password_submitted, otp_submitted=otp_submitted)
            return receipt

        text = legacy.body_text(page)
        low = text.lower()
        host = urlparse(str(page.url or "")).hostname or ""
        if host == "accounts.google.com":
            raise RuntimeError("NATIVE_PATH_REDIRECTED_TO_GOOGLE")
        if any(token in low for token in ("captcha", "verify you are human", "unusual activity", "browser or app may not be secure")):
            raise RuntimeError("NATIVE_AUTH_SECURITY_CHALLENGE")

        otp_node = legacy.visible(
            page.locator('input[autocomplete="one-time-code"],input[inputmode="numeric"],input[name*="code" i],input[id*="code" i]'),
            editable=True,
        )
        if otp_node is not None:
            stage(proof_id, "otp_requested", page)
            code = legacy.request_otp(private_key, proof_id, key_sha, "openai", email)
            otp_node.fill(code)
            code = "\0" * len(code)
            legacy.submit(page)
            otp_submitted = True
            time.sleep(7)
            continue

        email_node = legacy.visible(
            page.locator('input[type="email"],input[name="email"],input[autocomplete="email"],input[autocomplete="username"]'),
            editable=True,
        )
        if email_node is not None and not email_submitted:
            email_node.fill(email)
            legacy.submit(page)
            email_submitted = True
            stage(proof_id, "email_submitted", page)
            time.sleep(5)
            continue

        password_node = legacy.visible(
            page.locator('input[type="password"],input[name="password"]'),
            editable=True,
        )
        if password_node is not None and not password_submitted:
            password_node.fill(password)
            legacy.submit(page)
            password_submitted = True
            stage(proof_id, "password_submitted", page)
            time.sleep(7)
            continue

        if not native_selector_clicked and legacy.click_text_element(
            page,
            (r"continue.*email", r"use.*email", r"email.*instead", r"log in.*email", r"sign in.*email"),
            exclude=("google", "apple", "microsoft", "phone"),
        ):
            native_selector_clicked = True
            stage(proof_id, "native_selector_clicked", page)
            time.sleep(4)
            continue

        if legacy.click_text_element(page, (r"use.*password", r"password.*instead", r"enter.*password")):
            time.sleep(4)
            continue

        if re.search(r"check your email|code (?:was|has been) sent|enter the code", text, re.I):
            stage(proof_id, "email_code_surface", page)

        time.sleep(2)

    stage(
        proof_id,
        "timeout",
        page,
        email_submitted=email_submitted,
        password_submitted=password_submitted,
        otp_submitted=otp_submitted,
    )
    raise RuntimeError("NATIVE_CHATGPT_LOGIN_TIMEOUT")


legacy.login = native_login
lean.legacy = legacy


if __name__ == "__main__":
    raise SystemExit(lean.main())
