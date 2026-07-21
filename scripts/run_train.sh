#!/usr/bin/env bash
set -euo pipefail

: "${DATA_ROOT:?Set DATA_ROOT to the directory containing the HDF5 patch files.}"
: "${TRAIN_PARQUET:?Set TRAIN_PARQUET to the training metadata parquet.}"
: "${VAL_PARQUET:?Set VAL_PARQUET to the validation metadata parquet.}"

NUM_PROCESSES=${NUM_PROCESSES:-1}
MIXED_PRECISION=${MIXED_PRECISION:-bf16}

ARGS=(
  scripts/train.py
  --data-root "$DATA_ROOT"
  --train-parquet "$TRAIN_PARQUET"
  --val-parquet "$VAL_PARQUET"
  --output-dir "${OUTPUT_DIR:-checkpoints}"
  --epochs "${EPOCHS:-30}"
  --batch-size "${BATCH_SIZE:-24}"
  --num-workers "${NUM_WORKERS:-8}"
  --mixed-precision "$MIXED_PRECISION"
)
if [[ -n "${BASE_CHECKPOINT:-}" ]]; then
  ARGS+=(--base-checkpoint "$BASE_CHECKPOINT")
fi

accelerate launch \
  --num_processes "$NUM_PROCESSES" \
  --mixed_precision "$MIXED_PRECISION" \
  "${ARGS[@]}"
