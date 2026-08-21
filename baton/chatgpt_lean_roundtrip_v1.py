#!/usr/bin/env python3
"""Branch-local launcher repairing the lean proof's recursive navigation wrapper."""
from __future__ import annotations

import importlib
import types
import urllib.request
import time

from playwright.sync_api import Error as PlaywrightError, Page

# Load the reviewed implementation from the repository's canonical main branch.
source = urllib.request.urlopen(
    "https://raw.githubusercontent.com/evanbrown3000/oss-hunter-livefire/main/baton/chatgpt_lean_roundtrip_v1.py",
    timeout=30,
).read().decode("utf-8")
implementation = types.ModuleType("chatgpt_lean_roundtrip_impl_v1")
implementation.__file__ = "chatgpt_lean_roundtrip_impl_v1.py"
exec(compile(source, implementation.__file__, "exec"), implementation.__dict__)

# Executing the canonical implementation exposes its known defect: its retry
# wrapper replaces legacy.strict_me and then calls that same replaced name.
# Reload the provider helper to restore the original function, capture it once,
# then bind a nonrecursive retry wrapper.
legacy = importlib.reload(implementation.legacy)
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
implementation.legacy = legacy


if __name__ == "__main__":
    raise SystemExit(implementation.main())
