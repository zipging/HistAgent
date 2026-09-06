---
title: HistAgent API compatibility redirect
emoji: 🧬
colorFrom: green
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
license: mit
---

The active public API and all models run together in `wli14/HistAgent-agent`.
This retired address only returns HTTP 307 redirects for older cached website
clients, preserving request methods and bodies. It performs no inference,
upstream HTTP requests, credential handling, or usage-ledger writes.

Deploy this directory to `wli14/HistAgent-api` only after the unified service has
passed its deployment checks. The original gateway remains in source control
under `histagent-api` for rollback.
