#!/usr/bin/env python3
"""Exact self-propulsion v3 with bounded login diagnostics.

This wrapper does not change the A→B→A semantics. It instruments the existing
login path so a failed cloud login becomes actionable instead of an 8-minute
opaque timeout. No password, OTP, cookie, or reusable credential is logged.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time as _time
from pathlib import Path

import chatgpt_exact_selfprop_cycle_v3 as cycle

DIAG_PATH = Path(os.environ.get("DIAG_PATH", "/tmp/chatgpt-login-diagnostic.json"))
SHOT_PATH = Path(os.environ.get("SHOT_PATH", "/tmp/chatgpt-login-final.png"))
_REAL_SLEEP = _time.sleep


def _safe_text(text: str) -> str:
    text = re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "<email>", text)
    text = re.sub(r"\b\d{6,8}\b", "<code>", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:700]


def _snapshot(page):
    try:
        body = page.locator("body").inner_text(timeout=2500)
    except Exception:
        body = ""
    low = body.lower()
    needles = [
        "verify you are human", "verify it's you", "verify it’s you", "security check",
        "challenge", "turnstile", "captcha", "unusual activity", "try another way",
        "use your passkey", "couldn't sign you in", "couldn’t sign you in",
        "browser or app may not be secure", "confirm your recovery email",
        "check your email", "enter the code", "continue with google", "password",
    ]
    return {
        "url": str(getattr(page, "url", ""))[:500],
        "title": _safe_text(page.title() if hasattr(page, "title") else ""),
        "body_sha256": hashlib.sha256(body.encode("utf-8", "ignore")).hexdigest(),
        "signals": [x for x in needles if x in low],
        "body_excerpt_redacted": _safe_text(body),
    }


def diagnostic_login(page, *args, **kwargs):
    original_login = diagnostic_login._original
    history = []
    last_emit = [0.0]
    last_sig = [None]

    def record(force=False):
        now = _time.monotonic()
        if not force and now - last_emit[0] < 8:
            return
        try:
            snap = _snapshot(page)
        except Exception as exc:
            snap = {"snapshot_error": f"{type(exc).__name__}: {exc}"}
        sig = json.dumps(snap, sort_keys=True)
        if force or sig != last_sig[0]:
            history.append(snap)
            last_sig[0] = sig
        last_emit[0] = now

    def diag_sleep(seconds):
        record(False)
        _REAL_SLEEP(seconds)

    cycle.time.sleep = diag_sleep
    try:
        record(True)
        result = original_login(page, *args, **kwargs)
        record(True)
        DIAG_PATH.write_text(json.dumps({"status": "LOGIN_OK", "history": history}, indent=2), encoding="utf-8")
        return result
    except Exception as exc:
        record(True)
        try:
            page.screenshot(path=str(SHOT_PATH), full_page=True)
        except Exception:
            pass
        DIAG_PATH.write_text(json.dumps({
            "status": "LOGIN_FAILED",
            "failure": f"{type(exc).__name__}: {exc}",
            "history": history[-20:],
        }, indent=2), encoding="utf-8")
        raise
    finally:
        cycle.time.sleep = _REAL_SLEEP


diagnostic_login._original = cycle.login
cycle.login = diagnostic_login

if __name__ == "__main__":
    cycle.main()
