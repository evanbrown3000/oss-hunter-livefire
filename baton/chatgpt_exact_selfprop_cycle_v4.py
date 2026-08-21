#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import queue
import re
import socket
import subprocess
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import requests

import chatgpt_exact_selfprop_cycle_v3 as core

ORIGINAL_ADD_COMMENT = core.add_comment
ORIGINAL_WAIT_COMMENT = core.wait_comment
CLOUDFLARED = Path("/tmp/cloudflared-selfprop-v4")
INBOX: dict[str, "queue.Queue[str]"] = {
    "auth": queue.Queue(maxsize=1),
    "otp": queue.Queue(maxsize=8),
}
STATE: dict[str, Any] = {
    "proof_id": "",
    "run_id": core.RUN_ID,
    "key_sha256": "",
    "ingress_url": "",
    "server": None,
    "cloudflared": None,
}


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _download_cloudflared() -> None:
    if CLOUDFLARED.exists() and CLOUDFLARED.stat().st_size > 20_000_000:
        return
    url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"
    with requests.get(url, stream=True, timeout=120, allow_redirects=True) as response:
        response.raise_for_status()
        with CLOUDFLARED.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    handle.write(chunk)
    CLOUDFLARED.chmod(0o700)


class IngressHandler(BaseHTTPRequestHandler):
    server_version = "CognilodeSelfpropIngress/4"

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _reply(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/submit":
            self._reply(404, {"ok": False, "error": "not_found"})
            return
        params = urllib.parse.parse_qs(parsed.query, keep_blank_values=False)
        proof_id = str((params.get("proof_id") or [""])[0])
        run_id = str((params.get("run_id") or [""])[0])
        key_sha = str((params.get("key_sha256") or [""])[0])
        kind = str((params.get("kind") or [""])[0])
        ciphertext = str((params.get("ciphertext_b64") or [""])[0])
        if proof_id != STATE["proof_id"] or run_id != STATE["run_id"] or key_sha != STATE["key_sha256"]:
            self._reply(403, {"ok": False, "error": "binding_mismatch"})
            return
        if kind not in INBOX or not re.fullmatch(r"[A-Za-z0-9_-]{100,2000}", ciphertext):
            self._reply(400, {"ok": False, "error": "payload_invalid"})
            return
        try:
            INBOX[kind].put_nowait(ciphertext)
        except queue.Full:
            self._reply(409, {"ok": False, "error": "already_received"})
            return
        self._reply(200, {"ok": True, "kind": kind, "ciphertext_chars": len(ciphertext), "received_at": core.utc_now()})


def _start_ingress() -> str:
    if STATE["ingress_url"]:
        return str(STATE["ingress_url"])
    _download_cloudflared()
    port = _free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), IngressHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    process = subprocess.Popen(
        [str(CLOUDFLARED), "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    deadline = time.time() + 90
    url = ""
    captured: list[str] = []
    assert process.stdout is not None
    while time.time() < deadline:
        line = process.stdout.readline()
        if line:
            captured.append(line[-1000:])
            match = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line, re.I)
            if match:
                url = match.group(0)
                break
        elif process.poll() is not None:
            break
        else:
            time.sleep(0.2)
    if not url:
        process.terminate()
        raise RuntimeError("QUICK_TUNNEL_URL_NOT_OBSERVED:" + "".join(captured)[-2000:])
    STATE.update({"ingress_url": url, "server": server, "cloudflared": process})
    return url


def add_comment_v4(body: str) -> dict[str, Any]:
    if body.startswith("CYCLE_KEY_V3 "):
        proof_match = re.search(r"\bproof_id=([^\s]+)", body)
        key_match = re.search(r"\bkey_sha256=([0-9a-f]{64})", body)
        if not proof_match or not key_match:
            raise RuntimeError("KEY_COMMENT_BINDING_PARSE_FAILED")
        STATE["proof_id"] = proof_match.group(1)
        STATE["key_sha256"] = key_match.group(1)
        ingress = _start_ingress()
        body = (
            body
            + f" auth_transport=cloudflare_quick_tunnel_get_v4 ingress_url={ingress}/submit"
            + " ingress_plaintext=false single_use=true"
        )
    elif body.startswith("CYCLE_OTP_REQUEST_V3 "):
        ingress = _start_ingress()
        body = body + f" otp_transport=cloudflare_quick_tunnel_get_v4 ingress_url={ingress}/submit"
    return ORIGINAL_ADD_COMMENT(body)


def wait_comment_v4(pattern: re.Pattern[str], *, timeout_s: int, after_epoch: float = 0):
    pattern_text = pattern.pattern
    if "CYCLE_AUTH_BUNDLE_V3" in pattern_text:
        try:
            ciphertext = INBOX["auth"].get(timeout=timeout_s)
        except queue.Empty as exc:
            raise RuntimeError("QUICK_TUNNEL_AUTH_TIMEOUT") from exc
        body = (
            f"CYCLE_AUTH_BUNDLE_V3 proof_id={STATE['proof_id']} run_id={STATE['run_id']} "
            f"key_sha256={STATE['key_sha256']} ciphertext_b64={ciphertext}"
        )
        match = pattern.search(body)
        if match is None:
            raise RuntimeError("QUICK_TUNNEL_AUTH_SYNTHETIC_MATCH_FAILED")
        return {"id": f"quick-tunnel-auth:{core.utc_now()}", "created_at": core.utc_now()}, match
    if "CYCLE_OTP_BUNDLE_V3" in pattern_text:
        try:
            ciphertext = INBOX["otp"].get(timeout=timeout_s)
        except queue.Empty as exc:
            raise RuntimeError("QUICK_TUNNEL_OTP_TIMEOUT") from exc
        body = (
            f"CYCLE_OTP_BUNDLE_V3 proof_id={STATE['proof_id']} run_id={STATE['run_id']} "
            f"key_sha256={STATE['key_sha256']} ciphertext_b64={ciphertext}"
        )
        match = pattern.search(body)
        if match is None:
            raise RuntimeError("QUICK_TUNNEL_OTP_SYNTHETIC_MATCH_FAILED")
        return {"id": f"quick-tunnel-otp:{core.utc_now()}", "created_at": core.utc_now()}, match
    return ORIGINAL_WAIT_COMMENT(pattern, timeout_s=timeout_s, after_epoch=after_epoch)


def main() -> None:
    core.add_comment = add_comment_v4
    core.wait_comment = wait_comment_v4
    try:
        core.main()
    finally:
        server = STATE.get("server")
        if server is not None:
            try:
                server.shutdown()
            except Exception:
                pass
        process = STATE.get("cloudflared")
        if process is not None:
            try:
                process.terminate()
                process.wait(timeout=5)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass


if __name__ == "__main__":
    main()
