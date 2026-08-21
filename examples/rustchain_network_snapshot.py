#!/usr/bin/env python3
"""Read RustChain's public health/miner APIs and print a compact network snapshot.

No third-party packages are required.
"""

from __future__ import annotations

import collections
import json
import sys
import urllib.error
import urllib.request

BASE = "https://rustchain.org"
TIMEOUT_SECONDS = 12


def get_json(path: str) -> dict:
    request = urllib.request.Request(
        BASE + path,
        headers={"User-Agent": "rustchain-network-snapshot/1.0"},
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        if response.status != 200:
            raise RuntimeError(f"{path} returned HTTP {response.status}")
        return json.load(response)


def main() -> int:
    try:
        health = get_json("/health")
        miners_payload = get_json("/api/miners")
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, RuntimeError) as exc:
        print(f"snapshot failed: {exc}", file=sys.stderr)
        return 1

    miners = miners_payload.get("miners") or []
    pagination = miners_payload.get("pagination") or {}

    families = collections.Counter(
        str(miner.get("device_family") or "unknown") for miner in miners
    )
    multipliers = [
        float(miner["antiquity_multiplier"])
        for miner in miners
        if miner.get("antiquity_multiplier") is not None
    ]

    print("RustChain public network snapshot")
    print(f"node_ok={health.get('ok')}")
    print(f"version={health.get('version', 'unknown')}")
    print(f"db_rw={health.get('db_rw')}")
    print(f"tip_age_slots={health.get('tip_age_slots')}")
    print(f"uptime_s={health.get('uptime_s')}")
    print(f"miners_returned={len(miners)}")
    print(f"miners_total={pagination.get('total', len(miners))}")
    print(f"miners_total_enrolled={pagination.get('total_enrolled', 'unknown')}")
    print("device_families=" + ", ".join(f"{name}:{count}" for name, count in families.most_common()))

    if multipliers:
        print(f"antiquity_multiplier_min={min(multipliers):g}")
        print(f"antiquity_multiplier_max={max(multipliers):g}")
        print(f"antiquity_multiplier_mean={sum(multipliers) / len(multipliers):.4f}")

    # Show the highest-weight public miner without assuming that weight implies
    # age, trustworthiness, profitability, or any other fact not in this API.
    ranked = sorted(
        miners,
        key=lambda miner: float(miner.get("antiquity_multiplier") or 0),
        reverse=True,
    )
    if ranked:
        leader = ranked[0]
        print(
            "highest_public_multiplier="
            f"{leader.get('miner', 'unknown')}|"
            f"{leader.get('hardware_type', 'unknown')}|"
            f"{leader.get('antiquity_multiplier', 'unknown')}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
