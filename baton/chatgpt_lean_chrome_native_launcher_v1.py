#!/usr/bin/env python3
"""Lean roundtrip using system Google Chrome and native ChatGPT login."""
from __future__ import annotations

import importlib
import time
import types
import urllib.request

from playwright.sync_api import Error as PlaywrightError, Page

import chatgpt_lean_native_login_launcher_v1 as native

# Recompile the reviewed lean implementation with only one browser-runtime
# change: use the GitHub image's system Google Chrome rather than bundled
# Playwright Chromium. Prompting/accounting logic is unchanged.
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
lean = types.ModuleType("chatgpt_lean_roundtrip_chrome_impl_v1")
lean.__file__ = "chatgpt_lean_roundtrip_chrome_impl_v1.py"
exec(compile(source, lean.__file__, "exec"), lean.__dict__)

# Executing the canonical source rebinds its known recursive strict_me wrapper.
# Restore the provider helper, capture the original probe, and reattach the
# native-first login function to that restored module.
legacy = importlib.reload(lean.legacy)
original_strict_me = legacy.strict_me


def strict_me_with_navigation_retry(page: Page):
    last: Exception | None = None
    for _ in range(20):
        try:
            return original_strict_me(page)
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
native.legacy = legacy
legacy.login = native.native_login
lean.legacy = legacy


if __name__ == "__main__":
    raise SystemExit(lean.main())
