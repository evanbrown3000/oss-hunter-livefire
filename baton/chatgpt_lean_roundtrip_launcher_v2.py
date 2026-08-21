#!/usr/bin/env python3
"""Launch the lean roundtrip after repairing the legacy navigation race binding."""
from __future__ import annotations

import importlib
import time

from playwright.sync_api import Error as PlaywrightError, Page

import chatgpt_exact_selfprop_cycle_v3 as legacy_module
import chatgpt_lean_roundtrip_v1 as lean

# Importing v1 exposed a recursion bug: its retry wrapper replaced
# legacy.strict_me and then called legacy.strict_me. Reload restores the actual
# provider probe; this wrapper captures that original callable before rebinding.
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
lean.legacy = legacy


if __name__ == "__main__":
    raise SystemExit(lean.main())
