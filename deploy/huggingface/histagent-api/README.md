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

