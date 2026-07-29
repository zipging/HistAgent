#!/usr/bin/env python3
"""Export a balanced public subset of the HistAgent measured-ST evidence bank."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

import numpy as np


EMBEDDING_DIM = 4096


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--atlas-sqlite", type=Path, required=True)
    parser.add_argument("--embedding-manifest", type=Path, required=True)
    parser.add_argument("--embedding-shard-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--n-spots", type=int, default=100_000)
    parser.add_argument("--batch-size", type=int, default=500)
    return parser.parse_args()


def connect_readonly(path: Path) -> sqlite3.Connection:
    uri = f"file:{quote(str(path.expanduser()))}?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def parse_json(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    text = str(value).strip()
    if not text:
        return default
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return default


def parse_gene_list(value: Any) -> list[str]:
    parsed = parse_json(value, None)
    if isinstance(parsed, list):
        return [str(item).strip() for item in parsed if str(item).strip()]
    return [item for item in str(value or "").replace(",", " ").split() if item]


def allocate_balanced_targets(
    counts: dict[tuple[str, str], int],
    total: int,
) -> dict[tuple[str, str], int]:
    if total <= 0:
        raise ValueError("--n-spots must be positive")
    if total > sum(counts.values()):
        raise ValueError("--n-spots exceeds the number of atlas spots")

    targets = {group: 0 for group in counts}
    remaining = total
    while remaining:
        active = [group for group, count in counts.items() if targets[group] < count]
        if not active:
            break
        share = max(1, remaining // len(active))
        changed = 0
        for group in active:
            increment = min(share, counts[group] - targets[group], remaining)
            targets[group] += increment
            remaining -= increment
            changed += increment
            if remaining == 0:
                break
        if changed == 0:
            raise RuntimeError("Could not allocate the requested public subset")
    return targets


def evenly_spaced_indices(count: int, target: int) -> set[int]:
    if target >= count:
        return set(range(count))
    values = np.linspace(0, count - 1, num=target, dtype=np.int64)
    selected = {int(value) for value in values}
    if len(selected) != target:
        raise RuntimeError(f"Systematic sample produced {len(selected)} rows, expected {target}")
    return selected


def select_spots(
    connection: sqlite3.Connection,
    target_total: int,
) -> tuple[list[dict[str, Any]], dict[tuple[str, str], int], dict[tuple[str, str], int]]:
    count_rows = connection.execute(
        """
        SELECT species, organ, COUNT(*) AS n
        FROM spots
        GROUP BY species, organ
        ORDER BY species, organ
        """
    ).fetchall()
    group_counts = {
        (str(row["species"]), str(row["organ"])): int(row["n"])
        for row in count_rows
    }
    targets = allocate_balanced_targets(group_counts, target_total)
    selected_local = {
        group: evenly_spaced_indices(group_counts[group], targets[group])
        for group in group_counts
    }

    group_offsets: dict[tuple[str, str], int] = defaultdict(int)
    selected: list[dict[str, Any]] = []
    rows = connection.execute(
        """
        SELECT
          spot_key, slice_id, barcode, species, organ, top_genes,
          x, y, array_row, array_col, dominant_cell_type,
          dominant_cell_prop, low_evidence, pathway_sparse,
          organ_mismatch_risk
        FROM spots
        ORDER BY species, organ, slice_id, barcode
        """
    )
    for global_index, row in enumerate(rows):
        group = (str(row["species"]), str(row["organ"]))
        local_index = group_offsets[group]
        group_offsets[group] += 1
        if local_index not in selected_local[group]:
            continue
        record = dict(row)
        record["global_index"] = global_index
        selected.append(record)

    if len(selected) != target_total:
        raise RuntimeError(f"Selected {len(selected)} spots, expected {target_total}")
    return selected, group_counts, targets


def load_embedding_manifest(path: Path, shard_dir: Path) -> list[dict[str, Any]]:
    rows = list(iter_jsonl(path))
    rows.sort(key=lambda row: int(row["shard_index"]))
    offset = 0
    for row in rows:
        count = int(row["count"])
        embedding_path = shard_dir / Path(str(row["embedding_file"])).name
        if not embedding_path.exists():
            raise FileNotFoundError(embedding_path)
        row["resolved_embedding_file"] = str(embedding_path)
        row["start"] = offset
        row["end"] = offset + count
        offset += count
    return rows


def export_embeddings(
    selected: list[dict[str, Any]],
    manifest_rows: list[dict[str, Any]],
    output_path: Path,
) -> None:
    output = np.lib.format.open_memmap(
        output_path,
        mode="w+",
        dtype=np.float16,
        shape=(len(selected), EMBEDDING_DIM),
    )
    selected_by_shard: dict[int, list[tuple[int, int]]] = defaultdict(list)
    shard_cursor = 0
    for output_index, record in enumerate(selected):
        global_index = int(record["global_index"])
        while not (
            int(manifest_rows[shard_cursor]["start"])
            <= global_index
            < int(manifest_rows[shard_cursor]["end"])
        ):
            shard_cursor += 1
            if shard_cursor >= len(manifest_rows):
                raise RuntimeError(f"Embedding row {global_index} is outside the manifest")
        local_index = global_index - int(manifest_rows[shard_cursor]["start"])
        selected_by_shard[shard_cursor].append((output_index, local_index))

    for shard_index, output_rows in selected_by_shard.items():
        shard = np.load(
            manifest_rows[shard_index]["resolved_embedding_file"],
            mmap_mode="r",
        )
        if shard.ndim != 2 or shard.shape[1] != EMBEDDING_DIM:
            raise RuntimeError(f"Unexpected embedding shape: {shard.shape}")
        destination = np.asarray([row[0] for row in output_rows], dtype=np.int64)
        source = np.asarray([row[1] for row in output_rows], dtype=np.int64)
        output[destination] = np.asarray(shard[source], dtype=np.float16)
    output.flush()


def compact_cell_types(payload: dict[str, Any]) -> list[dict[str, Any]]:
    priors = payload.get("priors") if isinstance(payload.get("priors"), dict) else {}
    decon = priors.get("decon_cellmarker") if isinstance(priors.get("decon_cellmarker"), dict) else {}
    rows = decon.get("celltype_topk") if isinstance(decon.get("celltype_topk"), list) else []
    compact: list[dict[str, Any]] = []
    for row in rows[:5]:
        if not isinstance(row, dict):
            continue
        compact.append(
            {
                "cell_type": row.get("cell_type"),
                "proportion": row.get("proportion"),
                "score": row.get("score"),
                "evidence_genes": list(row.get("evidence_genes") or [])[:10],
            }
        )
    return compact


def compact_pathways(value: Any) -> list[dict[str, Any]]:
    rows = parse_json(value, [])
    compact: list[dict[str, Any]] = []
    for row in rows[:5] if isinstance(rows, list) else []:
        if isinstance(row, dict):
            compact.append(
                {
                    "pathway": row.get("pathway") or row.get("name") or row.get("term"),
                    "score": row.get("score"),
                    "hit_count": row.get("hit_count"),
                }
            )
        elif str(row).strip():
            compact.append({"pathway": str(row).strip()})
    return compact


def batched(items: list[str], size: int) -> Iterable[list[str]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def load_evidence(
    connection: sqlite3.Connection,
    spot_ids: list[str],
    batch_size: int,
) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for batch in batched(spot_ids, batch_size):
        placeholders = ",".join("?" for _ in batch)
        rows = connection.execute(
            f"""
            SELECT
              e.spot_key, e.payload, e.evidence_level,
              r.reactome_top_json, r.gobp_top_json
            FROM evidence e
            LEFT JOIN pathway_rgobp r USING (spot_key)
            WHERE e.spot_key IN ({placeholders})
            """,
            batch,
        ).fetchall()
        for row in rows:
            payload = parse_json(row["payload"], {})
            output[str(row["spot_key"])] = {
                "cell_type_composition": compact_cell_types(payload),
                "reactome_pathways": compact_pathways(row["reactome_top_json"]),
                "gobp_pathways": compact_pathways(row["gobp_top_json"]),
                "quality_flags": dict(payload.get("quality_flags") or {}),
                "evidence_level": row["evidence_level"],
            }
    return output


def export_metadata(
    selected: list[dict[str, Any]],
    evidence: dict[str, dict[str, Any]],
    output_path: Path,
) -> None:
    with output_path.open("w", encoding="utf-8") as handle:
        for record in selected:
            spot_id = str(record["spot_key"])
            row = {
                "spot_id": spot_id,
                "slice_id": record["slice_id"],
                "barcode": record["barcode"],
                "species": record["species"],
                "organ": record["organ"],
                "x": record["x"],
                "y": record["y"],
                "array_row": record["array_row"],
                "array_col": record["array_col"],
                "top_genes": parse_gene_list(record["top_genes"])[:50],
                "dominant_cell_type": record["dominant_cell_type"],
                "dominant_cell_proportion": record["dominant_cell_prop"],
                "quality_flags": {
                    "low_evidence": bool(record["low_evidence"]),
                    "pathway_sparse": bool(record["pathway_sparse"]),
                    "organ_mismatch_risk": bool(record["organ_mismatch_risk"]),
                },
                **evidence.get(spot_id, {}),
            }
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    connection = connect_readonly(args.atlas_sqlite)

    selected, group_counts, targets = select_spots(connection, args.n_spots)
    manifest_rows = load_embedding_manifest(
        args.embedding_manifest,
        args.embedding_shard_dir,
    )

    embedding_path = args.output_dir / "evidence_bank_embeddings_fp16.npy"
    metadata_path = args.output_dir / "evidence_bank_metadata.jsonl"
    export_embeddings(selected, manifest_rows, embedding_path)
    evidence = load_evidence(
        connection,
        [str(record["spot_key"]) for record in selected],
        args.batch_size,
    )
    export_metadata(selected, evidence, metadata_path)

    manifest = {
        "schema_version": "histagent_public_evidence_bank_v1",
        "n_spots": len(selected),
        "embedding_dim": EMBEDDING_DIM,
        "embedding_dtype": "float16",
        "embedding_model": "Qwen/Qwen3-Embedding-8B",
        "similarity": "cosine",
        "sampling": "deterministic balanced systematic sample by species and organ",
        "source_atlas_spots": sum(group_counts.values()),
        "source_group_counts": {
            f"{species}::{organ}": count
            for (species, organ), count in group_counts.items()
        },
        "sample_group_counts": {
            f"{species}::{organ}": count
            for (species, organ), count in targets.items()
        },
        "files": {
            embedding_path.name: {
                "bytes": embedding_path.stat().st_size,
                "sha256": sha256(embedding_path),
            },
            metadata_path.name: {
                "bytes": metadata_path.stat().st_size,
                "sha256": sha256(metadata_path),
            },
        },
    }
    manifest_path = args.output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
