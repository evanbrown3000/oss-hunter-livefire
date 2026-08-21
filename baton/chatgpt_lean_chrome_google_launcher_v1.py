#!/usr/bin/env python3
"""Lean A→B→A proof in system Google Chrome with Google-backed login.

Authentication and bounded stage receipts occur before request accounting. The
measured prompt window remains the reviewed three-send lean implementation.
"""
from __future__ import annotations

import importlib
import json
import re
import time
import types
import urllib.request
from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError, Page

import chatgpt_exact_selfprop_cycle_v3 as legacy_module

# Compile the reviewed lean roundtrip implementation with one browser-runtime
# substitution: system Google Chrome instead of bundled Playwright Chromium.
source = urllib.request.urlopen(
    "https://raw.githubusercontent.com/evanbrown3000/oss-hunter-livefire/main/baton/chatgpt_lean_roundtrip_v1.py",
    timeout=30,
).read().decode("utf-8")
needle = "browser = playwright.chromium.launch(\n"
if needle not in source:
    raise RuntimeError("CHROME_LAUNCH_PATCH_POINT_MISSING")
source = source.replace(
    needle,
    "browser = playwright.chromium.launch(\n                channel=\"chrome\",\n",
    1,
)
lean = types.ModuleType("chatgpt_lean_roundtrip_chrome_google_impl_v1")
lean.__file__ = "chatgpt_lean_roundtrip_chrome_google_impl_v1.py"
exec(compile(source, lean.__file__, "exec"), lean.__dict__)

# Restore the original provider helper after the canonical source installs its
# known recursive wrapper. Capture the original probe before rebinding.
legacy = importlib.reload(legacy_module)
_original_strict_me = legacy.strict_me


def strict_me_with_navigation_retry(page: Page):
    last: Exception | None = None
    for _ in range(30):
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
            time.sleep(0.6)
    raise RuntimeError(
        f"STRICT_ME_NAVIGATION_RACE:{type(last).__name__ if last else 'unknown'}"
    )


legacy.strict_me = strict_me_with_navigation_retry


def stage(proof_id: str, name: str, page: Page, **fields) -> None:
    body = legacy.body_text(page)
    payload = {
        "proof_id": proof_id,
        "run_id": legacy.RUN_ID,
        "stage": name,
        "host": urlparse(str(page.url or "")).hostname or "",
        "path_sha256": legacy.sha(urlparse(str(page.url or "")).path)[:20],
        "body_sha256": legacy.sha(body)[:20],
        "body_nonempty": bool(body),
        "work_mode": False,
        "codex": 0,
        "scheduled_tasks": 0,
        **fields,
    }
    legacy.add_comment("LEAN_GOOGLE_AUTH_STAGE_V1 " + json.dumps(payload, sort_keys=True))


def google_login(
    page: Page,
    private_key,
    proof_id: str,
    key_sha: str,
    email: str,
    password: str,
    recovery_email: str,
):
    page.goto("https://chatgpt.com/auth/login", wait_until="domcontentloaded", timeout=90_000)
    time.sleep(4)
    ok, receipt = legacy.strict_me(page)
    if ok:
        receipt.update(
            {
                "password_submitted": False,
                "recovery_submitted": False,
                "otp_submitted": False,
                "google_backed_path": True,
                "system_google_chrome": True,
            }
        )
        stage(proof_id, "already_authenticated", page)
        return receipt

    legacy.click_role(page, r"^log in$|^sign in$")
    time.sleep(2)
    if not legacy.click_role(page, r"continue with google|sign in with google|google"):
        # Some current login surfaces require submitting the account email first;
        # the provider then redirects the Google-backed account to Google OAuth.
        email_node = legacy.visible(
            page.locator(
                'input[type="email"],input[name="email"],input[autocomplete="email"],input[autocomplete="username"]'
            ),
            editable=True,
        )
        if email_node is not None:
            email_node.fill(email)
            legacy.submit(page)
            stage(proof_id, "chatgpt_email_router_submitted", page)
            time.sleep(6)
        else:
            stage(proof_id, "google_selector_missing", page)
    else:
        stage(proof_id, "google_selector_clicked", page)

    email_submitted = False
    password_submitted = False
    recovery_submitted = False
    otp_submitted = False
    alternative_clicks = 0
    last_stage_key = ""
    deadline = time.time() + 420

    while time.time() < deadline:
        ok, receipt = legacy.strict_me(page)
        if ok:
            receipt.update(
                {
                    "password_submitted": password_submitted,
                    "recovery_submitted": recovery_submitted,
                    "otp_submitted": otp_submitted,
                    "google_backed_path": True,
                    "system_google_chrome": True,
                }
            )
            stage(
                proof_id,
                "authenticated",
                page,
                password_submitted=password_submitted,
                recovery_submitted=recovery_submitted,
                otp_submitted=otp_submitted,
            )
            return receipt

        text = legacy.body_text(page)
        low = text.lower()
        host = urlparse(str(page.url or "")).hostname or ""
        state_key = f"{host}:{legacy.sha(text)[:12]}"
        if state_key != last_stage_key:
            last_stage_key = state_key
            stage(
                proof_id,
                "surface_changed",
                page,
                email_submitted=email_submitted,
                password_submitted=password_submitted,
                recovery_submitted=recovery_submitted,
                otp_submitted=otp_submitted,
            )

        if any(
            token in low
            for token in (
                "captcha",
                "verify you are human",
                "unusual activity",
                "browser or app may not be secure",
            )
        ):
            raise RuntimeError("GOOGLE_AUTH_SECURITY_CHALLENGE_NON_EMAIL")

        otp_node = legacy.visible(
            page.locator(
                'input[autocomplete="one-time-code"],input[inputmode="numeric"],input[name*="code" i],input[id*="code" i]'
            ),
            editable=True,
        )
        if otp_node is not None:
            provider = "google" if host == "accounts.google.com" else "openai"
            delivery = recovery_email if recovery_submitted else email
            stage(proof_id, "otp_requested", page, provider=provider)
            code = legacy.request_otp(private_key, proof_id, key_sha, provider, delivery)
            otp_node.fill(code)
            code = "\0" * len(code)
            legacy.submit(page)
            otp_submitted = True
            stage(proof_id, "otp_submitted", page, provider=provider)
            time.sleep(7)
            continue

        if host == "accounts.google.com":
            identifier = legacy.visible(
                page.locator(
                    'input#identifierId,input[name="identifier"],input[type="email"],input[autocomplete="username"]'
                ),
                editable=True,
            )
            if identifier is not None and not email_submitted:
                identifier.fill(email)
                legacy.submit(page)
                email_submitted = True
                stage(proof_id, "google_identifier_submitted", page)
                time.sleep(6)
                continue

            password_node = legacy.visible(
                page.locator('input[type="password"],input[name="Passwd"]'),
                editable=True,
            )
            if password_node is not None and not password_submitted:
                password_node.fill(password)
                legacy.submit(page)
                password_submitted = True
                stage(proof_id, "google_password_submitted", page)
                time.sleep(7)
                continue

            recovery_input = legacy.visible(
                page.locator(
                    'input[type="email"],input[name*="knowledgePreregisteredEmailResponse" i],input[autocomplete="email"]'
                ),
                editable=True,
            )
            if (
                recovery_input is not None
                and ("recovery email" in low or ("confirm" in low and "email" in low))
                and not recovery_submitted
            ):
                recovery_input.fill(recovery_email)
                legacy.submit(page)
                recovery_submitted = True
                stage(proof_id, "google_recovery_email_submitted", page)
                time.sleep(7)
                continue

            if legacy.click_text_element(
                page,
                (r"recovery email", r"confirm.*email", r"email.*account recovery"),
                exclude=(
                    "phone",
                    "text message",
                    "sms",
                    "passkey",
                    "security key",
                    "authenticator",
                    "backup code",
                    "tap yes",
                ),
            ):
                stage(proof_id, "google_recovery_email_selected", page)
                time.sleep(4)
                continue

            if not password_submitted and legacy.click_text_element(
                page,
                (r"enter your password", r"use your password", r"password instead"),
            ):
                stage(proof_id, "google_password_path_selected", page)
                time.sleep(4)
                continue

            if alternative_clicks < 4 and legacy.click_text_element(
                page,
                (
                    r"try another way",
                    r"use another way",
                    r"more ways to sign in",
                    r"choose another option",
                ),
            ):
                alternative_clicks += 1
                stage(
                    proof_id,
                    "google_alternative_selected",
                    page,
                    alternative_clicks=alternative_clicks,
                )
                time.sleep(4)
                continue

            if re.search(r"continue to openai|openai wants|allow access", text, re.I):
                legacy.submit(page)
                stage(proof_id, "google_consent_submitted", page)
                time.sleep(7)
                continue
        else:
            # A provider redirect may return to ChatGPT with a native password or
            # code surface. Handle it without reopening the Google selection.
            email_node = legacy.visible(
                page.locator(
                    'input[type="email"],input[name="email"],input[autocomplete="email"]'
                ),
                editable=True,
            )
            if email_node is not None and not email_submitted:
                email_node.fill(email)
                legacy.submit(page)
                email_submitted = True
                stage(proof_id, "chatgpt_email_submitted", page)
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
                stage(proof_id, "chatgpt_password_submitted", page)
                time.sleep(7)
                continue

        time.sleep(2)

    stage(
        proof_id,
        "timeout",
        page,
        email_submitted=email_submitted,
        password_submitted=password_submitted,
        recovery_submitted=recovery_submitted,
        otp_submitted=otp_submitted,
    )
    raise RuntimeError("SYSTEM_CHROME_GOOGLE_LOGIN_TIMEOUT")


legacy.login = google_login
lean.legacy = legacy


if __name__ == "__main__":
    raise SystemExit(lean.main())
