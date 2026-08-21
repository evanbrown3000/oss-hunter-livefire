#!/usr/bin/env python3
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import subprocess
import time
from datetime import datetime, timezone
from typing import Any

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from playwright.sync_api import Page, sync_playwright

REPO = os.environ["REPO"]
ISSUE = int(os.environ["CONTROL_ISSUE"])
RUN_ID = os.environ["RUN_ID"]
GH_TOKEN = os.environ["GH_TOKEN"]
API = "https://api.github.com"
H = {
    "Authorization": f"Bearer {GH_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "Content-Type": "application/json",
    "User-Agent": "Cognilode-Public-Chat-Cycle-V1",
}
START = time.time()


def utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def norm(v: Any) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()


def sha(v: str) -> str:
    return hashlib.sha256(v.encode("utf-8")).hexdigest()


def gh(method: str, path: str, data: dict[str, Any] | None = None, ok=(200, 201, 204)) -> Any:
    r = requests.request(method, API + path, headers=H, json=data, timeout=45)
    if r.status_code not in ok:
        raise RuntimeError(f"GITHUB_{method}_{r.status_code}:{path}:{r.text[:500]}")
    if not r.text or r.status_code == 204:
        return {}
    return r.json()


def issue() -> dict[str, Any]:
    return gh("GET", f"/repos/{REPO}/issues/{ISSUE}")


def comments() -> list[dict[str, Any]]:
    return gh("GET", f"/repos/{REPO}/issues/{ISSUE}/comments?per_page=100")


def comment(body: str) -> dict[str, Any]:
    return gh("POST", f"/repos/{REPO}/issues/{ISSUE}/comments", {"body": body[:64000]})


def delete_comment(cid: int | None) -> None:
    if not cid:
        return
    try:
        gh("DELETE", f"/repos/{REPO}/issues/comments/{cid}", ok=(204,))
    except Exception:
        pass


cfg = json.loads(issue().get("body") or "{}")
expected_keys = {"schema", "proof_id", "parent_locator", "generation"}
if set(cfg) != expected_keys:
    raise RuntimeError(f"BAD_CONFIG_KEYS:{sorted(cfg)}")
if cfg["schema"] != "cognilode.public_chat_cycle.v1":
    raise RuntimeError("BAD_SCHEMA")
PROOF = str(cfg["proof_id"])
PARENT_LOCATOR = str(cfg["parent_locator"])
GEN = int(cfg["generation"])
if not re.fullmatch(r"[A-Za-z0-9_.-]{16,120}", PROOF):
    raise RuntimeError("BAD_PROOF_ID")
if not re.fullmatch(r"PARENT_LOCATOR_[a-f0-9]{32}", PARENT_LOCATOR):
    raise RuntimeError("BAD_PARENT_LOCATOR")


def request_secret(kind: str, timeout: int = 420) -> str:
    priv = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    pub = priv.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    req = comment(
        "PUBLIC_CYCLE_SECRET_REQUEST "
        + json.dumps(
            {
                "proof_id": PROOF,
                "run_id": RUN_ID,
                "kind": kind,
                "public_key_der_b64": base64.b64encode(pub).decode("ascii"),
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    pattern = re.compile(
        rf"^PUBLIC_CYCLE_SECRET_REPLY\s+proof_id={re.escape(PROOF)}\s+"
        rf"run_id={re.escape(RUN_ID)}\s+kind={re.escape(kind)}\s+"
        r"ciphertext=([A-Za-z0-9+/=]+)\s*$",
        re.M,
    )
    deadline = time.time() + timeout
    reply_id = None
    plaintext = None
    while time.time() < deadline and plaintext is None:
        for row in reversed(comments()):
            m = pattern.search(str(row.get("body") or ""))
            if not m:
                continue
            plaintext = priv.decrypt(
                base64.b64decode(m.group(1)),
                padding.OAEP(
                    mgf=padding.MGF1(hashes.SHA256()),
                    algorithm=hashes.SHA256(),
                    label=None,
                ),
            ).decode("utf-8")
            reply_id = int(row["id"])
            break
        if plaintext is None:
            time.sleep(2)
    delete_comment(int(req.get("id") or 0))
    delete_comment(reply_id)
    if plaintext is None:
        raise RuntimeError(f"SECRET_TIMEOUT:{kind}")
    return plaintext.strip()


def selected_texts(page: Page) -> list[str]:
    vals = []
    for sel in (
        '[aria-pressed="true"]',
        '[aria-checked="true"]',
        '[aria-selected="true"]',
        '[data-state="checked"]',
        '[data-state="active"]',
    ):
        try:
            nodes = page.locator(sel)
            for i in range(min(nodes.count(), 80)):
                try:
                    if nodes.nth(i).is_visible():
                        t = norm(nodes.nth(i).inner_text(timeout=300))
                        if t:
                            vals.append(t)
                except Exception:
                    pass
        except Exception:
            pass
    return vals


def work_selected(page: Page) -> bool:
    if "/work" in (page.url or "").lower():
        return True
    return any(re.search(r"(^|\b)work(\b|$)", x, re.I) for x in selected_texts(page))


def enforce_chat(page: Page) -> None:
    if not work_selected(page):
        return
    for i in range(min(page.get_by_text("Chat", exact=True).count(), 10)):
        try:
            n = page.get_by_text("Chat", exact=True).nth(i)
            if n.is_visible():
                n.click(timeout=1800)
                time.sleep(1)
                break
        except Exception:
            pass
    if work_selected(page):
        raise RuntimeError("WORK_MODE_SELECTED")


def composer(page: Page):
    for sel in (
        "#prompt-textarea",
        '[data-testid="prompt-textarea"]',
        'div[data-lexical-editor="true"][contenteditable="true"]',
        '[role="textbox"][contenteditable="true"]',
        'div[contenteditable="true"]',
        "textarea",
    ):
        try:
            nodes = page.locator(sel)
            for i in range(min(nodes.count(), 8)):
                n = nodes.nth(i)
                if n.is_visible() and n.is_editable():
                    return n
        except Exception:
            pass
    return None


def wait_composer(page: Page, sec: int = 45):
    end = time.time() + sec
    while time.time() < end:
        n = composer(page)
        if n is not None:
            return n
        for label in ("Accept all", "Allow all", "Got it", "Continue", "Chat with ChatGPT"):
            try:
                q = page.get_by_text(label, exact=True)
                if q.count() and q.first.is_visible():
                    q.first.click(timeout=1200)
            except Exception:
                pass
        time.sleep(1)
    return None


def strict_me(page: Page) -> bool:
    try:
        q = page.evaluate(
            """async()=>{try{const r=await fetch('/backend-api/me');
            return {s:r.status,t:(await r.text()).slice(0,30000)}}catch(e){return {s:0,t:String(e)}}}"""
        )
        if q["s"] != 200:
            return False
        j = json.loads(q["t"])
        u = j.get("user") if isinstance(j.get("user"), dict) else {}
        uid = str(j.get("id") or j.get("user_id") or u.get("id") or "")
        email = str(j.get("email") or u.get("email") or "")
        aid = str(j.get("account_id") or j.get("accountId") or "")
        anon = uid.lower().startswith("ua-") or "anonymous" in uid.lower()
        return not anon and bool((email and "@" in email) or aid or uid)
    except Exception:
        return False


def fill_visible(page: Page, selectors: tuple[str, ...], value: str) -> bool:
    for sel in selectors:
        try:
            nodes = page.locator(sel)
            for i in range(min(nodes.count(), 6)):
                n = nodes.nth(i)
                if n.is_visible():
                    n.fill(value, timeout=6000)
                    return True
        except Exception:
            pass
    return False


def click_role(page: Page, pattern: str) -> bool:
    for role in ("button", "link"):
        try:
            nodes = page.get_by_role(role, name=re.compile(pattern, re.I))
            for i in range(min(nodes.count(), 8)):
                n = nodes.nth(i)
                if n.is_visible():
                    n.click(timeout=5000)
                    return True
        except Exception:
            pass
    return False


def login(page: Page) -> None:
    page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=60000)
    time.sleep(3)
    if strict_me(page) and wait_composer(page, 15) is not None:
        return
    click_role(page, r"log in|sign in")
    time.sleep(2)
    email = request_secret("chatgpt_email")
    if not fill_visible(
        page,
        ('input[type="email"]', 'input[name="email"]', 'input[autocomplete="email"]'),
        email,
    ):
        page.goto("https://auth.openai.com/log-in", wait_until="domcontentloaded", timeout=60000)
        if not fill_visible(
            page,
            ('input[type="email"]', 'input[name="email"]', 'input[autocomplete="email"]'),
            email,
        ):
            raise RuntimeError("EMAIL_FIELD_NOT_FOUND")
    email = "\0" * len(email)
    click_role(page, r"continue|next|log in|sign in")
    time.sleep(3)

    password_fields = page.locator('input[type="password"],input[name="password"],input[autocomplete="current-password"]')
    has_password = False
    try:
        has_password = password_fields.count() and password_fields.first.is_visible()
    except Exception:
        pass
    if has_password:
        password = request_secret("chatgpt_password")
        password_fields.first.fill(password)
        password = "\0" * len(password)
        click_role(page, r"continue|next|log in|sign in|submit")
        time.sleep(4)

    body = norm(page.locator("body").inner_text(timeout=5000))
    if re.search(r"captcha|verify you are human|security key|passkey|unusual activity", body, re.I):
        raise RuntimeError("HUMAN_VERIFICATION_REQUIRED")

    otp_fields = page.locator(
        'input[autocomplete="one-time-code"],input[inputmode="numeric"],input[name*="code" i]'
    )
    needs_otp = False
    try:
        needs_otp = otp_fields.count() and otp_fields.first.is_visible()
    except Exception:
        pass
    if needs_otp or re.search(r"verification code|check your email|one-time code", body, re.I):
        otp = request_secret("chatgpt_email_otp")
        if not fill_visible(
            page,
            ('input[autocomplete="one-time-code"]', 'input[inputmode="numeric"]', 'input[name*="code" i]'),
            otp,
        ):
            raise RuntimeError("OTP_FIELD_NOT_FOUND")
        otp = "\0" * len(otp)
        click_role(page, r"continue|verify|submit|next")
        time.sleep(4)

    page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=60000)
    time.sleep(4)
    if not strict_me(page):
        raise RuntimeError("STRICT_AUTH_NOT_PROVEN")
    if wait_composer(page, 30) is None:
        raise RuntimeError("AUTHENTICATED_COMPOSER_NOT_FOUND")


def send_exact(page: Page, text: str) -> tuple[int, int]:
    box = wait_composer(page, 30)
    if box is None:
        raise RuntimeError("NO_COMPOSER")
    before_users = page.locator('[data-message-author-role="user"]').count()
    before_assistants = page.locator('[data-message-author-role="assistant"]').count()
    box.fill(text)
    sent = False
    for sel in (
        'button[data-testid="send-button"]',
        'button[data-testid="composer-submit-button"]',
        'button[aria-label*="Send" i]',
    ):
        try:
            q = page.locator(sel)
            if q.count() and q.first.is_visible() and q.first.is_enabled():
                q.first.click()
                sent = True
                break
        except Exception:
            pass
    if not sent:
        box.press("Enter")
    deadline = time.time() + 45
    while time.time() < deadline:
        users = page.locator('[data-message-author-role="user"]')
        if users.count() > before_users:
            last = norm(users.last.inner_text(timeout=1500))
            if last == norm(text):
                return users.count(), before_assistants
        time.sleep(0.7)
    raise RuntimeError("PROVIDER_SEND_READBACK_FAILED")


def terminal(page: Page, before_assistants: int, timeout: int = 720) -> str:
    end = time.time() + timeout
    prev = ""
    stable = 0
    while time.time() < end:
        try:
            nodes = page.locator('[data-message-author-role="assistant"]')
            if nodes.count() > before_assistants:
                cur = norm(nodes.last.inner_text(timeout=2000))
                stop = page.locator('button[data-testid="stop-button"],button[aria-label*="Stop" i]')
                active = any(stop.nth(i).is_visible() for i in range(min(stop.count(), 4)))
                stable = stable + 1 if cur and cur == prev else 0
                prev = cur
                if cur and stable >= 2 and not active:
                    return cur
        except Exception:
            stable = 0
        time.sleep(2)
    raise RuntimeError("ASSISTANT_TIMEOUT")


def find_parent(page: Page, timeout: int = 420) -> str:
    end = time.time() + timeout
    while time.time() < end:
        try:
            items = page.evaluate(
                """async()=>{let a=[];for(let o=0;o<140;o+=20){
                let r=await fetch(`/backend-api/conversations?offset=${o}&limit=20&order=updated`);
                if(!r.ok)break;let j=await r.json(),x=j.items||j;if(!Array.isArray(x))break;
                a.push(...x);if(x.length<20)break}return a}"""
            )
            for it in items:
                cid = str(it.get("id") or "")
                if not cid:
                    continue
                try:
                    c = page.evaluate(
                        """async id=>{let r=await fetch('/backend-api/conversation/'+id);
                        return r.ok?await r.json():null}""",
                        cid,
                    )
                    if c and PARENT_LOCATOR in json.dumps(c, ensure_ascii=False):
                        return "https://chatgpt.com/c/" + cid
                except Exception:
                    pass
        except Exception:
            pass
        time.sleep(5)
    raise RuntimeError("PARENT_CONVERSATION_NOT_FOUND")


def wait_idle(page: Page, timeout: int = 900) -> None:
    end = time.time() + timeout
    while time.time() < end:
        try:
            stop = page.locator('button[data-testid="stop-button"],button[aria-label*="Stop" i]')
            active = any(stop.nth(i).is_visible() for i in range(min(stop.count(), 4)))
            if not active and composer(page) is not None:
                return
        except Exception:
            pass
        time.sleep(2)
    raise RuntimeError("PARENT_NOT_IDLE")


state: dict[str, Any] = {
    "schema": "cognilode.public_chat_cycle_proof.v1",
    "proof_id": PROOF,
    "generation": GEN,
    "run_id": RUN_ID,
    "status": "FAILED",
    "work_mode_used": False,
    "codex_quota_used": 0,
    "scheduled_tasks_used": 0,
    "local_pc_used": False,
    "runner": "github_public_hosted",
    "started_at_utc": utc(),
}

try:
    comment(
        "PUBLIC_CYCLE_RUNNER_STARTED "
        + json.dumps(
            {
                "proof_id": PROOF,
                "run_id": RUN_ID,
                "generation": GEN,
                "runner": "github_public_hosted",
                "work": 0,
                "codex": 0,
                "scheduled_tasks": 0,
                "local_pc": 0,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )

    with sync_playwright() as pw:
        launch_errors = []
        browser = None
        for kwargs in (
            {
                "channel": "chrome",
                "headless": False,
                "args": [
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                ],
            },
            {
                "headless": False,
                "args": [
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                ],
            },
        ):
            try:
                browser = pw.chromium.launch(**kwargs)
                break
            except Exception as exc:
                launch_errors.append(f"{type(exc).__name__}:{exc}")
        if browser is None:
            raise RuntimeError("BROWSER_LAUNCH_FAILED:" + " | ".join(launch_errors))

        ctx = browser.new_context(
            viewport={"width": 1440, "height": 1200},
            locale="en-US",
            timezone_id="America/Chicago",
        )
        a = ctx.new_page()
        login(a)
        enforce_chat(a)

        parent_url = find_parent(a)
        state["parent_url"] = parent_url
        comment(
            "PUBLIC_CYCLE_PARENT_FOUND "
            + json.dumps(
                {"proof_id": PROOF, "run_id": RUN_ID, "parent_url": parent_url},
                separators=(",", ":"),
                sort_keys=True,
            )
        )

        b = ctx.new_page()
        b.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=60000)
        time.sleep(3)
        enforce_chat(b)
        if page_users := b.locator('[data-message-author-role="user"]').count():
            try:
                new_chat = b.get_by_text("New chat", exact=True)
                if new_chat.count() and new_chat.first.is_visible():
                    new_chat.first.click(timeout=1800)
                    time.sleep(2)
            except Exception:
                pass
        if b.locator('[data-message-author-role="user"]').count() != 0:
            raise RuntimeError(f"B_NOT_FRESH:{page_users}")

        secret1 = secrets.token_urlsafe(32)
        b_submit = utc()
        b_prompt = f"""PUBLIC_CHAT_CYCLE_V1
PROOF_ID={PROOF}
GENERATION={GEN}
SECRET_PROMPT_1={secret1}
ACTUATOR_SUBMIT_UTC={b_submit}

You are conversation B, a DIFFERENT fresh ordinary ChatGPT.com Chat-mode conversation. This exact text is the first user message that started this conversation. Work mode, Codex, the OpenAI API, ChatGPT Scheduled Tasks, email transport, and Evan's local PC are prohibited.

Before doing substantive work, verify this conversation itself. In your final response include:
B_CONVERSATION_STARTED_UTC=<best provider-visible/start time>
B_ORIGINAL_PROMPT_WAS_THIS_EXACT_MESSAGE=yes
B_USER_MESSAGES_SEEN=1
B_SECRET1={secret1}

Generate:
- SECRET_PROMPT_2: at least 24 URL-safe characters and different from SECRET_PROMPT_1.
- B_CALLBACK_NONCE: at least 24 URL-safe characters.

During THIS turn, use your connected GitHub capability to add exactly one real comment to public repository {REPO}, issue #{ISSUE}. The comment must contain exactly:
B_EXTERNAL_TRIGGER proof_id={PROOF} nonce=<B_CALLBACK_NONCE>
Do not put either secret prompt in GitHub. Do not merely propose the comment: invoke GitHub and obtain the actual comment ID/coordinate.

Finish with:
PUBLIC_CYCLE_B_ACK={PROOF}
B_CONVERSATION_STARTED_UTC=<same time>
B_SECRET1={secret1}
B_SECRET2=<SECRET_PROMPT_2>
B_CALLBACK_NONCE=<same nonce>
B_GITHUB_COMMENT_ID=<actual id or coordinate>
RESOURCE_RECEIPT codex_quota=0 work_mode=0 scheduled_tasks=0 host_pc=0
"""
        after_users, before_assistants = send_exact(b, b_prompt)
        if after_users != 1:
            raise RuntimeError(f"B_FIRST_MESSAGE_COUNT:{after_users}")
        users = b.locator('[data-message-author-role="user"]')
        if users.count() != 1 or norm(users.first.inner_text(timeout=1500)) != norm(b_prompt):
            raise RuntimeError("B_FIRST_BUBBLE_NOT_EXACT")
        if "/c/" not in b.url:
            deadline = time.time() + 30
            while time.time() < deadline and "/c/" not in b.url:
                time.sleep(1)
        if "/c/" not in b.url:
            raise RuntimeError("B_URL_MISSING")

        b_url = b.url
        b_response = terminal(b, before_assistants)
        if f"PUBLIC_CYCLE_B_ACK={PROOF}" not in b_response:
            raise RuntimeError("B_ACK_MISSING")
        if f"B_SECRET1={secret1}" not in b_response:
            raise RuntimeError("B_SECRET1_MISMATCH")
        if "RESOURCE_RECEIPT codex_quota=0 work_mode=0 scheduled_tasks=0 host_pc=0" not in b_response:
            raise RuntimeError("B_RESOURCE_RECEIPT_MISSING")
        m2 = re.search(r"\bB_SECRET2=([A-Za-z0-9_.~-]{24,180})\b", b_response)
        mn = re.search(r"\bB_CALLBACK_NONCE=([A-Za-z0-9_.~-]{24,180})\b", b_response)
        ms = re.search(r"\bB_CONVERSATION_STARTED_UTC=([^\n]+)", b_response)
        if not m2 or not mn or not ms:
            raise RuntimeError("B_TERMINAL_FIELDS_MISSING")
        secret2 = m2.group(1)
        nonce = mn.group(1)
        if secret2 == secret1:
            raise RuntimeError("SECRET2_EQUALS_SECRET1")

        cb_pat = re.compile(
            rf"^B_EXTERNAL_TRIGGER proof_id={re.escape(PROOF)} nonce={re.escape(nonce)}\s*$",
            re.M,
        )
        cb = None
        end = time.time() + 300
        while time.time() < end and cb is None:
            for row in reversed(comments()):
                if cb_pat.search(str(row.get("body") or "")):
                    cb = row
                    break
            if cb is None:
                time.sleep(2)
        if cb is None:
            raise RuntimeError("B_EXTERNAL_CALLBACK_NOT_FOUND")
        cb_id = int(cb["id"])

        state.update(
            {
                "b_url": b_url,
                "b_submit_utc": b_submit,
                "b_started_utc": ms.group(1)[:120],
                "b_first_user_bubble_exact": True,
                "b_user_messages": 1,
                "secret1_sha256": sha(secret1),
                "secret2_sha256": sha(secret2),
                "callback_comment_id": cb_id,
            }
        )
        comment(
            "PUBLIC_CYCLE_B_PROVEN "
            + json.dumps(
                {
                    "proof_id": PROOF,
                    "run_id": RUN_ID,
                    "b_url": b_url,
                    "b_started_utc": ms.group(1)[:120],
                    "first_user_bubble_exact": True,
                    "user_message_count": 1,
                    "callback_comment_id": cb_id,
                    "secret1_sha256": sha(secret1),
                    "secret2_sha256": sha(secret2),
                    "work": 0,
                    "codex": 0,
                    "scheduled_tasks": 0,
                    "local_pc": 0,
                },
                separators=(",", ":"),
                sort_keys=True,
            )
        )

        a.goto(parent_url, wait_until="domcontentloaded", timeout=60000)
        time.sleep(2)
        enforce_chat(a)
        wait_idle(a)
        return_submit = utc()
        return_prompt = f"""PUBLIC_CHAT_CYCLE_RETURN_V1
PROOF_ID={PROOF}
GENERATION={GEN}
SECRET_PROMPT_2={secret2}
B_CONVERSATION_URL={b_url}
B_EXTERNAL_GITHUB_COMMENT_ID={cb_id}
RETURN_ACTUATOR_SUBMIT_UTC={return_submit}
PARENT_LOCATOR={PARENT_LOCATOR}

This exact user message was entered into this EXISTING parent ChatGPT.com conversation by a GitHub-hosted browser runner, after this parent turn caused the runner to start a different fresh ChatGPT conversation B and B caused external GitHub comment {cb_id}. It was not typed by Evan.

The causal path to confirm is:
this parent ChatGPT turn -> external GitHub event -> fresh different ChatGPT B -> B-authored external GitHub event -> this parent conversation's new user turn.

SECRET_PROMPT_2 must differ from the B-starting SECRET_PROMPT_1. Do not use Work mode, Codex, the OpenAI API, ChatGPT Scheduled Tasks, email transport, or Evan's local PC.

Continue the SAME recursion during THIS turn rather than merely reporting it:
1. Preserve the same parent locator exactly: {PARENT_LOCATOR}
2. Generate a fresh proof_id matching [A-Za-z0-9_.-]{{16,120}}.
3. Use connected GitHub to create exactly one new issue in public repository {REPO}.
4. Title it exactly: CHATGPT_PUBLIC_CYCLE <new proof_id>
5. Body must be exactly one JSON object with exactly these keys:
   schema = cognilode.public_chat_cycle.v1
   proof_id = <new proof_id>
   parent_locator = {PARENT_LOCATOR}
   generation = {GEN + 1}
6. That issue creation is the next external event. Do not merely print JSON: invoke GitHub and confirm the actual issue number.
7. Do not create a second issue or retry the same failed mechanism. If the issue creation fails, use a materially different external trigger in the same turn.

Finish with:
PUBLIC_CYCLE_A_RETURN_ACK={PROOF}
A_SECRET2={secret2}
NEXT_PROOF_ID=<new proof id>
NEXT_CONTROL_ISSUE=<actual issue number>
NEXT_PARENT_LOCATOR={PARENT_LOCATOR}
RESOURCE_RECEIPT codex_quota=0 work_mode=0 scheduled_tasks=0 host_pc=0
"""
        parent_users, parent_before = send_exact(a, return_prompt)
        if work_selected(a):
            state["work_mode_used"] = True
            raise RuntimeError("PARENT_WORK_MODE_SELECTED_AFTER_RETURN")
        state.update(
            {
                "status": "RETURN_SUBMITTED",
                "return_submit_utc": return_submit,
                "return_prompt_sha256": sha(return_prompt),
                "parent_user_message_count_after": parent_users,
                "parent_assistant_count_before_return": parent_before,
                "completed_at_utc": utc(),
                "elapsed_s": round(time.time() - START, 1),
            }
        )
        comment(
            "PUBLIC_CYCLE_RETURN_SUBMITTED "
            + json.dumps(
                {
                    "proof_id": PROOF,
                    "run_id": RUN_ID,
                    "generation": GEN,
                    "parent_url": parent_url,
                    "b_url": b_url,
                    "callback_comment_id": cb_id,
                    "return_prompt_sha256": sha(return_prompt),
                    "submitted_at_utc": return_submit,
                    "work": 0,
                    "codex": 0,
                    "scheduled_tasks": 0,
                    "local_pc": 0,
                },
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        browser.close()

except Exception as exc:
    state["status"] = "FAILED"
    state["failure"] = f"{type(exc).__name__}: {exc}"
    state["completed_at_utc"] = utc()
    comment(
        "PUBLIC_CYCLE_FAILED "
        + json.dumps(state, separators=(",", ":"), sort_keys=True)
    )
    raise
