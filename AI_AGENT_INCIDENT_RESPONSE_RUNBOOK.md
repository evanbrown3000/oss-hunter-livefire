# AI Agent Incident Response Runbook

A practical incident-response sequence for production systems that use LLM agents, browser automation, tool calls, queues, external APIs, credentials, and multi-agent workflows.

**Need the complete production toolkit? [Agentic AI Incident Response Kit 2026 — $109 →](https://buy.stripe.com/dRmeVd5uh0CP09NaaugQE01?utm_source=github&utm_medium=runbook&utm_campaign=agentic_ir_kit_20260819&utm_content=runbook_top&client_reference_id=github_ir_runbook_top)**

## When to use this runbook

Use it when the failure is larger than a bad model answer. Examples:

- an AI agent changed the wrong external resource;
- a browser agent reports success but the account/provider state did not change;
- an agent repeatedly retries a failing payment, publication, deployment, or message;
- the model is working from stale context or the wrong session/account;
- several agents or workers share the same tool, credential, queue, browser, or deployment surface;
- a provider accepted a request but later reset, rejected, or silently discarded the result;
- the repository is correct but the running system is stale;
- the system looks healthy while the customer/business outcome is still broken.

## 1. State the external failure

Write one sentence that names the valuable outcome that stopped working.

Bad:

> The agent is broken.

Better:

> The publication worker returns success, but the provider still shows no buyer-visible listing at the intended price.

This keeps the investigation anchored to the external result rather than to whatever internal signal happens to be easiest to inspect.

## 2. Contain only the harmful action path

Stop or isolate the narrow mechanism that can create additional harm:

- the specific writer;
- retry loop;
- credential;
- browser session;
- queue consumer;
- deployment route;
- provider mutation endpoint.

Preserve independent read-only evidence where possible. Shutting down every surface at once can destroy the evidence needed to distinguish a decision failure from an execution, custody, provider, or deployment failure.

## 3. Establish one identity for every external surface

For each relevant action, record:

- provider/account identity;
- browser profile/session/display if applicable;
- API credential identity without exposing the credential itself;
- repository + commit/release identity;
- worker/process identity;
- target resource identity;
- idempotency or operation identity.

A surprising fraction of “agent reasoning” incidents are actually identity mismatches: the right action executed against the wrong account, browser, deployment, branch, or stale runtime.

## 4. Separate the evidence planes

Do not collapse these into one definition of success:

1. **Decision evidence** — what the agent believed and chose.
2. **Transport evidence** — whether a tool/API/browser request was accepted.
3. **Execution evidence** — whether the intended code/action ran.
4. **Provider evidence** — what the external provider currently records.
5. **Outcome evidence** — whether the valuable customer/business result became true.

HTTP 200 proves only what that endpoint defines. A “submitted” response does not prove publication, payout, delivery, indexing, settlement, or customer access.

## 5. Classify the dominant mechanism

Use the narrowest mechanism that changes the next action:

| Mechanism | Typical signal | Fast discriminator |
|---|---|---|
| Provider throttling | 429 / intermittent denial | productive writes vs incidental polling traffic |
| Authentication drift | 401/403 after prior success | exact token/account/session identity |
| Tool/API contract mismatch | accepted request, missing effect | provider-native readback |
| Browser/session drift | UI differs across sessions | profile/display/account provenance |
| Retry runaway | repeated identical effects | operation + target + idempotency identity |
| Queue/routing failure | healthy workers, growing backlog | oldest job + current owner/lock |
| Memory/context failure | agent ignores resolved fact | what text/state was actually visible this turn |
| Deployment skew | source fixed, production wrong | running release vs repository commit |
| Cost runaway | spend rises, outcomes flat | cost per externally valuable completion |
| Verification failure | submission accepted then reset | provider postcondition after verifier runs |

## 6. Run one reversible experiment

A good incident experiment has:

- one changed variable;
- one owner;
- one success signal;
- one failure signal;
- one rollback;
- one time limit.

Examples:

- disable provider-contacting observation while preserving the writer;
- use one fresh browser session while preserving the old session read-only;
- deploy a blue/green candidate without replacing production;
- submit one idempotent provider mutation and read the provider state once afterward;
- replace a stale runtime with the exact known-good release while preserving logs.

Do not change five mechanisms and then infer causality from improvement.

## 7. Verify at the level that failed

If the failure was:

- **publication** → verify public listing, price, and buyer addressability;
- **payment** → verify provider-held accessible funds;
- **browser custody** → verify the exact browser/session/account;
- **deployment** → verify the running release, not Git history;
- **queue execution** → verify the valuable job crossed the queue and produced its downstream effect;
- **memory** → verify a later agent actually received and used the corrected information.

Recovery is not “process green.” Recovery is the valuable end-to-end outcome working again with an independent signal.

## 8. Capture the causal chain

A useful post-incident summary is compact but causal:

> trigger → incorrect assumption or failed mechanism → external effect → detection → bounded repair → verified recovery → recurrence prevention

Keep concrete identifiers needed for future diagnosis, but remove giant raw logs and duplicate screenshots once their unique information has been summarized somewhere durable.

## Minimal first-15-minute checklist

- [ ] State the external outcome failure.
- [ ] Stop only the harmful writer/retry path.
- [ ] Preserve independent provider/runtime/session evidence.
- [ ] Record exact account/session/release identities.
- [ ] Prevent blind duplicate side effects.
- [ ] Classify the dominant mechanism.
- [ ] Run one reversible experiment.
- [ ] Verify the external outcome independently.
- [ ] Record the causal chain and recurrence mechanism.

## Full production kit

The **Agentic AI Incident Response Kit 2026** adds the complete playbook, production triage matrix, observability JSON schema, incident intake and postmortem templates, reusable incident-commander/investigator/recovery/verifier prompts, Python failure classifier, cost-of-delay calculator, retry-budget calculator, SLO burn-rate calculator, evidence digest and log-redaction utilities, worked incident examples, and a one-organization commercial internal-use license.

**[Buy the full kit with Stripe — $109 →](https://buy.stripe.com/dRmeVd5uh0CP09NaaugQE01?utm_source=github&utm_medium=runbook&utm_campaign=agentic_ir_kit_20260819&utm_content=runbook_bottom&client_reference_id=github_ir_runbook_bottom)**

Instant digital delivery after successful payment. No account setup or sales call is required.
