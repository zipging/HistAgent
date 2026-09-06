"""Exercise the public gateway with the website's public RCC example.

Anonymous by default; --hf-auth explicitly uses the local Hub credential for a
separate account-quota check. This consumes real inference allowance; run
deliberately after deployments, not as a frequent health poll.
Requires httpx and Pillow.
"""
from __future__ import annotations

import argparse
import io
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx
from PIL import Image


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway", default="https://wli14-histagent-agent.hf.space")
    parser.add_argument("--site", default="https://histagent.bio")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--hf-auth", action="store_true", help="Use the locally configured HF account for model API calls only")
    args = parser.parse_args()
    api_headers = {}
    if args.hf_auth:
        if urlparse(args.gateway).scheme != "https" or urlparse(args.gateway).hostname not in {
            "wli14-histagent-agent.hf.space", "wli14-histagent-api.hf.space"
        }:
            parser.error("Authenticated checks require a configured HistAgent HF endpoint")
        from huggingface_hub import get_token
        token = get_token()
        if not token:
            parser.error("No local Hugging Face credential is available")
        api_headers["Authorization"] = "Bearer " + token
    args.output.mkdir(parents=True, exist_ok=True)
    summary = {"started_at": datetime.now(timezone.utc).isoformat(), "authentication": "local_hf_account" if args.hf_auth else "anonymous", "passed": False, "steps": []}
    headers = {"X-HistAgent-Session": "smoke-" + str(uuid.uuid4()), "Origin": args.site}

    def save_summary() -> None:
        (args.output / "summary.json").write_text(json.dumps(summary, indent=2))

    save_summary()
    with httpx.Client(timeout=300, headers=headers, follow_redirects=False) as client:
        def request(name, method, path, **kwargs):
            start = time.monotonic()
            # Credentials are confined to the chosen HF API, never website assets.
            response = client.request(method, args.gateway + path, headers=api_headers, **kwargs)
            try:
                payload = response.json()
            except ValueError:
                payload = {"error": "Non-JSON gateway response", "status": response.status_code}
            step = {"name": name, "http_status": response.status_code, "seconds": round(time.monotonic() - start, 2)}
            if name != "health":
                step["cached"] = payload.get("cached") is True
            summary["steps"].append(step)
            (args.output / (name + ".json")).write_text(json.dumps(payload, indent=2))
            save_summary()
            print(json.dumps(step), flush=True)
            response.raise_for_status()
            if name not in {"health", "atlas_cached"}:
                assert not step["cached"], "Cached result cannot establish fresh GPU availability; use a fresh deployment or wait for cache expiry"
            return payload, step

        health, _ = request("health", "GET", "/api/health")
        assert health["status"] == "available", health
        assert health["remaining_gpu_seconds"] >= 480, "Insufficient application budget for this smoke test"

        response = client.get(args.site + "/assets/gsm5924036-spots.json")
        response.raise_for_status()
        manifest = response.json()
        spot = next(s for s in manifest["spots"] if s["barcode"] == manifest["default_barcode"])
        response = client.get(args.site + manifest["image_url"])
        response.raise_for_status()
        tissue = Image.open(io.BytesIO(response.content)).convert("RGB")
        files = {}
        for kind, diameter, pixels in (("local", 55, 224), ("context", 220, 256)):
            half = diameter / manifest["mpp"] / 2
            box = (spot["x"] - half, spot["y"] - half, spot["x"] + half, spot["y"] + half)
            crop = tissue.resize((pixels, pixels), Image.Resampling.BICUBIC, box=box)
            buffer = io.BytesIO()
            crop.save(buffer, format="PNG")
            files[kind + "_image"] = (kind + ".png", buffer.getvalue(), "image/png")
        generated, step = request("generate", "POST", "/api/generate", files=files,
                                  data={"species": manifest["species"], "organ": manifest["organ"], "top_k": "50"})
        outputs = generated["data"]
        rows = outputs[0].get("data", []) if isinstance(outputs[0], dict) else outputs[0]
        genes = [row[1] for row in rows]
        assert len(genes) == len(set(genes)) == 50, "Expected 50 unique ranked genes"
        step["unique_ranked_genes"] = len(genes)
        evidence = {"spot": {"barcode": spot["barcode"], "species": "human", "organ": "kidney"},
                    "ranked_genes": genes, "spatial_context": {"available": False},
                    "provenance": {"source": "HistAgent ranked molecular readout", "image_name": manifest["title"]}}

        def call(name, api_name, data):
            payload, step = request(name, "POST", "/api/call", json={"service": "reasoning", "api_name": api_name, "data": data})
            assert isinstance(payload.get("data"), list), payload
            return payload["data"], step

        chat, step = call("spot_chat", "answer_atlas_question", ["Name three top-ranked genes in this evidence card and explain the limitations of interpreting this ranking.", [], evidence])
        history = chat[1]
        assert history[-1]["role"] == "assistant" and history[-1]["content"].strip()
        step["answer_characters"] = len(history[-1]["content"])
        followup, step = call("spot_followup", "answer_atlas_question", ["Does this ranking alone establish absolute gene expression?", history, evidence])
        assert len(followup[1]) > len(history) and followup[1][-1]["content"].strip()
        step["history_messages"] = len(followup[1])

        query = ["Find tumor-adjacent immune-stromal interface regions.", "human", "Any", "__ready__", 5]
        atlas, step = call("atlas_search", "retrieve_atlas", query)
        atlas_rows = atlas[0].get("data", []) if isinstance(atlas[0], dict) else atlas[0]
        assert len(atlas_rows) == 5 and atlas[1], "Expected five retrieved spots and their evidence"
        step["retrieved_spots"] = len(atlas_rows)
        atlas_chat, step = call("atlas_chat", "answer_atlas_question", ["What tissue context is supported by the retrieved evidence?", [], atlas[1]])
        assert atlas_chat[1][-1]["role"] == "assistant" and atlas_chat[1][-1]["content"].strip()
        step["answer_characters"] = len(atlas_chat[1][-1]["content"])
        cached, step = request("atlas_cached", "POST", "/api/call", json={"service": "reasoning", "api_name": "retrieve_atlas", "data": query})
        assert cached.get("cached") is True and cached["data"] == atlas
        step["cached"] = True
        summary["completed_at"] = datetime.now(timezone.utc).isoformat()
        summary["passed"] = True
        save_summary()


if __name__ == "__main__":
    main()
