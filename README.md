# Agentic AI Incident Response Kit 2026 — Commercial Delivery

## Quickstart

**0–2 min — state the external effect.** Who/what is failing, what valuable outcome stopped, and when?

**2–5 min — freeze blast radius.** Stop duplicate writers/retries/deploys, preserve one known-good read path, record release/provider/account/session identity.

**5–10 min — classify.** Provider/rate-limit; auth/identity; tool/API contract; browser/session; queue/deadlock; agent retry runaway; cost; memory/context; deploy/config; data/security; host saturation; unknown-but-bounded.

**10–15 min — one reversible experiment.** One owner, one changed variable, one success signal, one failure signal, one rollback, one time limit.

Exit emergency phase only when a valuable end-to-end outcome works again and an independent signal proves it.

## Triage matrix

| Signal | Mechanism | Fast discriminator | First safe action |
|---|---|---|---|
| 429 / too many requests | Provider throttling | prompting vs observation vs retry traffic | remove incidental provider requests; preserve valuable writer |
| 401/403 after prior success | Auth/identity | exact account/token/session identity | rebind one surface; avoid global rotation |
| Tool says success, no external effect | Tool contract | provider/customer readback | treat transport receipt as weak evidence |
| UI differs across captures | Browser/session drift | display/profile/URL/account provenance | freeze input; re-establish provenance |
| High load, CPU still idle | runnable backlog | runnable vs blocked vs IO wait | kill only attributable runaway work |
| Identical actions repeat | retry runaway | operation/target/idempotency identity | enforce retry budget |
| Queue grows, workers idle | deadlock/routing | oldest job + lock/owner | drain one valuable lane |
| Spend rises, outcomes flat | cost runaway | cost per valuable completion | remove low-value loops |
| Agent forgets resolved fact | context corruption | what was actually visible? | dense current handoff |
| Deploy green, business broken | semantic regression | previous release A/B | blue/green rollback |

## Incident prompts

**Incident commander:** Restore the valuable external outcome. Assign one owner per mechanism, prevent duplicate side effects, keep a rollback, and require decision-changing evidence.

**Investigator:** Give three plausible mechanisms. For each: support, falsifier, fastest safe discriminator, and test cost.

**Recovery owner:** Execute one reversible experiment. State changed variable, success/failure signals, rollback, and time limit before acting. Report the external effect afterward.

**Verifier:** Try to disprove recovery using an independent source. State only the narrow scope actually proven.

**Postmortem synthesizer:** Compress customer/economic effect, causal chain, detection lag, recovery that actually worked, misleading evidence, and one recurrence signal.

## Targeted runbooks

### Provider throttling
Measure valuable accepted operations/min separately from incidental provider requests/min. Preserve the highest-value writer, localize observation, collapse duplicate retries, and reintroduce lanes gradually. Alert on `provider_requests / externally_valuable_completions`, not raw request count alone.

### Authentication and identity drift
Record provider user/account ID, organization, credential source class, browser profile/session, and resource owner. Rebind one failing surface to one provider-native known-good identity and confirm with provider readback before retiring stale material.

### Tool/API contract failure
State the intended external effect independently of the tool. Capture operation/version and actual arguments. Verify provider state through a different read path. Patch the narrow adapter. A 200/queued/receipt state is not semantic success.

### Browser/session drift
Attach provenance: host/device -> display/session -> browser profile -> tab/URL -> account -> capture time. Freeze input, compare time-separated passive screenshots, and use a second representation when available. Never repair one session by mutating an unrelated protected session.

### Agent/retry runaway
Every side effect gets idempotency key, target identity, attempt number, next allowed time, success predicate, failure predicate, and retry budget. Retry only when expected value exceeds cost and failure is plausibly transient.

### Queue/deadlock/starvation
Capture oldest job age, active workers, per-worker job, queue by value, lock holders, external waits. Cancel stale duplicates, isolate long jobs from latency-sensitive work, bound occupancy, and prove one end-to-end job before reopening concurrency.

### Cost/budget runaway
Track fully-loaded cost per externally valuable completion: model/API, compute, payment/marketplace fees, tools, retries, and delay. Remove evidence-generation that doesn't change decisions. Preserve outcome-moving work.

### Memory/context corruption
Ask what the acting agent actually saw. Produce one dense current-state handoff, mark superseded claims, compress/delete redundant copies, and require the next action to demonstrably use the handoff.

### Deployment/configuration regression
Record source commit, built artifact, loaded runtime. Compare last known-good. Stand up green beside blue, route a small real workload, require external semantic success, then roll forward or discard green.

### Host/resource saturation
Read CPU utilization, load/runnable/blocked/IO wait, RAM available, swap pressure, disk IO/space, GPU/VRAM, and process-level RSS/CPU together. Shared-service cgroup limits can kill a whole generation despite host free memory. Kill only attributable runaway work.

### Prompt injection / unsafe tool influence
Separate retrieved data from instruction and preserve the current authority boundary. Minimize tool scope, redact secrets, verify the action against the original objective, and summarize/delete adversarial payloads unless they improve future detection.

### Data/privacy incident
Stop further disclosure, classify exposed data, bound recipients/time window, rotate only plausibly exposed credentials, preserve minimum evidence, and use the appropriate internal legal/security process. Never paste secrets into shared incident reports.

### Customer/economic outcome recovery
Do not close an incident because a process is green. Close when the valuable outcome is restored: purchase, publication, delivery, accepted job output, settlement, or other customer-visible result. Keep technical-health and economic/customer-health signals separate.

## Templates

### Incident intake
- Start time:
- Valuable outcome affected:
- Customer/economic impact:
- Severity:
- First known change:
- Known-good reference:
- Release/version:
- Provider/account/session identity:
- Evidence:
- Contradictions:
- Actions already taken:
- Current owner:
- Next experiment:
- Success signal:
- Rollback:

### Recovery experiment
- Hypothesis:
- Mechanism:
- Changed variable:
- Smallest useful experiment:
- Success signal:
- Failure signal:
- Independent verification:
- Time budget:
- Cost budget:
- Rollback:
- Owner:
- Result:
- Next decision:

### Postmortem
1. Executive summary
2. Customer/economic impact
3. Decision-relevant timeline
4. Causal chain: trigger -> mechanism -> propagation -> effect
5. Detection: useful vs misleading signals
6. Response: actions that changed outcomes vs consumed time
7. Exact recovery action and independent proof
8. Recurrence mechanism
9. Prevent / detect earlier / recover faster

## Observability schema

```json
{"required":["observed_at","valuable_outcome","state","evidence"],"state":["healthy","degraded","blocked","recovering","unknown_bounded"],"severity":["SEV0","SEV1","SEV2","SEV3"],"fields":["mechanism_class","identity","resource_metrics","economic_metrics","evidence","contradictions","next_experiment","rollback"]}
```

## Worked lessons

**Throttling amplified by observation.** Productive submissions are cut because of 429s, but completions fall further. Independent evidence shows observation/polling, not the valuable writer, creates most requests. Removing only provider-contacting observation restores output. Lesson: request source matters more than aggregate count.

**Transport success, business failure.** A publisher returns HTTP 200 and `submitted=true`, but provider readback shows a draft because a required field was ignored. Success is redefined as public listing + visible price + independent readback. Lesson: semantic success belongs at the external boundary.

The accompanying Python utilities are included as separate files in this delivery branch.