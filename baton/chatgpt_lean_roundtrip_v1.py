#!/usr/bin/env python3
"""Lean cloud-only ChatGPT A→B→A proof.

Authentication occurs before request accounting. The measured window contains
three intended ChatGPT conversation mutations only: initial prompt to fresh A,
A-generated prompt to distinct fresh B, and B-generated return prompt to A.
No conversation inventory, transcript scan, screenshot, upload, download,
Work mode, Codex, Scheduled Task, phone, or local-PC dependency is used.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import time
from typing import Any
from urllib.parse import urlparse

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from playwright.sync_api import Error as PlaywrightError, Page, sync_playwright

import chatgpt_exact_selfprop_cycle_v3 as legacy

REPO = os.environ["GITHUB_REPOSITORY"]
PR_NUMBER = int(os.environ["PR_NUMBER"])
RUN_ID = os.environ["RUN_ID"]
TRIGGER_PATH = Path(os.environ["TRIGGER_PATH"])
RESULT_PATH = Path(os.environ.get("RESULT_PATH", "/tmp/lean-roundtrip-result.json"))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def b64d(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * ((4 - len(value) % 4) % 4))


def add_comment(body: str) -> dict[str, Any]:
    return legacy.api(
        "POST",
        f"/repos/{REPO}/issues/{PR_NUMBER}/comments",
        json={"body": body[:64000]},
    )


def delete_comment(comment_id: int) -> None:
    try:
        legacy.api("DELETE", f"/repos/{REPO}/issues/comments/{comment_id}")
    except Exception:
        pass


def robust_strict_me(page: Page) -> tuple[bool, dict[str, Any]]:
    """Retry only navigation-destroyed execution contexts."""
    last: Exception | None = None
    for _ in range(20):
        try:
            return legacy.strict_me(page)
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
    raise RuntimeError(f"STRICT_ME_NAVIGATION_RACE:{type(last).__name__ if last else 'unknown'}")


legacy.strict_me = robust_strict_me


def decrypt_bundle(private_key: rsa.RSAPrivateKey, ciphertext: str) -> dict[str, Any]:
    plaintext = private_key.decrypt(
        b64d(ciphertext),
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    data = json.loads(plaintext.decode("utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError("AUTH_BUNDLE_NOT_OBJECT")
    return data


def wait_auth_bundle(private_key: rsa.RSAPrivateKey, proof_id: str, key_sha: str) -> dict[str, Any]:
    pattern = re.compile(
        rf"LEAN_AUTH_BUNDLE_V1\s+proof_id={re.escape(proof_id)}\s+run_id={re.escape(RUN_ID)}\s+"
        rf"key_sha256={re.escape(key_sha)}\s+ciphertext_b64=([A-Za-z0-9_-]+)"
    )
    row, match = legacy.wait_comment(pattern, timeout_s=900)
    data = decrypt_bundle(private_key, match.group(1))
    comment_id = int(row.get("id") or 0)
    add_comment(
        f"LEAN_AUTH_BUNDLE_CONSUMED_V1 proof_id={proof_id} run_id={RUN_ID} "
        f"source_comment_id={comment_id} plaintext_exposed=false"
    )
    if comment_id:
        delete_comment(comment_id)
    return data


def wait_composer(page: Page, timeout_s: int = 90):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        legacy.enforce_chat(page)
        node = legacy.composer(page)
        if node is not None:
            return node
        time.sleep(0.5)
    raise RuntimeError("CHAT_COMPOSER_NOT_FOUND")


def ensure_blank_chat(page: Page) -> None:
    page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=90_000)
    deadline = time.time() + 90
    while time.time() < deadline:
        try:
            legacy.enforce_chat(page)
            snap = legacy.snapshot(page)
            if not snap["user"] and wait_composer(page, 5) is not None:
                return
        except Exception:
            pass
        time.sleep(1)
    raise RuntimeError("FRESH_CHAT_NOT_READY")


def conversation_url(page: Page, timeout_s: int = 45) -> str:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        url = str(page.url or "")
        if re.search(r"https://chatgpt\.com/(?:g/[^/]+/)?c/[A-Za-z0-9-]+", url):
            return url
        time.sleep(0.25)
    raise RuntimeError("CONVERSATION_URL_NOT_STABLE")


def parse_json_object(text: str) -> dict[str, Any]:
    candidates = [text.strip()]
    candidates.extend(
        match.group(1).strip()
        for match in re.finditer(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S | re.I)
    )
    first, last = text.find("{"), text.rfind("}")
    if first >= 0 and last > first:
        candidates.append(text[first : last + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
            if isinstance(value, dict):
                return value
        except Exception:
            continue
    raise RuntimeError("ASSISTANT_JSON_PARSE_FAILED")


def classify_requests(events: list[dict[str, str]], elapsed: float) -> dict[str, Any]:
    telemetry_tokens = (
        "/ces/",
        "telemetry",
        "analytics",
        "sentinel",
        "log_event",
        "/events",
        "/lat/",
    )
    meaningful: list[dict[str, str]] = []
    telemetry: list[dict[str, str]] = []
    reads: list[dict[str, str]] = []
    other_writes: list[dict[str, str]] = []
    for event in events:
        method = event["method"]
        path = event["path"]
        lower = path.lower()
        if any(token in lower for token in telemetry_tokens):
            telemetry.append(event)
        elif method in {"GET", "HEAD", "OPTIONS"}:
            reads.append(event)
        elif (
            "conversation" in lower
            or "message" in lower
            or "upload" in lower
            or "attachment" in lower
            or re.search(r"(^|/)files?(/|$)", lower)
        ):
            meaningful.append(event)
        else:
            other_writes.append(event)

    def counts(items: list[dict[str, str]]) -> dict[str, int]:
        return dict(sorted(Counter(f"{x['method']} {x['path']}" for x in items).items()))

    minutes = max(elapsed / 60.0, 1 / 60.0)
    return {
        "accounting_window_seconds": round(elapsed, 3),
        "all_chatgpt_request_count": len(events),
        "all_by_method_path": counts(events),
        "meaningful_request_count": len(meaningful),
        "meaningful_requests_per_minute": round(len(meaningful) / minutes, 4),
        "meaningful_by_method_path": counts(meaningful),
        "background_read_count": len(reads),
        "background_reads_by_method_path": counts(reads),
        "telemetry_request_count": len(telemetry),
        "telemetry_by_method_path": counts(telemetry),
        "other_write_count": len(other_writes),
        "other_writes_by_method_path": counts(other_writes),
    }


def main() -> int:
    trigger = json.loads(TRIGGER_PATH.read_text(encoding="utf-8"))
    proof_id = str(trigger["proof_id"])
    auth_binding_sha = str(trigger["auth_binding_sha256"])
    a2b_seed = str(trigger["a2b_seed"])
    status: dict[str, Any] = {
        "schema": "cognilode.chatgpt.lean_roundtrip.v1",
        "proof_id": proof_id,
        "run_id": RUN_ID,
        "started_at_utc": utc_now(),
        "proof_complete": False,
        "cloud_runner": "github-hosted",
        "chat_mode_only": True,
        "work_mode_used": False,
        "codex_quota_consumed": 0,
        "scheduled_tasks_used": False,
        "phone_used": False,
        "local_pc_used": False,
        "observer_inventory_calls": 0,
        "conversation_list_calls": 0,
        "transcript_scan_calls": 0,
        "screenshots": 0,
        "uploads_to_chatgpt": 0,
        "downloads_from_chatgpt": 0,
    }
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    public_der = private_key.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    public_b64 = b64e(public_der)
    key_sha = sha(public_b64)
    try:
        add_comment(
            "LEAN_AUTH_KEY_V1 "
            f"proof_id={proof_id} run_id={RUN_ID} key_sha256={key_sha} "
            f"public_der_b64={public_b64} work_mode=0 codex=0 scheduled_tasks=0 phone=0 local_pc=0"
        )
        bundle = wait_auth_bundle(private_key, proof_id, key_sha)
        email = str(bundle.get("email") or "")
        password = str(bundle.get("password") or "")
        recovery_email = str(bundle.get("recovery_email") or email)
        auth_binding = str(bundle.get("auth_binding") or "")
        if not email or not password or not auth_binding:
            raise RuntimeError("AUTH_BUNDLE_FIELDS_MISSING")
        if sha(auth_binding) != auth_binding_sha:
            raise RuntimeError("AUTH_BINDING_SHA_MISMATCH")

        events: list[dict[str, str]] = []
        accounting = False
        accounting_started = 0.0
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=False,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                    "--password-store=basic",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--disable-background-networking",
                    "--disable-component-update",
                    "--disable-sync",
                    "--metrics-recording-only",
                    "--disable-domain-reliability",
                ],
            )
            context = browser.new_context(
                viewport={"width": 1440, "height": 1200},
                locale="en-US",
                timezone_id="America/Chicago",
            )
            try:
                auth_page = context.new_page()
                auth_receipt = legacy.login(
                    auth_page,
                    private_key,
                    proof_id,
                    key_sha,
                    email,
                    password,
                    recovery_email,
                )
                password = "\0" * len(password)
                auth_binding = "\0" * len(auth_binding)
                status["authentication"] = auth_receipt

                # Persist only inside the ephemeral runner so a later failure does
                # not force a second login within this run. It is never uploaded.
                context.storage_state(path="/tmp/lean-authenticated-storage.json")

                page_a = context.new_page()
                page_b = context.new_page()
                ensure_blank_chat(page_a)
                ensure_blank_chat(page_b)

                def record(request: Any) -> None:
                    if not accounting:
                        return
                    parsed = urlparse(request.url)
                    if parsed.hostname != "chatgpt.com":
                        return
                    events.append(
                        {
                            "method": request.method.upper(),
                            "path": parsed.path,
                            "resource_type": request.resource_type,
                        }
                    )

                context.on("request", record)
                accounting = True
                accounting_started = time.monotonic()

                prompt_a = (
                    f"LEAN_ROUNDTRIP_A run={proof_id}. Stay in ordinary Chat mode and use no tools. "
                    f"The seed commitment is {a2b_seed}. Generate a fresh secret beginning A2B_ with "
                    "at least 32 total characters and an exact prompt for a distinct fresh Conversation B. "
                    "The B prompt must include the run, your A2B secret, and instruct B to acknowledge it, "
                    "generate a fresh B2A_ secret, and return an exact prompt for A requiring A to acknowledge "
                    "both secrets and set cycle_complete=true. Reply as one JSON object only with keys run, "
                    "a2b_secret, and prompt_for_b."
                )
                before_a = legacy.send_prompt(page_a, prompt_a)
                a_first_text = legacy.wait_assistant(page_a, len(before_a["assistant"]), timeout_s=480)
                a_url = conversation_url(page_a)
                a_first = parse_json_object(a_first_text)
                a2b = str(a_first.get("a2b_secret") or "")
                prompt_for_b = str(a_first.get("prompt_for_b") or "")
                if a_first.get("run") != proof_id:
                    raise RuntimeError("A_RUN_MISMATCH")
                if not a2b.startswith("A2B_") or len(a2b) < 32:
                    raise RuntimeError("A2B_SECRET_INVALID")
                if proof_id not in prompt_for_b or a2b not in prompt_for_b:
                    raise RuntimeError("A_PROMPT_FOR_B_NOT_BOUND")

                before_b = legacy.send_prompt(page_b, prompt_for_b)
                b_text = legacy.wait_assistant(page_b, len(before_b["assistant"]), timeout_s=480)
                b_url = conversation_url(page_b)
                if b_url == a_url:
                    raise RuntimeError("A_AND_B_NOT_DISTINCT")
                b_result = parse_json_object(b_text)
                b2a = str(b_result.get("b2a_secret") or "")
                prompt_for_a = str(b_result.get("prompt_for_a") or "")
                if b_result.get("run") != proof_id:
                    raise RuntimeError("B_RUN_MISMATCH")
                if str(b_result.get("a2b_ack") or "") != a2b:
                    raise RuntimeError("B_A2B_ACK_MISMATCH")
                if not b2a.startswith("B2A_") or len(b2a) < 32 or b2a == a2b:
                    raise RuntimeError("B2A_SECRET_INVALID")
                if proof_id not in prompt_for_a or a2b not in prompt_for_a or b2a not in prompt_for_a:
                    raise RuntimeError("B_PROMPT_FOR_A_NOT_BOUND")

                before_return = legacy.send_prompt(page_a, prompt_for_a)
                a_return_text = legacy.wait_assistant(page_a, len(before_return["assistant"]), timeout_s=480)
                a_return = parse_json_object(a_return_text)
                if a_return.get("run") != proof_id:
                    raise RuntimeError("A_RETURN_RUN_MISMATCH")
                if str(a_return.get("a2b_ack") or "") != a2b:
                    raise RuntimeError("A_RETURN_A2B_ACK_MISMATCH")
                if str(a_return.get("b2a_ack") or "") != b2a:
                    raise RuntimeError("A_RETURN_B2A_ACK_MISMATCH")
                if a_return.get("cycle_complete") is not True:
                    raise RuntimeError("A_RETURN_CYCLE_INCOMPLETE")

                accounting = False
                elapsed = time.monotonic() - accounting_started
                request_accounting = classify_requests(events, elapsed)
                status.update(
                    {
                        "proof_complete": request_accounting["meaningful_request_count"] <= 6,
                        "completed_at_utc": utc_now(),
                        "a_conversation_id_sha256": sha(a_url.rsplit("/", 1)[-1]),
                        "b_conversation_id_sha256": sha(b_url.rsplit("/", 1)[-1]),
                        "a2b_secret_sha256": sha(a2b),
                        "b2a_secret_sha256": sha(b2a),
                        "a_first_response_sha256": sha(a_first_text),
                        "b_response_sha256": sha(b_text),
                        "a_return_response_sha256": sha(a_return_text),
                        "a_generated_exact_b_prompt": True,
                        "b_generated_exact_a_prompt": True,
                        "a_acknowledged_b_return": True,
                        "fresh_distinct_conversations": True,
                        "request_accounting": request_accounting,
                    }
                )
                if request_accounting["meaningful_request_count"] > 6:
                    status["failure"] = "MEANINGFUL_REQUEST_CAP_EXCEEDED"
                RESULT_PATH.write_text(json.dumps(status, indent=2, sort_keys=True) + "\n", encoding="utf-8")
                add_comment("LEAN_ROUNDTRIP_RESULT_V1\n\n```json\n" + json.dumps(status, indent=2, sort_keys=True) + "\n```")
                return 0 if status["proof_complete"] else 2
            finally:
                accounting = False
                browser.close()
    except Exception as exc:
        status.update(
            {
                "completed_at_utc": utc_now(),
                "proof_complete": False,
                "error_class": type(exc).__name__,
                "failure": str(exc)[:500],
                "failure_fingerprint": sha(f"{type(exc).__name__}:{str(exc)}")[:20],
            }
        )
        RESULT_PATH.write_text(json.dumps(status, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        add_comment("LEAN_ROUNDTRIP_FAILURE_V1 " + json.dumps(status, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
