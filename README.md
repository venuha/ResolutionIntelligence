# ResolutionIntelligence · Continuum Refund Resolution

A separate Week 3 application built from the Week 2 synthetic RAG corpus and deterministic reconciliation module. A LangGraph orchestrator coordinates six investigation specialists, then a deterministic receiving-bank agent matches approved refunds to original card purchases. Telecom and bank reviewers have separate responsibilities; customer updates reflect confirmed workflow stages.

**The original Week 2 RAG project is unchanged.** This project has its own source, environment, SQLite databases, configuration and interface.

## Run locally

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.lock
./run.sh
```

Open **http://127.0.0.1:8103**. Choose a fictional demo identity. No provider key or personal information is required for the rule-based demonstration. Bind to localhost and run a single worker.

The identity selector is intentionally a local demo mechanism, not production authentication. The backend enforces each selected identity's role and case assignments. This application is not designed for public hosting with the demo identity selector enabled.

## Try the complete journey

1. Select **Telecom supervisor** and open **Riley Quinn / CASE-1007**.
2. Inspect **Linked transactions**: the original $749 card purchase is associated with `ORD-24007`, `DEV-88007`, `PAY-0007`, and a fictional case-specific card token. Only a masked suffix is displayed.
3. Select **Timeout once · recover**, then **Investigate case**. Inspect the recorded retry and the proposed $749 refund.
4. Review the amount, evidence, procedure and customer wording; choose **Approve action**. The simulated processor returns a receipt and the bank receives an incoming-review task.
5. Switch to **Bank reviewer**. Review the original transaction, card, device, order, amount and remaining purchase balance. Approve the simulated credit.
6. Switch to **Riley Quinn · customer**. See the original purchase, posted refund, and separate submission/credit notifications.

Additional cases:

| Case | Scenario | Expected behavior |
|---|---|---|
| CASE-2001 / Morgan Lee | $149 approved return | Telecom approval, then automatic simulated bank match under the threshold |
| CASE-2002 / Jamie Patel | $500 approved, $200 already submitted and posted | Propose only the remaining $300; maintain both credit records |
| CASE-2003 / Drew Wilson | Statement and bank snapshot disagree | Propose an investigation; no new refund |
| CASE-2004 / Robin Shaw | $300 provisional credit, submitted refund without posted merchant credit | Bank trace; provisional credit never counts as a refund |
| CASE-1001 / Maya Chen | Carrier delivery without warehouse verification | Warehouse evidence task |
| CASE-1006 / Casey Park | Device mismatch | Warehouse reconciliation task |

The bank threshold is **$500 by default**, expressed as `BANK_REVIEW_THRESHOLD_CENTS=50000`. This is a fictional project rule, not actual bank policy. Amounts at or above the threshold require a bank reviewer. Mismatches and external sandbox references require review regardless of amount. A trace request cannot itself create a credit. Reviewers cannot override failed identity or amount checks to post a credit.

## Agentic and demo modes

**Rule-based demo** runs the complete durable workflow without LLM calls. It is labeled throughout the interface and must not be presented as evidence of live model quality.

**Model-driven agents** requires a privately configured OpenAI key. The orchestrator selects a permitted next specialist. Evidence and trace investigators can select up to three scoped follow-up tools each, then produce structured explanations. A separate model call reviews the complete draft against original sources. One repair round is permitted. Unavailable or unsupported model output blocks execution and is withheld from user-facing responses.

The fixed graph enforces mandatory checks and human approvals even when model routing differs. Arithmetic, allowed action selection, role permissions, threshold checks and writes are deterministic. The bank agent is deterministic in both modes.

## Optional provider setup

Run `.venv/bin/python scripts/configure.py` in your own terminal. Secret input is hidden and saved only to this project's ignored, permission-600 `.env`. Restart the server after configuration. Do not paste keys into chat, screenshots, GitHub, or a submission document.

| Provider | Information needed | What the adapter does |
|---|---|---|
| OpenAI | API key; optional model name | Structured orchestration, tool selection, explanations and independent review |
| Keenable | API key | Alternative to Tavily for official-provider guidance search; no customer identifiers sent |
| Tavily | API key | Generic official-provider guidance search; no customer identifiers in search queries |
| Stripe | **Test** secret key | Case-linked test charge reads, approved partial refunds, explicit status refresh |
| Plaid | Sandbox client ID, secret and access token | Bank reviewer's read-only transaction candidates; no automatic matching to Stripe |
| Twilio | Account SID, token, sender and designated test recipient | Separate explicit test-SMS button when `ENABLE_SMS=true`; may incur provider charges |

To use Keenable, run `.venv/bin/python scripts/configure.py keenable` and enter your key at the hidden prompt. This also selects `SEARCH_PROVIDER=keenable`. Restart the app, then check **Connections**. Configuring Tavily similarly selects Tavily. `SEARCH_PROVIDER=auto` prefers a configured Keenable key, then Tavily; `local` disables web requests.

The policy specialist uses Keenable's documented [Search API](https://docs.keenable.ai/api-reference/search) with two generic searches per investigation: one scoped to `docs.stripe.com` and one to `plaid.com`. Keyed searches use your provider allowance/credits. Returned HTTPS domains are checked again locally; excerpts remain public guidance and never establish a case event, authorize a refund, or replace the internal procedure. Search failure retains the local procedure with an audit note; it does not silently switch to another paid provider. The Evidence tab shows the provider and retrieved excerpts. Keenable supplies web research; the model, bank-data and payment integrations retain their separate roles.

The customer portal works without SMS. SMS API acceptance is not treated as delivery. This version has no automatic SMS status callbacks and no scheduled provider polling.

To create and link a fictional Stripe charge after configuring a test key:

```bash
.venv/bin/python scripts/link_stripe.py CASE-2001 --create-test-charge
```

This command explicitly creates a sandbox payment using Stripe's `tok_visa`; no real card is used. Alternatively supply an existing test charge ID whose metadata contains the case ID and whose amount/currency match the case. Use a case without previous action history. In the app select **Stripe sandbox** before investigating.

**Stripe success does not prove bank posting.** Stripe and Plaid sandboxes are separate. The application explicitly translates a successful test refund into a *simulated* bank task for review; it does not claim that Plaid or a real issuer received it. Original card correlations in this demo are fictional. Real issuer or telecom integration requires authorized institution-specific access. No ServiceNow or real bank write integration is implemented.

If a provider submission times out after a possible write, its result is marked uncertain and new investigation runs are blocked. Reconcile the external outcome before retrying. The **Refresh Stripe status** button retrieves a known refund or searches the linked charge for a matching action ID after an uncertain submission. If no receipt is found, the outcome stays uncertain and no new refund is issued. This lookup is manual; background polling is not implemented.

## Persistence and approval semantics

- `work/checkpoints.sqlite`: durable LangGraph checkpoints and approval interrupts.
- `work/operations.sqlite`: cases' run summaries, approval audit state, idempotent actions, bank tasks, bank ledger, customer messages and hashed local demo sessions.
- Approval includes the exact proposal hash and evidence/financial snapshot. Changes invalidate execution.
- Repeated approval requests or action replay cannot duplicate a recorded refund or bank credit.
- The demo uses one application process and a serialized execution lock. SQLite transactions protect bank posting. Multi-worker coordination is future work.
- Source corpus records remain immutable in normal operation. A bank-posted event supplements them and does not rewrite the Week 2 snapshot.

## Week 2 feedback

The recorded low-faithfulness answers sometimes removed unsupported claims while repeating the same amount in a suggested action or unknown. This version checks the whole model output, validates source scope, uses deterministic action and notification templates, and independently tests contradictory and partial-payment cases.

Calculation correctness, explanation faithfulness, action justification and workflow completion are reported separately. No new model-faithfulness score is claimed without actual provider evaluation. See `docs/week2-feedback-review.md`, `docs/evaluation.json` and `docs/demo-script.md`.

## Validate

```bash
.venv/bin/pytest -q
.venv/bin/python scripts/evaluate.py
```

Tests exercise role/case boundaries, a full telecom-to-bank-to-customer journey, restart/resume, temporary and permanent tool errors, duplicate approvals, stale evidence, conflicting financial records, provisional credits, scoped model-selected tools, external sandbox review, and provider key restrictions. Provider adapters are tested with controlled mocks; they are not live-connection verification.

## Project layout

| Path | Purpose |
|---|---|
| `resolution/workflow.py` | Orchestrator, specialists, tool loop, approval interrupt, execution and notification |
| `resolution/agents.py` | Structured model contracts and specialist prompts |
| `resolution/bank.py` | Receiving-bank matching, threshold routing, human review and posting |
| `resolution/integrations.py` | Keenable, Tavily, Stripe, Plaid and Twilio adapters |
| `resolution/legacy_reconciliation.py` | Copied Week 2 deterministic calculation module |
| `resolution/evidence.py` | Case-filtered BM25 retrieval and versioned fictional procedure |
| `resolution/main.py` | Local API, sessions, role checks and serving |
| `resolution/static/` | Telecom, bank, customer, connection and agent-team screens |
| `data/` | Copied synthetic sources, fresh cases and historical Week 2 evaluation |

The previous dense/Pinecone retrieval, PDF uploads and production account system are not ported into this first version. Retrieval here is case-filtered BM25 over the synthetic source export. This is an explicit implementation boundary, not a claim of retaining all Week 2 capabilities.

Keenable's search adapter was verified against its live API on October 25, 2026 (UTC), returning four official-provider sources. The key remains private and is not included in this repository. This verifies web retrieval only; model quality, Stripe and Plaid sandbox connections still require separate verification.

Remaining submission work: enable and evaluate model mode with your own key, finish sandbox transaction linking, record the live demo (five minutes or less), and prepare the Google Doc. No cloud deployment, submission, or external customer notification has been performed.
