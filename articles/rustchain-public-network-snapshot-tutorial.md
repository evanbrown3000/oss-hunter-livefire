# Build a RustChain Network Snapshot in Python — Health, Miners, and Proof-of-Antiquity Signals

RustChain is unusual among blockchain projects because the public network exposes enough operational data to inspect the relationship between node health and its Proof-of-Antiquity hardware model without installing a full node first. That makes it a useful target for a small observability exercise: can we fetch live node status, enumerate the currently returned miners, summarize their hardware families, and inspect the antiquity multipliers with nothing except Python's standard library?

This tutorial builds exactly that. The result is a small script you can run from a laptop, CI job, monitoring host, or agent workflow. It does **not** mine, move RTC, create a wallet, or require credentials. It is deliberately read-only.

RustChain source: <https://github.com/Scottcjn/Rustchain>

Public site: <https://rustchain.org>

Runnable example in this repository: [`examples/rustchain_network_snapshot.py`](../examples/rustchain_network_snapshot.py)

## Why observe both health and miners?

A common monitoring mistake is to treat one green endpoint as proof that the useful system is healthy. A process can be up while its database is read-only; an API can answer while its chain tip is stale; a miner list can be non-empty while the underlying node is unhealthy.

RustChain's public `/health` response exposes several distinct signals. At the time this article was prepared, it returned fields including:

- `ok`
- `db_rw`
- `tip_age_slots`
- `uptime_s`
- `version`
- `backup_age_hours`

The public `/api/miners` response separately returned a miner collection plus pagination metadata. Each visible miner record included fields such as `device_arch`, `device_family`, `hardware_type`, `antiquity_multiplier`, `first_attest`, and `last_attest`.

Those are different questions. `/health` says something about the serving node. `/api/miners` gives a public view of enrolled/returned mining participants and their reported classification. Keeping them separate makes the snapshot more useful.

## The smallest useful client

Python already ships with `urllib.request` and `json`, so we do not need `requests` or another dependency:

```python
import json
import urllib.request

BASE = "https://rustchain.org"


def get_json(path):
    request = urllib.request.Request(
        BASE + path,
        headers={"User-Agent": "rustchain-network-snapshot/1.0"},
    )
    with urllib.request.urlopen(request, timeout=12) as response:
        if response.status != 200:
            raise RuntimeError(f"{path} returned HTTP {response.status}")
        return json.load(response)


health = get_json("/health")
miners_payload = get_json("/api/miners")
```

That already gives us two independently useful observations. The full example adds explicit error handling so a timeout, invalid JSON response, or HTTP failure exits non-zero instead of being mistaken for an empty network.

That distinction is worth preserving in automation. If an API lookup fails, `0 miners` is not a truthful substitute for `unknown miners`.

## Summarize hardware without flattening the interesting part

The miner API exposes both a broad `device_family` and a more descriptive `hardware_type`. We can use `collections.Counter` for a quick family distribution:

```python
import collections

miners = miners_payload.get("miners") or []

families = collections.Counter(
    str(miner.get("device_family") or "unknown")
    for miner in miners
)

for family, count in families.most_common():
    print(f"{family}: {count}")
```

When I checked the live endpoint while preparing this tutorial, the returned set included Apple Silicon, x86, ARM, PowerPC, and Windows-classified entries. One visible PowerPC G5 record carried an antiquity multiplier of `2.0`; several modern x86 records were at `0.8`; Apple Silicon examples were visible at `1.2`; and one ARM/unknown record was much lower.

The important thing is not to over-interpret that snapshot. A multiplier is a value returned by this public API. By itself it does not prove a machine's profitability, authenticity, age, ownership, or future reward. Those are separate claims that require separate evidence.

## Inspect the multiplier range

A compact summary can still help you see whether the visible set is homogeneous or varied:

```python
multipliers = [
    float(miner["antiquity_multiplier"])
    for miner in miners
    if miner.get("antiquity_multiplier") is not None
]

if multipliers:
    print("min", min(multipliers))
    print("max", max(multipliers))
    print("mean", sum(multipliers) / len(multipliers))
```

This is useful for observation, not for declaring a winner. The full script also sorts the returned records and prints the miner with the highest **publicly returned multiplier**, carefully labeling it that way.

## Keep pagination visible

The miner response includes pagination metadata, so the script prints both the number of rows actually returned and the provider's `total` / `total_enrolled` values when present:

```python
pagination = miners_payload.get("pagination") or {}

print(f"miners_returned={len(miners)}")
print(f"miners_total={pagination.get('total', len(miners))}")
print(f"miners_total_enrolled={pagination.get('total_enrolled', 'unknown')}")
```

This avoids another common observability failure: silently treating one API page as the whole population.

During my live check, the endpoint returned 12 miner rows while the response metadata reported `total: 12` and `total_enrolled: 14`. That is a better fact to preserve than simply saying "RustChain has 12 miners," because the API itself distinguishes the returned total from total enrolled.

## Run it

Clone this repository or copy the script, then run:

```bash
python3 examples/rustchain_network_snapshot.py
```

A successful run prints a compact record shaped like:

```text
RustChain public network snapshot
node_ok=True
version=2.2.1-rip200
db_rw=True
tip_age_slots=0
uptime_s=...
miners_returned=...
miners_total=...
miners_total_enrolled=...
device_families=...
antiquity_multiplier_min=...
antiquity_multiplier_max=...
antiquity_multiplier_mean=...
highest_public_multiplier=...
```

The exact values will change as the live network changes. That is the point: this is a snapshot tool, not a hard-coded status page.

## Turn it into a useful monitor

If you want to build on this, preserve three design rules.

First, **fail loudly on observation failure**. A timeout should not become zero miners, zero rewards, or a fake green status.

Second, **preserve source time and pagination**. If you store snapshots, include your observation timestamp and the provider's count metadata so a later reader can tell the difference between "the network had no miners" and "our collector saw no miners."

Third, **separate metrics from conclusions**. A high antiquity multiplier is a returned protocol signal; whether a machine is genuinely vintage, economically attractive, reliable, or environmentally preferable is a different analytical question. Good observability leaves enough structure for that later reasoning rather than collapsing everything into one score.

A practical next step is to append each snapshot as JSON Lines and compare changes over time: node version changes, miner arrivals/departures, family mix, and multiplier distribution. Another is to alert only on transitions that matter—for example `ok` changing from true to false, `db_rw` becoming false, tip age growing, or the miner endpoint becoming unreadable.

That gives you a small but real operational loop around the public RustChain network without running a miner or taking custody of funds. More importantly, it demonstrates a general rule that applies well beyond RustChain: **observe the valuable state directly, keep unknown distinct from zero, and do not let a green process substitute for the outcome you actually care about.**
