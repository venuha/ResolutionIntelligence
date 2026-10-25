# Render Free + Supabase

Deploy `main` of `venuha/ResolutionIntelligence` as one Docker web service on **Free**, in Virginia. Use the root Dockerfile, `/health` as the health-check path and one instance. `scripts/serve.py` binds `0.0.0.0:$PORT` with one worker. No paid disk is needed.

## Private configuration

Set these in the new Render service's private environment settings. Never commit their values:

- `REFUND_HOSTED=true`
- `FORWARDED_ALLOW_IPS=*` (Render terminates HTTPS at its proxy)
- `DATABASE_URL`: the existing Supabase transaction-pooler URI, port 6543.
- `CHECKPOINT_DATABASE_URL`: the same project's **session-pooler** URI, port 5432. LangGraph uses pipelines and a persistent search path, which require session mode. Both database connections require TLS and disable prepared statements.
- `REFUND_ACCESS_PASSWORD`: a unique, privately generated password of at least 16 characters. Keep it private; its holder can explore all fictional perspectives.
- `SEARCH_PROVIDER=keenable` and `KEENABLE_API_KEY`: the private search key.
- `ENABLE_SMS=false` and `BANK_REVIEW_THRESHOLD_CENTS=50000`.

Render supplies `RENDER_EXTERNAL_HOSTNAME`. For a different host/custom domain, set `REFUND_PUBLIC_URL` to the exact HTTPS app origin. The server validates hosts and write origins, sets Secure/HttpOnly/Strict cookies and limits incorrect password attempts. Provider secrets never enter browser code or graph state. `/health` exposes only status, storage type and hosted mode.

The original project's Supabase URL can be reused. This application creates only `resolution_private` and its own tables, revokes anonymous/authenticated schema access and keeps the Week 2 `cci_private` data separate. App credentials, not browser-side Supabase keys, authorize each demonstration perspective. Corpus files are synthetic and shipped with the image. There are no uploaded user documents in this version, so a Supabase object bucket is not required.

## Behavior and limits

The workspace password protects the portfolio demonstration. It is not separate authentication for telecom staff, bank staff and customers: the signed-in operator can switch perspectives. Do not use this deployment for actual customer data or money. The receiving bank remains explicitly simulated. Model, Stripe and Plaid verification are separate from deployment.

Runs and checkpoints survive restarts. A background investigation interrupted by a service restart can be resumed from the case page; no independent scheduler is running. Bank posting uses a transaction advisory lock and existing balance validation to prevent duplicate credits. Keep one service instance and one worker; distributed orchestration is outside this demo's scope.

Render Free can sleep after inactivity and has shared workspace usage limits. This service shares those limits with the user's other Free apps. Supabase Free can pause inactive projects. API search/model charges are separate from free compute. No paid upgrade or payment details are configured.

## Verification

Run `.venv/bin/pytest -q`. To test the real database privately, put configuration in ignored `.env.render`, then run `.venv/bin/python scripts/verify_postgres.py`. It creates a unique disposable schema, writes a pending approval in one process, resumes in a second process, verifies the simulated bank credit and customer messages, confirms anonymous schema access is denied, and removes only its own test schema. It does not call external model/search/payment providers.

After deployment verify `/health`, anonymous case denial, password sign-in, selected-perspective permissions, Keenable guidance, a complete telecom/bank/customer example and persistence after a Render restart. Record the actual URL and results only after verification.

## Deployment status

Deployment prepared; live Render verification pending.

References: [Render Free](https://render.com/docs/free), [Render Docker](https://render.com/docs/docker), [Supabase connection modes](https://supabase.com/docs/guides/database/connecting-to-postgres).
