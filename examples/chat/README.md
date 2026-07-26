# HistAgent Agent Module

This example runs the same spot selection and molecular-profile chat interface
used by the HistAgent project website. Its service also powers natural-language
atlas search and H&amp;E tissue analysis in the website Agent Module. The public
data bundle contains 9,150 spatial transcriptomics spots and three RCC tissue
images for the slice view.

## Install

From the HistAgent repository root:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python -m pip install -r examples/chat/requirements.txt
```

Copy the configuration template and set an OpenAI-compatible language model
endpoint:

```bash
cp examples/chat/.env.example examples/chat/.env
```

`HISTAGENT_API_BASE_URL`, `HISTAGENT_MODEL` and `HISTAGENT_API_KEY` configure
the language model used for Spot Chat and natural-language atlas queries.

## Run

```bash
python examples/chat/run_chat.py
```

The first run downloads and verifies the Agent Module data from
[`wli13/HistAgent-data`](https://huggingface.co/datasets/wli13/HistAgent-data).
Open `http://127.0.0.1:7860` for Spot Chat after the server starts. The same
origin provides the API used by Atlas Search and Tissue Analysis.

To connect a deployed project page, set its service origin:

```html
<meta name="histagent-chat-origin" content="https://your-agent-service.example">
```

## Local Qwen endpoint

An external OpenAI-compatible endpoint is not required. To serve Qwen3-8B
locally:

```bash
python -m pip install -r examples/chat/requirements-llm.txt
HISTAGENT_QWEN_PATH=Qwen/Qwen3-8B \
python examples/chat/llm_server.py
```

The default Chat configuration connects to this server at
`http://127.0.0.1:8001/v1`.

## Image analysis

H&E image analysis loads the released HistAgent checkpoint from
`wli13/HistAgent`. Prov-GigaPath is gated, so request access to the base model
and set `HF_TOKEN` in `examples/chat/.env` before using image analysis.

Set `HISTAGENT_BASE_CHECKPOINT` when the GigaPath checkpoint is already
available locally.

## Data configuration

`run_chat.py` writes `examples/chat/data.env` after downloading the bundle:

```text
HISTAGENT_INPUT_JSONL=/absolute/path/to/chat/spots.jsonl
HISTAGENT_ATLAS_SQLITE=
HISTAGENT_SLICE_IMAGE_DIRS=/absolute/path/to/chat/slice_images
```

The public Agent Module bundle runs without the optional SQLite atlas. To use a larger
atlas, set `HISTAGENT_ATLAS_SQLITE` to a compatible database containing the
`spots` and `slices` tables.
