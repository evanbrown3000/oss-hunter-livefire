#!/usr/bin/env python3
from __future__ import annotations

import json
from typing import Any

import chatgpt_exact_selfprop_cycle_v4 as ingress

core = ingress.core
ORIGINAL_DECRYPT_BUNDLE = core.decrypt_bundle
ORIGINAL_SHA = core.sha
V5_STATE: dict[str, Any] = {
    "secret1": "",
    "bypass_pending": False,
    "deferred_hash": "",
}


def decrypt_bundle_v5(private_key, ciphertext: str):
    data = ORIGINAL_DECRYPT_BUNDLE(private_key, ciphertext)
    secret1 = str(data.get("secret1") or "")
    if len(secret1) < 24:
        raise RuntimeError("SECRET1_TOO_SHORT_V5")
    trigger = json.loads(core.TRIGGER_PATH.read_text(encoding="utf-8"))
    deferred_hash = str(trigger.get("secret1_sha256") or "")
    if deferred_hash != "0" * 64:
        raise RuntimeError("V5_TRIGGER_MUST_USE_DEFERRED_COMMITMENT")
    V5_STATE.update(
        {
            "secret1": secret1,
            "bypass_pending": True,
            "deferred_hash": deferred_hash,
        }
    )
    ingress.ORIGINAL_ADD_COMMENT(
        "CYCLE_SECRET1_COMMITTED_V5 "
        f"proof_id={trigger.get('proof_id')} run_id={core.RUN_ID} "
        f"secret1_sha256={ORIGINAL_SHA(secret1)} plaintext_exposed=false "
        "commitment_stage=after_decrypt_before_child_prompt"
    )
    return data


def sha_v5(value: object) -> str:
    if V5_STATE["bypass_pending"] and str(value) == V5_STATE["secret1"]:
        V5_STATE["bypass_pending"] = False
        return str(V5_STATE["deferred_hash"])
    return ORIGINAL_SHA(value)


def main() -> None:
    core.decrypt_bundle = decrypt_bundle_v5
    core.sha = sha_v5
    ingress.main()


if __name__ == "__main__":
    main()
