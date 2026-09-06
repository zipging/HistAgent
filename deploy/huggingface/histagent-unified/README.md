---
title: HistAgent
emoji: 🧬
colorFrom: green
colorTo: blue
sdk: gradio
sdk_version: 5.44.1
python_version: "3.10"
app_file: app.py
pinned: false
license: other
models:
  - wli13/HistAgent
  - prov-gigapath/prov-gigapath
  - Qwen/Qwen3-8B
  - wli14/HistAgent-Qwen3-8B-LoRA
  - Qwen/Qwen3-Embedding-8B
datasets:
  - wli14/HistAgent-atlas-images
---

# HistAgent unified service

This Gradio ZeroGPU Space serves the public HistAgent API and executes visual
gene ranking, Atlas retrieval, and evidence-grounded chat in the same Space.
It removes the requests between separate inference and gateway Spaces. Model
functions use one shared GPU dispatcher and queue, with one GPU task at a time.
The application cache, request limits, and conservative usage ledger remain
separate from Hugging Face's own scheduling and quota checks.

## Build from the repository

The deployment directory is generated from the canonical model implementations;
do not maintain a second copy of their scientific logic here. From the repository
root, run:

```sh
python scripts/build_unified_space.py --output /tmp/histagent-unified-bundle
```

The builder requires a new or empty output directory. Use `--force` only when the
chosen output directory is disposable. It copies the gateway, unified runtime,
and complete HistAgent Python package; extracts the model functions without their
old UIs; removes unused reference-spot data loading; and leaves GPU allocation to
the shared dispatcher. Reasoning models use explicit CUDA placement. Their
precision, model identifiers, prompts, image preprocessing, and retrieval logic
are preserved. `bundle-manifest.json` records SHA-256 hashes of the source files
and generated files so a deployment can be checked against its source.

Upload the generated directory to a Gradio Space configured for ZeroGPU. This
bundle uses the Gradio SDK, not a Docker SDK image. Keep the existing service
available until anonymous requests to the merged deployment have passed the
public smoke test for generation, retrieval, follow-up chat, and caching.

## Credentials and quota

Configure persistent secrets in the Space settings, never in the website:

- `WLI14_HF_TOKEN`: the primary long-term service token, with write access to the
  private usage ledger. `HF_TOKEN` is the fallback when this secret is absent.
- `HF_TOKEN`: the existing model-download credential, including gated GigaPath
  access. When absent, model downloads fall back to the service token.
- `HISTAGENT_MODEL_TOKEN`: optional separate model-access credential. When set,
  model downloads use it while ledger operations retain the service token.
- `HISTAGENT_VISION_TOKEN`: optional separate token for the HistAgent checkpoint
  and gated GigaPath base; defaults to `HF_TOKEN`. Startup downloads the GigaPath
  base explicitly with this token and sets `HISTAGENT_BASE_CHECKPOINT` to its
  local path before loading HistAgent. The canonical model loader then uses the
  local checkpoint with strict weight validation, avoiding a second download
  through timm's global token. `HISTAGENT_BASE_CHECKPOINT` can also identify an
  already downloaded base checkpoint. Configure tokens at startup; do not switch
  process environment tokens around concurrent requests.

The existing gateway's `HISTAGENT_QUOTA_REPO`, `HISTAGENT_GPU_QUOTA_SECONDS`, quota
window, rate-limit, and response-cache settings remain applicable. The health
response's remaining seconds describe the application ledger, not a measured
Hugging Face allowance. Anonymous visitors remain subject to Hugging Face's
ZeroGPU quota and availability. An owner's PRO subscription must not be assumed
to provide that allowance to anonymous requests.

## Capacity and validation

On September 6, 2026, all three models loaded successfully and ZeroGPU packed
36.4 GB of tensors. Real generation, chat, follow-up, retrieval, and Atlas chat
completed using the public RCC example. This is a working example, not a
measurement of worst-case memory use. Preserve the shared concurrency limit of one. Do not change
model precision or switch models to fit memory without validating the resulting
scientific behavior.

The dispatcher requests 30 seconds (45 scheduler seconds on Blackwell), with
models already loaded before admission. Measured model execution on the public
example was approximately 0.8 seconds for generation, 1.5 seconds for retrieval,
and 3–4 seconds for chat; HTTP and scheduling overhead are additional.

Dependency versions are pinned to one environment. In particular PEFT 0.20.0
replaces the incompatible version ranges in the separate services. Dependency
resolution does not prove checkpoint compatibility: perform real visual-model
loading and compare the public example's ranked output before switching traffic.

Use `scripts/smoke_public_gateway.py --gateway <space-url> --output <new-directory>`
from the source repository for uncached public requests. This consumes actual GPU
allowance. Check memory and repeat after a service restart; a passing health probe
alone does not establish inference availability.
