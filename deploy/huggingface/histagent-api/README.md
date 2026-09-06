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
enforces a conservative shared application GPU budget, and keeps the Hugging Face
access token out of browser code.

## Deployment checks

Set `WLI14_HF_TOKEN` to an owner access token with access to both private model
Spaces and write access to the private quota ledger. `HF_TOKEN` is a fallback;
an expiring browser OAuth credential should not be the primary service secret.
Never put either token in the website assets.

Keep `HISTAGENT_FORWARD_VISITOR_IDENTITY=1` (the default). The gateway forwards
Hugging Face's signed `X-IP-Token` to the two fixed model hosts and sends its
server token in both `Authorization` and `X-HF-Authorization`. The public browser
does not supply the service token. These headers are necessary authentication
and identity plumbing, but do not guarantee that HF's edge will accept traffic.
The September 6 incident reproduced HTTP 429 with forwarding both enabled and
disabled, including an identical credential that succeeded outside Spaces.

The inference and atlas GPU functions request 60 seconds. Longer requests can
be rejected by the ZeroGPU scheduler before model execution. These scheduling
limits are separate from the remaining daily allowance. The gateway uses the
full Gradio queue protocol because the simplified `/call` stream can discard
the actual error and report `null`. Explicit duration and quota rejections
refund the application reservation; interrupted or ambiguous jobs do not.
The deployed `spaces` SDK scales nominal duration by GPU type; on Blackwell the
factor is 1.5, so the old 180-second inference request became 270 seconds.

`GET /api/health` reports model-host reachability and the remaining shared budget.
It does not allocate a GPU or prove that a model job can finish. Release checks
must also exercise anonymous image inference, conversation and atlas retrieval.

Host-level HTTP 429 responses trigger a cooldown for that visitor identity,
respecting `Retry-After`
(five minutes when no duration is provided). They are distinct from GPU quota
exhaustion. GPU submissions are not automatically repeated after transport
failures, and accepted or uncertain jobs retain their budget reservation if the
response is lost. This prevents retries from undercounting GPU use. The ledger
remains a conservative application limit, not a replacement for HF billing
settings or account-level quotas.

Run `pytest test_app.py` before deployment. Tests use simulated HTTP responses
and do not access private models or consume GPU allowance.

After all three Spaces finish restarting, run the public integration check from
the repository root (requires `httpx` and `Pillow`):

```sh
python scripts/smoke_public_gateway.py --output /tmp/histagent-smoke-release
```

This uses the website's public example without a Hugging Face login and verifies
50 unique generated genes, spot chat and follow-up, five atlas results, atlas
chat, and a repeated cached retrieval. It consumes real GPU allowance. Run it
deliberately after deployment, not as a frequent health probe. Fresh GPU steps
must not be served from cache. The saved `summary.json` is marked successful
only after every check passes. Repeat after a restart to check the cold path;
one successful run does not establish overnight availability.
