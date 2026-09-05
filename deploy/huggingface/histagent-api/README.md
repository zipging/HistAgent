---
title: HistAgent API
emoji: 🧬
colorFrom: green
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
license: mit
---

# HistAgent API

Server-side gateway for the public HistAgent workbench and Atlas Explorer.

The gateway authenticates fixed calls to the released HistAgent ZeroGPU Spaces,
enforces a conservative account-level GPU budget, and keeps the Hugging Face
access token out of browser code.

## Deployment checks

Set `WLI14_HF_TOKEN` to an owner access token with access to both private model
Spaces and write access to the private quota ledger. `HF_TOKEN` is a fallback;
an expiring browser OAuth credential should not be the primary service secret.
Never put either token in the website assets.

`GET /api/health` reports model-host reachability and the remaining shared budget.
It does not allocate a GPU or prove that a model job can finish. Release checks
must also exercise anonymous image inference, conversation and atlas retrieval.

Host-level HTTP 429 responses trigger a shared cooldown respecting `Retry-After`
(five minutes when no duration is provided). They are distinct from GPU quota
exhaustion. GPU submissions are not automatically repeated after transport
failures, and accepted or uncertain jobs retain their budget reservation if the
response is lost. This prevents retries from undercounting GPU use. The ledger
remains a conservative application limit, not a replacement for HF billing
settings or account-level quotas.

Run `pytest test_app.py` before deployment. Tests use simulated HTTP responses
and do not access private models or consume GPU allowance.
