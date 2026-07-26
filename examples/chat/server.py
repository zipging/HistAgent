#!/usr/bin/env python3
"""Backend for HistAgent Spot Chat, Atlas Search and Tissue Analysis."""

from __future__ import annotations

import json
import hashlib
import base64
import io
import os
import re
import sqlite3
import sys
import threading
import uuid
import math
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple
from urllib.parse import quote

import requests
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

try:
    from PIL import Image, ImageOps

    Image.MAX_IMAGE_PIXELS = None
    PIL_AVAILABLE = True
except Exception:
    Image = None
    ImageOps = None
    PIL_AVAILABLE = False


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
THUMB_CACHE_DIR = BASE_DIR / "cache" / "slice_thumbs"

DEFAULT_INPUT_JSONL = str(BASE_DIR / "data" / "chat" / "spots.jsonl")
DEFAULT_ATLAS_SQLITE = ""


def _slice_image_dirs() -> List[Path]:
    configured = os.getenv("HISTAGENT_SLICE_IMAGE_DIRS", "").strip()
    if configured:
        return [
            Path(value).expanduser()
            for value in configured.split(os.pathsep)
            if value.strip()
        ]
    return [BASE_DIR / "data" / "chat" / "slice_images"]


SLICE_IMAGE_DIRS = _slice_image_dirs()
SLICE_IMAGE_EXTS = [".png", ".jpg", ".jpeg", ".tif", ".tiff"]
MAX_DIRECT_IMAGE_BYTES = int(os.getenv("HISTAGENT_MAX_DIRECT_IMAGE_BYTES", str(120 * 1024 * 1024)))
MAX_THUMB_SOURCE_BYTES = int(os.getenv("HISTAGENT_MAX_THUMB_SOURCE_BYTES", str(512 * 1024 * 1024)))
SLICE_THUMB_MAX_DIM = int(os.getenv("HISTAGENT_SLICE_THUMB_MAX_DIM", "2200"))


def _spot_key(slice_id: str, barcode: str) -> str:
    sid = str(slice_id or "").strip()
    bc = str(barcode or "").strip()
    if sid and bc:
        return f"{sid}\t{bc}"
    return ""


def _uniq_keep_order(items: List[str], topk: int) -> List[str]:
    out: List[str] = []
    seen = set()
    for item in items:
        s = str(item).strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
        if len(out) >= topk:
            break
    return out


def _parse_gene_list(val: Any, topk: int = 50) -> List[str]:
    if isinstance(val, list):
        return _uniq_keep_order([str(x).strip() for x in val], topk)
    s = str(val or "").strip()
    if not s:
        return []
    if s.startswith("["):
        try:
            obj = json.loads(s)
            if isinstance(obj, list):
                return _uniq_keep_order([str(x).strip() for x in obj], topk)
        except Exception:
            pass
    return _uniq_keep_order(s.replace(",", " ").split(), topk)


def _truthy_sql_value(val: Any) -> bool:
    if isinstance(val, bool):
        return val
    if val is None:
        return False
    s = str(val).strip().lower()
    return s in {"1", "true", "yes", "y", "on"}


def _has_cjk(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", str(text or "")))


def _split_pathways(merged_topk: List[Dict[str, Any]], topk_each: int = 8) -> Tuple[List[str], List[str]]:
    scored: List[Tuple[float, str, str]] = []
    for row in merged_topk or []:
        if not isinstance(row, dict):
            continue
        db = str(row.get("db", "")).strip().lower()
        pw = str(row.get("pathway", "")).strip()
        if not pw:
            continue
        score = row.get("merged_score", row.get("score", 0.0))
        try:
            score_f = float(score)
        except Exception:
            score_f = 0.0
        scored.append((score_f, db, pw))

    scored.sort(key=lambda x: x[0], reverse=True)
    reactome: List[str] = []
    gobp: List[str] = []
    for _, db, pw in scored:
        if db == "reactome":
            reactome.append(pw)
        elif db in {"gobp", "go_bp", "go"}:
            gobp.append(pw)
    return _uniq_keep_order(reactome, topk_each), _uniq_keep_order(gobp, topk_each)


def _sanitize_decon(decon_topk: List[Dict[str, Any]], topk: int = 6) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for row in (decon_topk or [])[:topk]:
        if not isinstance(row, dict):
            continue
        cell_type = str(row.get("cell_type", "")).strip()
        if not cell_type:
            continue
        try:
            proportion = float(row.get("proportion", 0.0))
        except Exception:
            proportion = 0.0
        egs = _uniq_keep_order([str(x).strip() for x in (row.get("evidence_genes") or [])], 10)
        out.append(
            {
                "cell_type": cell_type,
                "proportion": round(proportion, 4),
                "evidence_genes": egs,
            }
        )
    return out


def _sanitize_spatial(sp: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(sp, dict):
        return {"available": False}
    out: Dict[str, Any] = {
        "available": bool(sp.get("available", False)),
        "x": sp.get("x"),
        "y": sp.get("y"),
        "array_row": sp.get("array_row"),
        "array_col": sp.get("array_col"),
        "n_neighbors": int(sp.get("n_neighbors", 0) or 0),
        "neighborhood_consensus": sp.get("neighborhood_consensus", {}),
        "boundary_label_discordance": sp.get("boundary_label_discordance"),
        "boundary_entropy": sp.get("boundary_entropy"),
        "local_dominance": sp.get("local_dominance"),
    }
    neighbors = sp.get("neighbors", [])
    compact_neighbors: List[Dict[str, Any]] = []
    if isinstance(neighbors, list):
        for row in neighbors[:6]:
            if not isinstance(row, dict):
                continue
            compact_neighbors.append(
                {
                    "cell_type": str(row.get("cell_type", "")).strip(),
                    "proportion": row.get("proportion"),
                    "distance": row.get("distance"),
                }
            )
    out["neighbors"] = compact_neighbors
    return out


def _parse_record(obj: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Parse either freeqa input-pack row or freeqa output row."""
    if not isinstance(obj, dict):
        return None

    # prior_pack_v1 style: spot evidence without dialogue wrapper.
    if str(obj.get("schema_version", "")).startswith("prior_pack_v1") and isinstance(obj.get("spot"), dict):
        spot = obj.get("spot", {}) or {}
        sid = str(spot.get("slice_id", "")).strip()
        bc = str(spot.get("barcode", "")).strip()
        sk = str(spot.get("spot_key", "")).strip() or _spot_key(sid, bc)
        if not sk:
            return None

        pri = obj.get("priors", {}) or {}
        decon = ((pri.get("decon_cellmarker", {}) or {}).get("celltype_topk", []) or [])
        pathways: Dict[str, List[str]] = {"reactome_top": [], "gobp_top": []}
        merged_topk = ((pri.get("pathway_multidb", {}) or {}).get("merged_topk", []) or [])
        reactome_top, gobp_top = _split_pathways(merged_topk, topk_each=8)
        pathways["reactome_top"] = reactome_top
        pathways["gobp_top"] = gobp_top
        compact_top = [
            str(x.get("pathway", "")).strip()
            for x in (((pri.get("pathway_compact", {}) or {}).get("topk", []) or [])[:8])
            if isinstance(x, dict) and str(x.get("pathway", "")).strip()
        ]
        if compact_top:
            pathways["compact_top"] = _uniq_keep_order(compact_top, 8)
        return {
            "spot_key": sk,
            "slice_id": sid,
            "barcode": bc,
            "species": str(spot.get("species", "")).strip(),
            "organ": str(spot.get("organ", "")).strip(),
            "top_genes": _parse_gene_list((obj.get("inputs", {}) or {}).get("top_genes", []), 50),
            "decon_topk": _sanitize_decon(decon, 6),
            "pathways": pathways,
            "quality_flags": obj.get("quality_flags", {}) or {},
            "spatial_context": {"available": False},
        }

    # freeqa_v2 output style
    if isinstance(obj.get("state_json"), dict) and isinstance(obj.get("spot"), dict):
        state = obj.get("state_json", {})
        spot = state.get("spot", obj.get("spot", {})) or {}
        sid = str(spot.get("slice_id", "")).strip()
        bc = str(spot.get("barcode", "")).strip()
        sk = str(spot.get("spot_key", "")).strip() or _spot_key(sid, bc)
        if not sk:
            return None
        return {
            "spot_key": sk,
            "slice_id": sid,
            "barcode": bc,
            "species": str(spot.get("species", "")).strip(),
            "organ": str(spot.get("organ", "")).strip(),
            "top_genes": _uniq_keep_order(state.get("top_genes", []), 50),
            "decon_topk": _sanitize_decon(state.get("decon_topk", []), 6),
            "pathways": {
                "reactome_top": _uniq_keep_order((state.get("pathways", {}) or {}).get("reactome_top", []), 8),
                "gobp_top": _uniq_keep_order((state.get("pathways", {}) or {}).get("gobp_top", []), 8),
            },
            "quality_flags": state.get("quality_flags", {}) or {},
            "spatial_context": _sanitize_spatial(state.get("spatial_context", {}) or {}),
        }

    # freeqa input-pack style
    sid = str(obj.get("slice_id", "")).strip()
    bc = str(obj.get("barcode", "")).strip()
    sk = str(obj.get("spot_key", "")).strip() or _spot_key(sid, bc)
    if not sk:
        return None

    ev = obj.get("input_evidence", {}) or {}
    inputs = ev.get("inputs", {}) or {}
    pri = ev.get("priors", {}) or {}
    spot_ev = ev.get("spot", {}) or {}
    qf = ev.get("quality_flags", {}) or {}

    decon = ((pri.get("decon_cellmarker", {}) or {}).get("celltype_topk", []) or [])
    merged_topk = ((pri.get("pathway_multidb", {}) or {}).get("merged_topk", []) or [])
    reactome_top, gobp_top = _split_pathways(merged_topk, topk_each=8)

    return {
        "spot_key": sk,
        "slice_id": sid,
        "barcode": bc,
        "species": str(obj.get("species", "")).strip(),
        "organ": str(obj.get("organ", "")).strip(),
        "top_genes": _uniq_keep_order(inputs.get("top_genes", []), 50),
        "decon_topk": _sanitize_decon(decon, 6),
        "pathways": {"reactome_top": reactome_top, "gobp_top": gobp_top},
        "quality_flags": qf,
        "spatial_context": _sanitize_spatial(spot_ev.get("spatial_context", {}) or {}),
    }


def _coerce_float(val: Any) -> Optional[float]:
    try:
        if val is None:
            return None
        return float(val)
    except Exception:
        return None


def _dominant_cell_type(rec: Dict[str, Any]) -> str:
    decon = rec.get("decon_topk", []) or []
    if not decon or not isinstance(decon[0], dict):
        return ""
    return str(decon[0].get("cell_type", "")).strip()


def _coerce_text_content(val: Any) -> str:
    if isinstance(val, str):
        return val
    if isinstance(val, list):
        parts: List[str] = []
        for item in val:
            if isinstance(item, str):
                parts.append(item)
                continue
            if isinstance(item, dict):
                text = item.get("text", item.get("content", ""))
                if text:
                    parts.append(str(text))
        return "".join(parts)
    return "" if val is None else str(val)


def _ndjson_line(obj: Dict[str, Any]) -> bytes:
    return (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")


def _load_spot_db(path: str) -> Tuple[Dict[str, Dict[str, Any]], List[Dict[str, str]]]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Input JSONL not found: {path}")

    db: Dict[str, Dict[str, Any]] = {}
    listing: List[Dict[str, str]] = []
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            rec = _parse_record(obj)
            if not rec:
                continue
            key = rec["spot_key"]
            db[key] = rec
            spatial = rec.get("spatial_context", {}) or {}
            listing.append(
                {
                    "spot_key": key,
                    "slice_id": rec["slice_id"],
                    "barcode": rec["barcode"],
                    "species": rec["species"],
                    "organ": rec["organ"],
                    "x": _coerce_float(spatial.get("x")),
                    "y": _coerce_float(spatial.get("y")),
                    "dominant_cell_type": _dominant_cell_type(rec),
                }
            )

    listing.sort(key=lambda x: (x["species"], x["organ"], x["slice_id"], x["barcode"]))
    return db, listing


class AtlasSpotStore:
    """Optional SQLite-backed index for a larger HistAgent spot atlas."""

    def __init__(self, path: str) -> None:
        raw_path = str(path or "").strip()
        self.path = Path(raw_path).expanduser() if raw_path else None
        self.enabled = bool(
            self.path
            and self.path.is_file()
            and self.path.stat().st_size > 0
        )
        self.conn: Optional[sqlite3.Connection] = None
        self.lock = threading.Lock()
        if self.enabled:
            try:
                self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
                self.conn.row_factory = sqlite3.Row
                self.conn.execute("PRAGMA query_only = ON")
                self.conn.execute("SELECT 1 FROM spots LIMIT 1").fetchone()
                self.has_evidence_table = self._table_exists("evidence")
                self.has_rgobp_table = self._table_exists("pathway_rgobp")
            except Exception:
                self.enabled = False
                if self.conn is not None:
                    self.conn.close()
                self.conn = None
                self.has_evidence_table = False
                self.has_rgobp_table = False
        else:
            self.has_evidence_table = False
            self.has_rgobp_table = False

    def _table_exists(self, name: str) -> bool:
        if self.conn is None:
            return False
        row = self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?", (name,)).fetchone()
        return row is not None

    def _fetchall(self, sql: str, params: Tuple[Any, ...] = ()) -> List[sqlite3.Row]:
        if not self.enabled or self.conn is None:
            return []
        with self.lock:
            return list(self.conn.execute(sql, params).fetchall())

    def _fetchone(self, sql: str, params: Tuple[Any, ...] = ()) -> Optional[sqlite3.Row]:
        if not self.enabled or self.conn is None:
            return None
        with self.lock:
            return self.conn.execute(sql, params).fetchone()

    def count(self) -> int:
        row = self._fetchone("SELECT COUNT(*) AS n FROM spots")
        return int(row["n"]) if row else 0

    def prior_count(self) -> int:
        row = self._fetchone("SELECT COUNT(*) AS n FROM spots WHERE has_prior = 1")
        return int(row["n"]) if row else 0

    def evidence_count(self) -> int:
        if not self.has_evidence_table:
            return 0
        row = self._fetchone("SELECT COUNT(*) AS n FROM evidence")
        return int(row["n"]) if row else 0

    def complete_evidence_count(self) -> int:
        if not self.has_evidence_table:
            return 0
        row = self._fetchone(
            "SELECT COUNT(*) AS n FROM evidence WHERE evidence_level = 'complete'"
        )
        return int(row["n"]) if row else 0

    def rgobp_count(self) -> int:
        if not self.has_rgobp_table:
            return 0
        row = self._fetchone("SELECT COUNT(*) AS n FROM pathway_rgobp")
        return int(row["n"]) if row else 0

    @staticmethod
    def _row_to_listing(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "spot_key": row["spot_key"],
            "slice_id": row["slice_id"],
            "barcode": row["barcode"],
            "species": row["species"] or "",
            "organ": row["organ"] or "",
            "x": _coerce_float(row["x"]),
            "y": _coerce_float(row["y"]),
            "dominant_cell_type": row["dominant_cell_type"] or "",
            "has_prior": bool(row["has_prior"]),
        }

    def list_species(self) -> List[str]:
        rows = self._fetchall("SELECT DISTINCT species FROM spots WHERE species != '' ORDER BY species")
        return [str(r["species"]) for r in rows]

    def list_organs(self, species: str) -> List[str]:
        sp = str(species or "").strip()
        if sp:
            rows = self._fetchall(
                "SELECT DISTINCT organ FROM spots WHERE species = ? AND organ != '' ORDER BY organ",
                (sp,),
            )
        else:
            rows = self._fetchall("SELECT DISTINCT organ FROM spots WHERE organ != '' ORDER BY organ")
        return [str(r["organ"]) for r in rows]

    def search_spots(self, query: str, limit: int) -> List[Dict[str, Any]]:
        q = str(query or "").strip().lower()
        lim = max(1, int(limit))
        cols = (
            "spot_key, slice_id, barcode, species, organ, x, y, dominant_cell_type, has_prior"
        )
        if not q:
            rows = self._fetchall(
                f"SELECT {cols} FROM spots ORDER BY species, organ, slice_id, barcode LIMIT ?",
                (lim,),
            )
        else:
            like = f"%{q}%"
            rows = self._fetchall(
                f"""
                SELECT {cols}
                FROM spots
                WHERE lower(spot_key) LIKE ?
                   OR lower(slice_id) LIKE ?
                   OR lower(barcode) LIKE ?
                   OR lower(species) LIKE ?
                   OR lower(organ) LIKE ?
                ORDER BY species, organ, slice_id, barcode
                LIMIT ?
                """,
                (like, like, like, like, like, lim),
            )
        return [self._row_to_listing(r) for r in rows]

    def list_spots_filtered(self, species: str, organ: str, query: str, limit: int) -> List[Dict[str, Any]]:
        sp = str(species or "").strip()
        og = str(organ or "").strip()
        q = str(query or "").strip().lower()
        lim = max(1, int(limit))
        wheres: List[str] = []
        params: List[Any] = []
        if sp:
            wheres.append("species = ?")
            params.append(sp)
        if og:
            wheres.append("organ = ?")
            params.append(og)
        if q:
            like = f"%{q}%"
            wheres.append("(lower(spot_key) LIKE ? OR lower(slice_id) LIKE ? OR lower(barcode) LIKE ?)")
            params.extend([like, like, like])
        where_sql = ("WHERE " + " AND ".join(wheres)) if wheres else ""
        params.append(lim)
        rows = self._fetchall(
            f"""
            SELECT spot_key, slice_id, barcode, species, organ, x, y, dominant_cell_type, has_prior
            FROM spots
            {where_sql}
            ORDER BY slice_id, barcode
            LIMIT ?
            """,
            tuple(params),
        )
        return [self._row_to_listing(r) for r in rows]

    def list_slices(self, species: str, organ: str) -> List[Dict[str, Any]]:
        sp = str(species or "").strip()
        og = str(organ or "").strip()
        wheres: List[str] = []
        params: List[Any] = []
        if sp:
            wheres.append("species = ?")
            params.append(sp)
        if og:
            wheres.append("organ = ?")
            params.append(og)
        where_sql = ("WHERE " + " AND ".join(wheres)) if wheres else ""
        rows = self._fetchall(
            f"""
            SELECT slice_id, species, organ, n_spots, n_with_coords, n_with_prior, sample_spot_key
            FROM slices
            {where_sql}
            ORDER BY n_spots DESC, slice_id
            """,
            tuple(params),
        )
        return [
            {
                "slice_id": r["slice_id"],
                "species": r["species"] or "",
                "organ": r["organ"] or "",
                "n_spots": int(r["n_spots"] or 0),
                "n_with_coords": int(r["n_with_coords"] or 0),
                "n_with_prior": int(r["n_with_prior"] or 0),
                "sample_spot_key": r["sample_spot_key"] or "",
            }
            for r in rows
        ]

    def get_slice_spots(self, species: str, organ: str, slice_id: str, limit: int) -> List[Dict[str, Any]]:
        sp = str(species or "").strip()
        og = str(organ or "").strip()
        sid = str(slice_id or "").strip()
        wheres: List[str] = []
        params: List[Any] = []
        if sid:
            wheres.append("slice_id = ?")
            params.append(sid)
        if sp:
            wheres.append("species = ?")
            params.append(sp)
        if og:
            wheres.append("organ = ?")
            params.append(og)
        where_sql = ("WHERE " + " AND ".join(wheres)) if wheres else ""
        params.append(max(1, int(limit)))
        rows = self._fetchall(
            f"""
            SELECT spot_key, slice_id, barcode, species, organ, x, y, dominant_cell_type, has_prior
            FROM spots
            {where_sql}
            ORDER BY (x IS NULL), y, x, barcode
            LIMIT ?
            """,
            tuple(params),
        )
        return [self._row_to_listing(r) for r in rows]

    def get_slice_total(self, species: str, organ: str, slice_id: str) -> int:
        sp = str(species or "").strip()
        og = str(organ or "").strip()
        sid = str(slice_id or "").strip()
        wheres: List[str] = ["slice_id = ?"]
        params: List[Any] = [sid]
        if sp:
            wheres.append("species = ?")
            params.append(sp)
        if og:
            wheres.append("organ = ?")
            params.append(og)
        row = self._fetchone(
            f"SELECT COUNT(*) AS n FROM spots WHERE {' AND '.join(wheres)}",
            tuple(params),
        )
        return int(row["n"]) if row else 0

    def get_row(self, spot_key: str) -> Optional[sqlite3.Row]:
        return self._fetchone("SELECT * FROM spots WHERE spot_key = ?", (str(spot_key or "").strip(),))

    def get_evidence_record(self, spot_key: str) -> Optional[Dict[str, Any]]:
        if not self.has_evidence_table:
            return None
        sk = str(spot_key or "").strip()
        row = self._fetchone("SELECT payload FROM evidence WHERE spot_key = ?", (str(spot_key or "").strip(),))
        if row is None:
            return None
        try:
            obj = json.loads(row["payload"])
        except Exception:
            return None
        rec = _parse_record(obj)
        if rec is not None:
            self._merge_rgobp_pathways(sk, rec)
        return rec

    def _merge_rgobp_pathways(self, spot_key: str, rec: Dict[str, Any]) -> None:
        if not self.has_rgobp_table:
            return
        row = self._fetchone(
            "SELECT reactome_top_json, gobp_top_json FROM pathway_rgobp WHERE spot_key = ?",
            (str(spot_key or "").strip(),),
        )
        if row is None:
            return
        try:
            reactome_rows = json.loads(row["reactome_top_json"] or "[]")
        except Exception:
            reactome_rows = []
        try:
            gobp_rows = json.loads(row["gobp_top_json"] or "[]")
        except Exception:
            gobp_rows = []
        pathways = rec.setdefault("pathways", {})
        reactome_top = [
            str(x.get("pathway", "")).strip()
            for x in (reactome_rows or [])[:8]
            if isinstance(x, dict) and str(x.get("pathway", "")).strip()
        ]
        gobp_top = [
            str(x.get("pathway", "")).strip()
            for x in (gobp_rows or [])[:8]
            if isinstance(x, dict) and str(x.get("pathway", "")).strip()
        ]
        if reactome_top:
            pathways["reactome_top"] = _uniq_keep_order(reactome_top, 8)
        if gobp_top:
            pathways["gobp_top"] = _uniq_keep_order(gobp_top, 8)

    def resolve_spot_key(self, raw: str) -> Optional[str]:
        s = str(raw or "").strip()
        if not s:
            return None
        row = self._fetchone("SELECT spot_key FROM spots WHERE spot_key = ?", (s,))
        if row:
            return str(row["spot_key"])

        candidates: List[Tuple[str, str]] = []
        if "\t" in s:
            p = s.split("\t", 1)
            candidates.append((p[0].strip(), p[1].strip()))
        for sep in ["|", ","]:
            if sep in s:
                p = s.split(sep, 1)
                candidates.append((p[0].strip(), p[1].strip()))
        if " " in s and "\t" not in s and "|" not in s and "," not in s:
            parts = s.split()
            if len(parts) >= 2:
                candidates.append((parts[0].strip(), parts[1].strip()))

        for sid, bc in candidates:
            k = _spot_key(sid, bc)
            row = self._fetchone("SELECT spot_key FROM spots WHERE spot_key = ?", (k,))
            if row:
                return str(row["spot_key"])

        rows = self._fetchall("SELECT spot_key FROM spots WHERE barcode = ? LIMIT 2", (s,))
        if len(rows) == 1:
            return str(rows[0]["spot_key"])
        rows = self._fetchall("SELECT spot_key FROM spots WHERE slice_id = ? LIMIT 2", (s,))
        if len(rows) == 1:
            return str(rows[0]["spot_key"])
        return None

    def _spatial_context_for_row(self, row: sqlite3.Row, k_neighbors: int = 6) -> Dict[str, Any]:
        x0 = _coerce_float(row["x"])
        y0 = _coerce_float(row["y"])
        sid = str(row["slice_id"] or "")
        if x0 is None or y0 is None or not sid:
            return {"available": False}

        rows = self._fetchall(
            """
            SELECT spot_key, x, y, dominant_cell_type, dominant_cell_prop
            FROM spots
            WHERE slice_id = ? AND x IS NOT NULL AND y IS NOT NULL
            """,
            (sid,),
        )
        cand: List[Tuple[float, sqlite3.Row]] = []
        for r in rows:
            sk = str(r["spot_key"] or "")
            if sk == row["spot_key"]:
                continue
            x = _coerce_float(r["x"])
            y = _coerce_float(r["y"])
            if x is None or y is None:
                continue
            d2 = (x - x0) * (x - x0) + (y - y0) * (y - y0)
            cand.append((d2, r))
        cand.sort(key=lambda t: t[0])
        neigh = cand[: max(0, int(k_neighbors))]
        dom = str(row["dominant_cell_type"] or "").strip()
        labels = [str(r["dominant_cell_type"] or "").strip() for _, r in neigh if str(r["dominant_cell_type"] or "").strip()]
        label_agreement = None
        entropy = None
        local_dominance = None
        if neigh:
            label_agreement = sum(1 for _, r in neigh if str(r["dominant_cell_type"] or "").strip() == dom) / len(neigh)
            if labels:
                counts: Dict[str, int] = {}
                for lab in labels:
                    counts[lab] = counts.get(lab, 0) + 1
                probs = [v / len(labels) for v in counts.values()]
                if len(probs) <= 1:
                    entropy = 0.0
                else:
                    h = -sum(p * math.log(p + 1e-12) for p in probs)
                    entropy = h / (math.log(len(probs)) + 1e-12)
            same_props = [
                float(r["dominant_cell_prop"] or 0.0)
                for _, r in neigh
                if str(r["dominant_cell_type"] or "").strip() == dom
            ]
            local_dominance = sum(same_props) / len(same_props) if same_props else 0.0

        neighbors = []
        for d2, r in neigh:
            neighbors.append(
                {
                    "cell_type": str(r["dominant_cell_type"] or ""),
                    "proportion": None if r["dominant_cell_prop"] is None else round(float(r["dominant_cell_prop"]), 4),
                    "distance": round(math.sqrt(float(d2)), 4),
                }
            )
        return {
            "available": True,
            "x": round(float(x0), 4),
            "y": round(float(y0), 4),
            "array_row": row["array_row"],
            "array_col": row["array_col"],
            "n_neighbors": len(neighbors),
            "neighborhood_consensus": {
                "label_agreement_ratio": None if label_agreement is None else round(float(label_agreement), 4),
                "comp_cosine_mean": None,
            },
            "boundary_label_discordance": None if label_agreement is None else round(float(1.0 - label_agreement), 4),
            "boundary_entropy": None if entropy is None else round(float(entropy), 4),
            "local_dominance": None if local_dominance is None else round(float(local_dominance), 4),
            "neighbors": neighbors,
        }

    def record_from_row(self, row: sqlite3.Row) -> Dict[str, Any]:
        pathways: Dict[str, List[str]] = {"reactome_top": [], "gobp_top": []}
        if str(row["compact_top1"] or "").strip():
            pathways["compact_top"] = [str(row["compact_top1"]).strip()]
        decon_topk = []
        if str(row["dominant_cell_type"] or "").strip():
            decon_topk.append(
                {
                    "cell_type": str(row["dominant_cell_type"]).strip(),
                    "proportion": round(float(row["dominant_cell_prop"] or 0.0), 4),
                    "evidence_genes": [],
                }
            )
        rec = {
            "spot_key": str(row["spot_key"] or ""),
            "slice_id": str(row["slice_id"] or ""),
            "barcode": str(row["barcode"] or ""),
            "species": str(row["species"] or ""),
            "organ": str(row["organ"] or ""),
            "top_genes": _parse_gene_list(row["top_genes"], 50),
            "decon_topk": decon_topk,
            "pathways": pathways,
            "quality_flags": {
                "atlas_minimal_record": True,
                "has_highconf_prior": bool(row["has_prior"]),
                "organ_mismatch_risk": _truthy_sql_value(row["organ_mismatch_risk"]),
                "low_evidence": _truthy_sql_value(row["low_evidence"]),
                "pathway_sparse": _truthy_sql_value(row["pathway_sparse"]),
            },
            "spatial_context": self._spatial_context_for_row(row),
        }
        self._merge_rgobp_pathways(str(row["spot_key"] or ""), rec)
        return rec

    def get_record(self, spot_key: str) -> Optional[Dict[str, Any]]:
        evidence = self.get_evidence_record(spot_key)
        if evidence is not None:
            row = self.get_row(spot_key)
            if row is not None:
                evidence["spatial_context"] = self._spatial_context_for_row(row)
            return evidence
        row = self.get_row(spot_key)
        if row is None:
            return None
        return self.record_from_row(row)


class ChatRequest(BaseModel):
    spot_key: Optional[str] = Field(None, description="slice_id\\tbarcode; optional for general chat")
    message: str = Field(..., min_length=1)
    session_id: Optional[str] = None
    lang: Optional[str] = None
    enable_thinking: Optional[bool] = None


class TextRetrievalRequest(BaseModel):
    query: str = Field(..., min_length=2, max_length=1200)
    limit: int = Field(6, ge=1, le=20)


class ImageRetrievalRequest(BaseModel):
    image_base64: Optional[str] = Field(None, min_length=32)
    local_image_base64: Optional[str] = Field(None, min_length=32)
    context_image_base64: Optional[str] = Field(None, min_length=32)
    filename: Optional[str] = None
    species: Optional[str] = None
    organ: Optional[str] = None
    spot_diameter_um: Optional[float] = None
    context_diameter_um: Optional[float] = None
    microns_per_pixel: Optional[float] = None
    limit: int = Field(6, ge=1, le=20)


class ResetRequest(BaseModel):
    session_id: str
    spot_key: Optional[str] = None


class HistAgentChatService:
    def __init__(self) -> None:
        self.input_jsonl = os.getenv("HISTAGENT_INPUT_JSONL", DEFAULT_INPUT_JSONL)
        self.atlas_sqlite = os.getenv("HISTAGENT_ATLAS_SQLITE", DEFAULT_ATLAS_SQLITE)
        self.api_base_url = os.getenv("HISTAGENT_API_BASE_URL", "http://127.0.0.1:8001/v1")
        self.model = os.getenv("HISTAGENT_MODEL", "Qwen3-8B-local")
        self.api_key = os.getenv("HISTAGENT_API_KEY", os.getenv("SILRA_API_KEY", "local-empty-key"))
        self.max_history_turns = int(os.getenv("HISTAGENT_MAX_HISTORY_TURNS", "6"))
        self.temperature = float(os.getenv("HISTAGENT_TEMPERATURE", "0.2"))
        self.timeout_sec = int(os.getenv("HISTAGENT_TIMEOUT_SEC", "120"))
        self.enable_thinking = os.getenv("HISTAGENT_ENABLE_THINKING", "false").strip().lower() in {"1", "true", "yes", "on"}

        self.spot_db, self.spot_list = _load_spot_db(self.input_jsonl)
        self.atlas = AtlasSpotStore(self.atlas_sqlite)
        self.species_list = sorted({str(x.get("species", "")).strip() for x in self.spot_list if str(x.get("species", "")).strip()})
        species_to_organs_raw: Dict[str, set] = {}
        self.pair_to_spots: Dict[Tuple[str, str], List[Dict[str, str]]] = {}
        self.slice_to_spots: Dict[str, List[Dict[str, Any]]] = {}
        self.pair_to_slices_raw: Dict[Tuple[str, str], set] = {}
        self._slice_image_cache: Dict[Tuple[str, bool], Optional[Path]] = {}
        self._slice_image_info_cache: Dict[str, Dict[str, Any]] = {}
        self._thumb_lock = threading.Lock()
        for row in self.spot_list:
            sp = str(row.get("species", "")).strip()
            og = str(row.get("organ", "")).strip()
            sid = str(row.get("slice_id", "")).strip()
            if not sp or not og:
                continue
            species_to_organs_raw.setdefault(sp, set()).add(og)
            self.pair_to_spots.setdefault((sp, og), []).append(row)
            if sid:
                self.slice_to_spots.setdefault(sid, []).append(row)
                self.pair_to_slices_raw.setdefault((sp, og), set()).add(sid)
        # finalize deterministic ordering
        self.species_to_organs: Dict[str, List[str]] = {k: sorted(list(v)) for k, v in species_to_organs_raw.items()}
        self.pair_to_slices: Dict[Tuple[str, str], List[str]] = {}
        for key, slice_ids in self.pair_to_slices_raw.items():
            self.pair_to_slices[key] = sorted(
                slice_ids,
                key=lambda sid: (-len(self.slice_to_spots.get(sid, [])), sid),
            )

        self.sessions: Dict[str, List[Dict[str, str]]] = {}
        self.lock = threading.Lock()
        self._retrieval_index: Optional[List[Dict[str, Any]]] = None
        self._retrieval_lock = threading.Lock()
        self._image_model: Any = None
        self._image_tokenizer: Any = None
        self._image_device: Any = None
        self._image_model_lock = threading.Lock()

    def _session_key(self, session_id: str, spot_key: Optional[str]) -> str:
        sk = str(spot_key or "").strip() or "__general__"
        return f"{session_id}::{sk}"

    def display_spot_count(self) -> int:
        return self.atlas.count() if self.atlas.enabled else len(self.spot_db)

    def rich_spot_count(self) -> int:
        return len(self.spot_db)

    def atlas_prior_count(self) -> int:
        return self.atlas.prior_count() if self.atlas.enabled else 0

    def evidence_count(self) -> int:
        return self.atlas.evidence_count() if self.atlas.enabled else 0

    def complete_evidence_count(self) -> int:
        return self.atlas.complete_evidence_count() if self.atlas.enabled else 0

    def rgobp_count(self) -> int:
        return self.atlas.rgobp_count() if self.atlas.enabled else 0

    @staticmethod
    def _retrieval_text(rec: Dict[str, Any]) -> str:
        cell_types = [
            str(x.get("cell_type", "")).strip()
            for x in (rec.get("decon_topk", []) or [])
            if isinstance(x, dict)
        ]
        pathways = rec.get("pathways", {}) or {}
        pathway_names: List[str] = []
        for key in ("reactome_top", "gobp_top", "compact_top"):
            pathway_names.extend([str(x).strip() for x in (pathways.get(key, []) or [])])
        return " ".join(
            [
                str(rec.get("species", "")),
                str(rec.get("organ", "")),
                " ".join(rec.get("top_genes", []) or []),
                " ".join(cell_types),
                " ".join(pathway_names),
            ]
        ).lower()

    def _ensure_retrieval_index(self) -> List[Dict[str, Any]]:
        if self._retrieval_index is not None:
            return self._retrieval_index
        with self._retrieval_lock:
            if self._retrieval_index is not None:
                return self._retrieval_index
            rows: List[Dict[str, Any]] = []
            for rec in self.spot_db.values():
                genes = [str(x).strip() for x in (rec.get("top_genes", []) or []) if str(x).strip()]
                gene_rank = {g.upper(): i for i, g in enumerate(genes)}
                cell_types = [
                    str(x.get("cell_type", "")).strip()
                    for x in (rec.get("decon_topk", []) or [])
                    if isinstance(x, dict) and str(x.get("cell_type", "")).strip()
                ]
                pathways = rec.get("pathways", {}) or {}
                pathway_names: List[str] = []
                for key in ("reactome_top", "gobp_top", "compact_top"):
                    pathway_names.extend([str(x).strip() for x in (pathways.get(key, []) or []) if str(x).strip()])
                text = self._retrieval_text(rec)
                rows.append(
                    {
                        "rec": rec,
                        "gene_rank": gene_rank,
                        "dominant": _dominant_cell_type(rec).lower(),
                        "cells": " | ".join(cell_types).lower(),
                        "pathways": " | ".join(pathway_names).lower(),
                        "text": text,
                        "tokens": set(re.findall(r"[a-z0-9][a-z0-9_.+-]{2,}", text)),
                    }
                )
            self._retrieval_index = rows
            return rows

    @staticmethod
    def _clean_retrieval_terms(value: Any, limit: int = 20) -> List[str]:
        if not isinstance(value, list):
            return []
        return _uniq_keep_order([str(x).strip() for x in value if str(x).strip()], limit)

    def _interpret_retrieval_query(self, query: str) -> Dict[str, List[str]]:
        prompt = """Convert a natural-language spatial transcriptomics atlas query into a compact JSON search plan.
Return JSON only with exactly these array fields:
{"genes":[],"cell_types":[],"pathways":[],"organs":[],"species":[],"keywords":[]}
Use canonical English gene symbols, cell states/types, biological programs/pathways, organs, and species that are explicitly stated or strongly implied. Translate Chinese biological terms to English. Keep at most 12 entries per field. Do not add prose."""
        parsed: Dict[str, Any] = {}
        try:
            result = self._chat_completion(
                [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": str(query).strip()},
                ],
                temperature=0.0,
                max_tokens=320,
                enable_thinking=False,
            )
            raw = self._strip_think_tags(str(result.get("answer", ""))).strip()
            match = re.search(r"\{.*\}", raw, flags=re.S)
            if match:
                parsed = json.loads(match.group(0))
        except Exception:
            parsed = {}

        fallback_tokens = [
            x.lower()
            for x in re.findall(r"[A-Za-z][A-Za-z0-9_.+-]{2,}", str(query))
            if x.lower() not in {"with", "from", "that", "this", "spot", "spots", "find", "show", "atlas"}
        ]
        gene_like = [
            x.upper()
            for x in re.findall(r"\b[A-Za-z][A-Za-z0-9-]{1,11}\b", str(query))
            if any(ch.isdigit() for ch in x) or x.isupper()
        ]
        return {
            "genes": _uniq_keep_order(
                [x.upper() for x in self._clean_retrieval_terms(parsed.get("genes"))] + gene_like,
                20,
            ),
            "cell_types": self._clean_retrieval_terms(parsed.get("cell_types")),
            "pathways": self._clean_retrieval_terms(parsed.get("pathways")),
            "organs": self._clean_retrieval_terms(parsed.get("organs")),
            "species": self._clean_retrieval_terms(parsed.get("species")),
            "keywords": _uniq_keep_order(
                [x.lower() for x in self._clean_retrieval_terms(parsed.get("keywords"))] + fallback_tokens,
                24,
            ),
        }

    @staticmethod
    def _format_retrieval_result(rec: Dict[str, Any], similarity: float, matched: List[str]) -> Dict[str, Any]:
        decon = rec.get("decon_topk", []) or []
        pathways = rec.get("pathways", {}) or {}
        pathway_names: List[str] = []
        for key in ("reactome_top", "gobp_top", "compact_top"):
            pathway_names.extend([str(x).strip() for x in (pathways.get(key, []) or []) if str(x).strip()])
        dominant = ""
        if decon and isinstance(decon[0], dict):
            dominant = str(decon[0].get("cell_type", "")).strip()
        return {
            "spot_key": rec.get("spot_key", ""),
            "slice_id": rec.get("slice_id", ""),
            "barcode": rec.get("barcode", ""),
            "species": rec.get("species", ""),
            "organ": rec.get("organ", ""),
            "dominant_cell_type": dominant,
            "top_genes": list(rec.get("top_genes", []) or [])[:10],
            "pathways": _uniq_keep_order(pathway_names, 4),
            "similarity": round(max(0.0, min(1.0, float(similarity))), 4),
            "matched_evidence": _uniq_keep_order(matched, 8),
        }

    def retrieve_text(self, query: str, limit: int) -> Dict[str, Any]:
        plan = self._interpret_retrieval_query(query)
        q_tokens = set(plan["keywords"])
        scored: List[Tuple[float, Dict[str, Any], List[str]]] = []
        for row in self._ensure_retrieval_index():
            rec = row["rec"]
            score = 0.0
            matched: List[str] = []
            for gene in plan["genes"]:
                rank = row["gene_rank"].get(gene.upper())
                if rank is not None:
                    score += 3.0 * (1.0 - min(rank, 49) / 60.0)
                    matched.append(gene.upper())
            for term in plan["cell_types"]:
                low = term.lower()
                informative = [
                    p
                    for p in re.findall(r"[a-z0-9+]+", low)
                    if len(p) > 2 and p not in {"cell", "cells", "type", "state"}
                ]
                if low and row["dominant"] and (low in row["dominant"] or row["dominant"] in low):
                    score += 4.0
                    matched.append(term)
                elif low and low in row["cells"]:
                    score += 2.8
                    matched.append(term)
                elif informative and any(p in row["dominant"] for p in informative):
                    score += 1.5
                    matched.append(term)
            for term in plan["pathways"]:
                low = term.lower()
                words = [p for p in re.findall(r"[a-z0-9]+", low) if len(p) > 3]
                if low in row["pathways"] or (words and sum(p in row["pathways"] for p in words) >= max(1, len(words) // 2)):
                    score += 2.1
                    matched.append(term)
            if plan["organs"]:
                organ = str(rec.get("organ", "")).lower()
                if not any(x.lower() in organ or organ in x.lower() for x in plan["organs"]):
                    continue
                score += 1.8
                matched.append(str(rec.get("organ", "")))
            if plan["species"]:
                species = str(rec.get("species", "")).lower()
                normalized_species = {
                    "homo sapiens": "human",
                    "mus musculus": "mouse",
                }
                requested_species = [
                    normalized_species.get(x.lower(), x.lower())
                    for x in plan["species"]
                ]
                if not any(x in species or species in x for x in requested_species):
                    continue
                score += 0.8
            lexical = len(q_tokens.intersection(row["tokens"]))
            if lexical:
                score += min(2.0, 0.35 * lexical)
            if score > 0:
                scored.append((score, rec, matched))

        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[: max(1, int(limit))]
        denom = max(5.0, top[0][0] if top else 5.0)
        items = [
            self._format_retrieval_result(rec, 0.35 + 0.63 * (score / denom), matched)
            for score, rec, matched in top
        ]
        return {
            "mode": "natural_language",
            "query": query,
            "query_interpretation": plan,
            "candidate_spots": len(self._ensure_retrieval_index()),
            "items": items,
        }

    def _load_image_model(self) -> None:
        if self._image_model is not None:
            return
        with self._image_model_lock:
            if self._image_model is not None:
                return
            try:
                import torch
                from histagent import load_pretrained
            except Exception as e:
                raise HTTPException(status_code=503, detail=f"HistAgent image runtime is unavailable: {e}") from e

            device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
            checkpoint = os.getenv("HISTAGENT_CHECKPOINT", "wli13/HistAgent")
            base_checkpoint = os.getenv("HISTAGENT_BASE_CHECKPOINT", "").strip() or None
            model, tokenizer, _ = load_pretrained(
                checkpoint,
                token=os.getenv("HF_TOKEN") or None,
                base_checkpoint_path=base_checkpoint,
                device=device,
            )
            self._image_model = model
            self._image_tokenizer = tokenizer
            self._image_device = device

    def _predict_image_genes(
        self,
        local_image_bytes: bytes,
        context_image_bytes: bytes,
        organ: str,
        species: str,
    ) -> List[str]:
        if not PIL_AVAILABLE:
            raise HTTPException(status_code=503, detail="Pillow is unavailable")

        def open_view(image_bytes: bytes, label: str) -> Any:
            if len(image_bytes) > 16 * 1024 * 1024:
                raise HTTPException(status_code=413, detail=f"{label} image must be 16 MB or smaller")
            try:
                image = Image.open(io.BytesIO(image_bytes))
                image = ImageOps.exif_transpose(image).convert("RGB")
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Cannot decode {label} image: {e}") from e
            if image.width < 4 or image.height < 4:
                raise HTTPException(status_code=400, detail=f"{label} image is too small")
            return image

        local_source = open_view(local_image_bytes, "local")
        context_source = open_view(context_image_bytes, "context")

        self._load_image_model()
        try:
            from histagent import predict_ranked_genes

            return predict_ranked_genes(
                self._image_model,
                self._image_tokenizer,
                local_source,
                context_source,
                organ=str(organ or "Unknown"),
                species=str(species or "unknown"),
                top_k=50,
                device=self._image_device,
            )
        except Exception as e:
            raise HTTPException(
                status_code=503,
                detail=f"HistAgent image inference failed: {e}",
            ) from e

    def retrieve_image(
        self,
        local_image_bytes: bytes,
        context_image_bytes: bytes,
        organ: str,
        species: str,
        limit: int,
        input_mode: str = "paired_fov",
    ) -> Dict[str, Any]:
        genes = self._predict_image_genes(local_image_bytes, context_image_bytes, organ, species)
        if not genes:
            raise HTTPException(status_code=502, detail="HistAgent did not produce a gene ranking")
        scored: List[Tuple[float, Dict[str, Any], List[str]]] = []
        for row in self._ensure_retrieval_index():
            rec = row["rec"]
            if organ and str(rec.get("organ", "")).lower() != str(organ).lower():
                continue
            if species and str(rec.get("species", "")).lower() != str(species).lower():
                continue
            score = 0.0
            matched: List[str] = []
            for q_rank, gene in enumerate(genes):
                atlas_rank = row["gene_rank"].get(gene.upper())
                if atlas_rank is None:
                    continue
                weight = (1.0 - min(q_rank, 49) / 60.0) * (1.0 - min(atlas_rank, 49) / 60.0)
                score += weight
                if len(matched) < 8:
                    matched.append(gene.upper())
            if score > 0:
                scored.append((score, rec, matched))
        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[: max(1, int(limit))]
        denom = max(1.0, top[0][0] if top else 1.0)
        return {
            "mode": "he_image",
            "input_mode": input_mode,
            "query_genes": genes,
            "candidate_spots": len(self._ensure_retrieval_index()),
            "items": [
                self._format_retrieval_result(rec, 0.35 + 0.63 * (score / denom), matched)
                for score, rec, matched in top
            ],
        }

    def get_record(self, spot_key: str) -> Optional[Dict[str, Any]]:
        key = str(spot_key or "").strip()
        if not key:
            return None
        if key in self.spot_db:
            return self.spot_db[key]
        if self.atlas.enabled:
            return self.atlas.get_record(key)
        return None

    def search_spots(self, query: str, limit: int) -> List[Dict[str, str]]:
        if self.atlas.enabled:
            return self.atlas.search_spots(query, limit)
        q = query.strip().lower()
        if not q:
            return self.spot_list[:limit]
        out: List[Dict[str, str]] = []
        for row in self.spot_list:
            text = " ".join([row["spot_key"], row["slice_id"], row["barcode"], row["species"], row["organ"]]).lower()
            if q in text:
                out.append(row)
                if len(out) >= limit:
                    break
        return out

    def list_species(self) -> List[str]:
        if self.atlas.enabled:
            return self.atlas.list_species()
        return list(self.species_list)

    def list_organs(self, species: str) -> List[str]:
        if self.atlas.enabled:
            return self.atlas.list_organs(species)
        sp = str(species or "").strip()
        if not sp:
            # all organs if species is not provided
            return sorted({str(x.get("organ", "")).strip() for x in self.spot_list if str(x.get("organ", "")).strip()})
        return list(self.species_to_organs.get(sp, []))

    def list_spots_filtered(self, species: str, organ: str, query: str, limit: int) -> List[Dict[str, str]]:
        if self.atlas.enabled:
            return self.atlas.list_spots_filtered(species, organ, query, limit)
        sp = str(species or "").strip()
        og = str(organ or "").strip()
        q = str(query or "").strip().lower()

        if sp and og:
            base = self.pair_to_spots.get((sp, og), [])
        elif sp:
            base = [x for x in self.spot_list if str(x.get("species", "")).strip() == sp]
        elif og:
            base = [x for x in self.spot_list if str(x.get("organ", "")).strip() == og]
        else:
            base = self.spot_list

        if not q:
            return base[:limit]

        out: List[Dict[str, str]] = []
        for row in base:
            text = " ".join([row["spot_key"], row["slice_id"], row["barcode"], row["species"], row["organ"]]).lower()
            if q in text:
                out.append(row)
                if len(out) >= limit:
                    break
        return out

    def find_slice_image_path(self, slice_id: str, allow_large: bool = False) -> Optional[Path]:
        sid = str(slice_id or "").strip()
        if not sid:
            return None
        cache_key = (sid, bool(allow_large))
        if cache_key in self._slice_image_cache:
            return self._slice_image_cache[cache_key]
        for d in SLICE_IMAGE_DIRS:
            for ext in SLICE_IMAGE_EXTS:
                p = d / f"{sid}{ext}"
                if not p.exists():
                    continue
                if allow_large or p.stat().st_size <= MAX_DIRECT_IMAGE_BYTES:
                    self._slice_image_cache[cache_key] = p
                    return p
        self._slice_image_cache[cache_key] = None
        return None

    def _slice_image_info(self, path: Path) -> Dict[str, Any]:
        key = str(path)
        if key in self._slice_image_info_cache:
            return self._slice_image_info_cache[key]
        info: Dict[str, Any] = {"bytes": path.stat().st_size, "width": None, "height": None}
        if PIL_AVAILABLE:
            try:
                with Image.open(path) as img:  # type: ignore[union-attr]
                    info["width"] = int(img.width)
                    info["height"] = int(img.height)
            except Exception:
                pass
        self._slice_image_info_cache[key] = info
        return info

    def can_serve_slice_image(self, slice_id: str) -> bool:
        p = self.find_slice_image_path(slice_id, allow_large=True)
        if not p:
            return False
        size = p.stat().st_size
        return size <= MAX_DIRECT_IMAGE_BYTES or (PIL_AVAILABLE and size <= MAX_THUMB_SOURCE_BYTES)

    def _thumbnail_path(self, source: Path, max_dim: int) -> Path:
        st = source.stat()
        raw = f"{source}|{st.st_size}|{st.st_mtime_ns}|{max_dim}"
        digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]
        return THUMB_CACHE_DIR / f"{digest}.jpg"

    def get_slice_image_response_path(self, slice_id: str, max_dim: int) -> Optional[Path]:
        source = self.find_slice_image_path(slice_id, allow_large=True)
        if not source:
            return None
        info = self._slice_image_info(source)
        source_bytes = int(info.get("bytes") or source.stat().st_size)
        width = int(info.get("width") or 0)
        height = int(info.get("height") or 0)
        max_dim = max(256, min(int(max_dim or SLICE_THUMB_MAX_DIM), 6000))

        needs_thumb = source_bytes > MAX_DIRECT_IMAGE_BYTES
        if width > 0 and height > 0:
            needs_thumb = needs_thumb or max(width, height) > max_dim
        if not needs_thumb:
            return source
        if not PIL_AVAILABLE or source_bytes > MAX_THUMB_SOURCE_BYTES:
            return None

        thumb = self._thumbnail_path(source, max_dim)
        if thumb.exists() and thumb.stat().st_size > 0:
            return thumb
        THUMB_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with self._thumb_lock:
            if thumb.exists() and thumb.stat().st_size > 0:
                return thumb
            try:
                with Image.open(source) as img:  # type: ignore[union-attr]
                    img = ImageOps.exif_transpose(img)  # type: ignore[union-attr]
                    img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)  # type: ignore[union-attr]
                    if img.mode not in {"RGB", "L"}:
                        img = img.convert("RGB")
                    thumb_tmp = thumb.with_suffix(".tmp.jpg")
                    img.save(thumb_tmp, format="JPEG", quality=86, optimize=True)
                    thumb_tmp.replace(thumb)
            except Exception:
                return None
        return thumb

    def get_slice_image_meta(self, slice_id: str) -> Dict[str, Any]:
        p = self.find_slice_image_path(slice_id, allow_large=True)
        if not p or not self.can_serve_slice_image(slice_id):
            return {
                "has_image": False,
                "image_url": None,
                "image_width": None,
                "image_height": None,
            }
        info = self._slice_image_info(p)
        return {
            "has_image": True,
            "image_url": f"/api/slice/image?slice_id={quote(str(slice_id or '').strip(), safe='')}&max_dim={SLICE_THUMB_MAX_DIM}",
            "image_width": info.get("width"),
            "image_height": info.get("height"),
        }

    def list_slices(self, species: str, organ: str) -> List[Dict[str, Any]]:
        if self.atlas.enabled:
            out = []
            for row in self.atlas.list_slices(species, organ):
                sid = str(row.get("slice_id", "")).strip()
                out.append(
                    {
                        **row,
                        "has_image": self.can_serve_slice_image(sid),
                    }
                )
            out.sort(
                key=lambda x: (
                    not bool(x.get("has_image")),
                    -int(int(x.get("n_with_prior", 0)) > 0),
                    -int(x.get("n_with_prior", 0)),
                    -int(x.get("n_spots", 0)),
                    str(x.get("slice_id", "")),
                )
            )
            return out

        sp = str(species or "").strip()
        og = str(organ or "").strip()
        if sp and og:
            slice_ids = self.pair_to_slices.get((sp, og), [])
        else:
            base = self.list_spots_filtered(sp, og, "", len(self.spot_list))
            slice_ids = sorted(
                {str(x.get("slice_id", "")).strip() for x in base if str(x.get("slice_id", "")).strip()},
                key=lambda sid: (-len(self.slice_to_spots.get(sid, [])), sid),
            )
        out: List[Dict[str, Any]] = []
        for sid in slice_ids:
            spots = self.slice_to_spots.get(sid, [])
            if not spots:
                continue
            first = spots[0]
            out.append(
                {
                    "slice_id": sid,
                    "species": first.get("species", ""),
                    "organ": first.get("organ", ""),
                    "n_spots": len(spots),
                    "has_image": self.can_serve_slice_image(sid),
                    "sample_spot_key": first.get("spot_key", ""),
                }
            )
        out.sort(key=lambda x: (not bool(x.get("has_image")), -int(x.get("n_spots", 0)), str(x.get("slice_id", ""))))
        return out

    def get_slice_map(self, species: str, organ: str, slice_id: str, limit: int) -> Dict[str, Any]:
        sid = str(slice_id or "").strip()
        if not sid:
            slices = self.list_slices(species, organ)
            if not slices:
                raise HTTPException(status_code=404, detail="No slices found for current filters")
            sid = slices[0]["slice_id"]

        if self.atlas.enabled:
            spots_raw = self.atlas.get_slice_spots(species, organ, sid, limit)
            total_spots = self.atlas.get_slice_total(species, organ, sid)
        else:
            spots_raw = list(self.slice_to_spots.get(sid, []))
            total_spots = len(spots_raw)
        sp = str(species or "").strip()
        og = str(organ or "").strip()
        if sp and not self.atlas.enabled:
            spots_raw = [x for x in spots_raw if str(x.get("species", "")).strip() == sp]
        if og and not self.atlas.enabled:
            spots_raw = [x for x in spots_raw if str(x.get("organ", "")).strip() == og]
        if not spots_raw:
            raise HTTPException(status_code=404, detail=f"No spots found for slice: {sid}")

        if not self.atlas.enabled:
            spots_raw = spots_raw[: max(1, int(limit))]
        xs = [_coerce_float(x.get("x")) for x in spots_raw]
        ys = [_coerce_float(x.get("y")) for x in spots_raw]
        coords = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
        bounds: Dict[str, Optional[float]] = {"min_x": None, "max_x": None, "min_y": None, "max_y": None}
        if coords:
            xvals = [x for x, _ in coords]
            yvals = [y for _, y in coords]
            bounds = {
                "min_x": min(xvals),
                "max_x": max(xvals),
                "min_y": min(yvals),
                "max_y": max(yvals),
            }

        image_meta = self.get_slice_image_meta(sid)
        spots = [
            {
                "spot_key": row.get("spot_key", ""),
                "slice_id": row.get("slice_id", ""),
                "barcode": row.get("barcode", ""),
                "species": row.get("species", ""),
                "organ": row.get("organ", ""),
                "x": _coerce_float(row.get("x")),
                "y": _coerce_float(row.get("y")),
                "dominant_cell_type": row.get("dominant_cell_type", ""),
            }
            for row in spots_raw
        ]
        first = spots_raw[0]
        return {
            "slice_id": sid,
            "species": first.get("species", ""),
            "organ": first.get("organ", ""),
            "n_spots": len(spots),
            "total_spots": total_spots,
            "has_image": bool(image_meta.get("has_image")),
            "image_url": image_meta.get("image_url"),
            "image_width": image_meta.get("image_width"),
            "image_height": image_meta.get("image_height"),
            "bounds": bounds,
            "spots": spots,
        }

    def resolve_spot_key(self, raw: str) -> Optional[str]:
        s = str(raw or "").strip()
        if not s:
            return None
        if s in self.spot_db:
            return s
        if self.atlas.enabled:
            atlas_key = self.atlas.resolve_spot_key(s)
            if atlas_key:
                return atlas_key

        # common manual formats: "slice<TAB>barcode", "slice|barcode", "slice,barcode", "slice barcode"
        candidates: List[Tuple[str, str]] = []
        if "\t" in s:
            p = s.split("\t", 1)
            candidates.append((p[0].strip(), p[1].strip()))
        for sep in ["|", ","]:
            if sep in s:
                p = s.split(sep, 1)
                candidates.append((p[0].strip(), p[1].strip()))
        if " " in s and "\t" not in s and "|" not in s and "," not in s:
            parts = s.split()
            if len(parts) >= 2:
                candidates.append((parts[0].strip(), parts[1].strip()))

        for sid, bc in candidates:
            k = _spot_key(sid, bc)
            if k and k in self.spot_db:
                return k

        # fallback: exact match to barcode or slice_id if unique
        by_barcode = [r["spot_key"] for r in self.spot_list if str(r.get("barcode", "")).strip() == s]
        if len(by_barcode) == 1:
            return by_barcode[0]
        by_slice = [r["spot_key"] for r in self.spot_list if str(r.get("slice_id", "")).strip() == s]
        if len(by_slice) == 1:
            return by_slice[0]
        return None

    def _build_evidence_payload(self, rec: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "spot": {
                "spot_key": rec["spot_key"],
                "slice_id": rec["slice_id"],
                "barcode": rec["barcode"],
                "species": rec["species"],
                "organ": rec["organ"],
            },
            "top_genes": rec.get("top_genes", []),
            "decon_topk": rec.get("decon_topk", []),
            "pathways": rec.get("pathways", {}),
            "spatial_context": rec.get("spatial_context", {}),
            "quality_flags": rec.get("quality_flags", {}),
        }

    def _chat_completion(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        enable_thinking: Optional[bool] = None,
    ) -> Dict[str, Any]:
        url = self.api_base_url.rstrip("/") + "/chat/completions"
        thinking_flag = self.enable_thinking if enable_thinking is None else bool(enable_thinking)
        payload = {
            "model": self.model,
            "temperature": self.temperature if temperature is None else float(temperature),
            "messages": messages,
            "chat_template_kwargs": {"enable_thinking": thinking_flag},
        }
        if max_tokens is not None:
            payload["max_tokens"] = int(max_tokens)
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"}
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=self.timeout_sec)
        except requests.RequestException as e:
            raise HTTPException(status_code=502, detail=f"LLM connection failed: {e}") from e

        if resp.status_code >= 400:
            detail = resp.text[:1200]
            raise HTTPException(status_code=resp.status_code, detail=f"LLM API error: {detail}")

        try:
            obj = resp.json()
            content = _coerce_text_content(obj["choices"][0]["message"]["content"])
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Bad LLM response: {e}; raw={resp.text[:1200]}") from e

        return {"answer": content, "usage": obj.get("usage", {})}

    def _chat_completion_stream(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        enable_thinking: Optional[bool] = None,
    ) -> Iterator[str]:
        url = self.api_base_url.rstrip("/") + "/chat/completions"
        thinking_flag = self.enable_thinking if enable_thinking is None else bool(enable_thinking)
        payload = {
            "model": self.model,
            "temperature": self.temperature if temperature is None else float(temperature),
            "messages": messages,
            "stream": True,
            "chat_template_kwargs": {"enable_thinking": thinking_flag},
        }
        if max_tokens is not None:
            payload["max_tokens"] = int(max_tokens)
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"}
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=self.timeout_sec, stream=True)
        except requests.RequestException as e:
            raise HTTPException(status_code=502, detail=f"LLM stream connection failed: {e}") from e

        if resp.status_code >= 400:
            detail = resp.text[:1200]
            raise HTTPException(status_code=resp.status_code, detail=f"LLM API error: {detail}")

        for raw_line in resp.iter_lines(decode_unicode=True):
            if raw_line is None:
                continue
            line = str(raw_line).strip()
            if not line:
                continue
            data = line[5:].strip() if line.startswith("data:") else line
            if not data:
                continue
            if data == "[DONE]":
                break
            try:
                obj = json.loads(data)
            except Exception:
                continue
            choices = obj.get("choices", []) or []
            if not choices:
                continue
            choice = choices[0] or {}
            delta = choice.get("delta", {}) or {}
            content = _coerce_text_content(delta.get("content"))
            if not content:
                content = _coerce_text_content((choice.get("message", {}) or {}).get("content"))
            if content:
                yield content

    @staticmethod
    def _strip_think_tags(text: str) -> str:
        s = str(text or "")
        # Remove model reasoning blocks if the backend emits them.
        s = re.sub(r"<think>.*?</think>\s*", "", s, flags=re.DOTALL | re.IGNORECASE)
        s = re.sub(r"</?think>", "", s, flags=re.IGNORECASE)
        return s.strip()

    @staticmethod
    def _strip_think_tags_partial(text: str) -> str:
        s = str(text or "")
        out: List[str] = []
        lower = s.lower()
        cursor = 0
        open_tag = "<think>"
        close_tag = "</think>"
        while True:
            start = lower.find(open_tag, cursor)
            if start < 0:
                out.append(s[cursor:])
                break
            out.append(s[cursor:start])
            end = lower.find(close_tag, start + len(open_tag))
            if end < 0:
                break
            cursor = end + len(close_tag)
        cleaned = "".join(out)
        cleaned = re.sub(r"</?think>", "", cleaned, flags=re.IGNORECASE)
        return cleaned

    def _spot_brief_for_suggest(self, rec: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not rec:
            return {"mode": "general"}
        decon = rec.get("decon_topk", []) or []
        pw = rec.get("pathways", {}) or {}
        spatial = rec.get("spatial_context", {}) or {}
        top_cells: List[Dict[str, Any]] = []
        for row in decon[:3]:
            if not isinstance(row, dict):
                continue
            top_cells.append(
                {
                    "cell_type": str(row.get("cell_type", "")).strip(),
                    "proportion": row.get("proportion", 0.0),
                }
            )
        return {
            "mode": "spot",
            "species": rec.get("species", ""),
            "organ": rec.get("organ", ""),
            "top_cells": top_cells,
            "reactome_top": (pw.get("reactome_top", []) or [])[:2],
            "gobp_top": (pw.get("gobp_top", []) or [])[:2],
            "spatial_available": bool(spatial.get("available", False)),
        }

    @staticmethod
    def _looks_like_origin_question(text: str) -> bool:
        s = str(text or "").strip()
        sl = s.lower()
        zh_kws = (
            "谁开发你",
            "谁开发你的",
            "谁做的",
            "谁研发的",
            "你是谁开发的",
            "你是哪个团队",
            "你来自哪里",
            "你是哪来的",
            "你的开发者",
            "开发方",
            "背后团队",
            "哪个实验室",
            "哪个团队",
            "哪个机构",
        )
        en_kws = (
            "who developed you",
            "who built you",
            "who made you",
            "where are you from",
            "who created you",
            "which lab developed you",
            "which team developed you",
            "who is behind you",
            "which institution developed you",
        )
        return any(k in s for k in zh_kws) or any(k in sl for k in en_kws)

    @staticmethod
    def _looks_like_identity_question(text: str) -> bool:
        s = str(text or "").strip()
        sl = s.lower()
        zh_kws = ("你是谁", "你是什么", "介绍一下你自己", "你是干什么的")
        en_kws = ("who are you", "what are you", "introduce yourself", "what do you do")
        return any(k in s for k in zh_kws) or any(k in sl for k in en_kws)

    def _direct_identity_or_origin_answer(self, text: str, lang: str) -> Optional[Dict[str, Any]]:
        is_zh = str(lang or "").lower().startswith("zh") or _has_cjk(text)
        if self._looks_like_origin_question(text):
            if is_zh:
                return {
                    "answer": (
                        "HistAgent 由香港中文大学（深圳）刘瑾团队开发。"
                        "底层可以调用不同的大语言模型作为推理基座，但这些基座模型不代表 HistAgent 本身的产品归属。"
                    ),
                    "followup_questions": [
                        "你能概括一下 HistAgent 的核心能力吗？",
                        "HistAgent 与底层语言模型之间是什么关系？",
                    ],
                }
            return {
                "answer": (
                    "HistAgent is developed by the Jin Liu Lab at The Chinese University of Hong Kong, Shenzhen. "
                    "It may call different language models as underlying reasoning backbones, but those base models do not define the product identity of HistAgent itself."
                ),
                "followup_questions": [
                    "Can you summarize the core capability of HistAgent in one sentence?",
                    "What is the relationship between HistAgent and the underlying language model?",
                ],
            }
        if self._looks_like_identity_question(text):
            if is_zh:
                return {
                    "answer": (
                        "我是 HistAgent，一个面向组织图像的分子推理助手。"
                        "我的核心任务是把组织图像相关的分子信号整合成可解释的生物学推断。"
                    ),
                    "followup_questions": [
                        "HistAgent 能解释哪些类型的分子信息？",
                        "HistAgent 与普通通用大模型有什么区别？",
                    ],
                }
            return {
                "answer": (
                    "I am HistAgent, a molecular reasoning assistant for histology images. "
                    "My core role is to turn image-related molecular signals into interpretable biological reasoning."
                ),
                "followup_questions": [
                    "What kinds of molecular information can HistAgent explain?",
                    "How is HistAgent different from a general-purpose language model?",
                ],
            }
        return None

    def _parse_followup_questions(self, text: str) -> List[str]:
        s = self._strip_think_tags(text or "")
        if not s:
            return []
        s = s.replace("```json", "```").replace("```JSON", "```")
        # Try strict JSON first.
        try:
            obj = json.loads(s)
            if isinstance(obj, list):
                out = [str(x).strip() for x in obj if str(x).strip()]
                return out[:2]
            if isinstance(obj, dict):
                cand = obj.get("questions", obj.get("followup_questions", []))
                if isinstance(cand, list):
                    out = [str(x).strip() for x in cand if str(x).strip()]
                    return out[:2]
        except Exception:
            pass
        # Try extracting JSON array substring.
        m = re.search(r"\[[\s\S]*\]", s)
        if m:
            try:
                arr = json.loads(m.group(0))
                if isinstance(arr, list):
                    out = [str(x).strip() for x in arr if str(x).strip()]
                    return out[:2]
            except Exception:
                pass
        # Fallback: parse lines.
        out: List[str] = []
        for ln in s.splitlines():
            t = re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", ln).strip()
            tl = t.lower()
            if (
                t
                and ("?" in t or "？" in t)
                and len(t) <= 180
                and "think" not in tl
                and not tl.startswith("{")
                and not tl.startswith("[")
            ):
                out.append(t)
            if len(out) >= 2:
                break
        return out[:2]

    @staticmethod
    def _question_sig(q: str) -> str:
        s = str(q or "").lower().strip()
        # remove punctuation/symbols and whitespace for rough semantic de-dup
        s = re.sub(r"[\s\W_]+", "", s, flags=re.UNICODE)
        return s

    @staticmethod
    def _looks_like_assistant_asks_user(q: str) -> bool:
        s = str(q or "").strip()
        if not s:
            return True
        sl = s.lower()
        zh_prefixes = (
            "你想",
            "你希望我",
            "你更想",
            "你会优先",
            "如果你给我",
            "如果你提供",
            "如果给我",
            "要不要我",
            "是否要我",
            "你要我",
        )
        en_prefixes = (
            "do you want",
            "would you like",
            "should i",
            "should we",
            "if you provide",
            "if you give me",
            "do you prefer",
            "would you prefer",
        )
        if any(s.startswith(prefix) for prefix in zh_prefixes):
            return True
        if any(sl.startswith(prefix) for prefix in en_prefixes):
            return True
        return False

    def _filter_followups(
        self,
        cands: List[str],
        user_q: str,
        blocked_questions: Optional[List[str]] = None,
        existing: Optional[List[str]] = None,
    ) -> List[str]:
        out: List[str] = []
        seen = set()
        blocked_sigs = set()
        uq_sig = self._question_sig(user_q)
        if uq_sig:
            blocked_sigs.add(uq_sig)
        for b in (blocked_questions or []):
            bs = self._question_sig(b)
            if bs:
                blocked_sigs.add(bs)
        for x in (cands or []):
            q = str(x or "").strip()
            if not q:
                continue
            if len(q) > 180:
                continue
            lq = q.lower()
            if "think" in lq:
                continue
            if ("?" not in q) and ("？" not in q):
                continue
            if self._looks_like_assistant_asks_user(q):
                continue
            sig = self._question_sig(q)
            if not sig:
                continue
            if sig in blocked_sigs:
                continue
            if sig in seen:
                continue
            if existing and any(sig == self._question_sig(e) for e in existing):
                continue
            seen.add(sig)
            out.append(q)
            if len(out) >= 2:
                break
        return out

    def _fallback_followups(self, rec: Optional[Dict[str, Any]], lang: str, user_q: str = "") -> List[str]:
        is_zh = str(lang or "").lower().startswith("zh")
        uq = str(user_q or "").lower()
        if rec:
            if is_zh:
                if any(k in uq for k in ["pathway", "通路", "机制"]):
                    return [
                        "你能把主导细胞与当前关键通路逐一对应起来吗？",
                        "哪一段机制链路证据最弱，下一步应如何验证？",
                    ]
                if any(k in uq for k in ["spatial", "邻域", "空间", "微环境"]):
                    return [
                        "结合邻域信息，这个 spot 更像组织边界还是核心区？",
                        "如果去掉空间信息，结论中哪一项会最先变化？",
                    ]
                if any(k in uq for k in ["主导细胞", "次主导", "差异", "证据"]):
                    return [
                        "在你给出的差异证据里，哪些属于细胞特异证据而不是共享标记？",
                        "若主导细胞比例下调 10%，结论里哪一条会先不稳定？",
                    ]
                return [
                    "主导细胞与次主导细胞的关键差异证据分别是什么？",
                    "如果做一个最低成本验证实验，你会优先验证哪条结论？",
                ]
            if any(k in uq for k in ["pathway", "mechanism"]):
                return [
                    "Can you map the dominant cell composition to the key pathway signal one by one?",
                    "Which segment of the mechanism chain is weakest and how should we validate it next?",
                ]
            if any(k in uq for k in ["spatial", "neighbor", "microenvironment"]):
                return [
                    "Using neighborhood evidence, is this spot more likely a boundary state or a core tissue state?",
                    "If spatial context is removed, which conclusion would change first?",
                ]
            if any(k in uq for k in ["dominant", "secondary", "difference", "evidence"]):
                return [
                    "Among the evidence you listed, which markers are cell-type-specific versus shared?",
                    "If dominant-cell proportion drops by 10%, which conclusion becomes unstable first?",
                ]
            return [
                "What are the strongest distinguishing evidence points between dominant and secondary cell states here?",
                "If we run one low-cost validation experiment, which conclusion should be tested first?",
            ]
        if is_zh:
            return [
                "这个问题更适合先从细胞组成、通路机制还是空间上下文切入？",
                "如果聚焦到一个具体 spot，最关键的分子读出会是什么？",
            ]
        return [
            "Should this question be approached first through cell composition, pathway mechanism, or spatial context?",
            "If we ground this in one specific spot, what would be the most informative molecular readout?",
        ]

    def _suggest_followups(
        self,
        rec: Optional[Dict[str, Any]],
        user_q: str,
        answer: str,
        lang: str,
        asked_questions: Optional[List[str]] = None,
    ) -> List[str]:
        brief = self._spot_brief_for_suggest(rec)
        is_zh = str(lang or "").lower().startswith("zh")
        system = (
            "You generate follow-up questions for an interactive molecular reasoning chat.\n"
            "Output EXACTLY 2 concise follow-up questions, one per line.\n"
            "Rules:\n"
            "1) Questions must be grounded in the given user question + assistant answer + spot brief.\n"
            "2) No generic filler, no repetition, no markdown, no explanations.\n"
            "3) Ask high-value next questions that deepen reasoning.\n"
            "4) Language must follow target_lang exactly.\n"
            "5) Each line must be phrased from the user's perspective so it can be clicked and sent directly.\n"
            "6) Never ask what the user wants you to do, and never use wording like 'Do you want', 'Would you like', 'Should I', 'Should we', '你想', or '你希望我'.\n"
            "7) End each line with '?' (or '？' for Chinese).\n"
        )
        user = (
            f"target_lang={ 'zh' if is_zh else 'en' }\n"
            f"user_question={user_q}\n"
            f"assistant_answer={answer}\n"
            f"spot_brief={json.dumps(brief, ensure_ascii=False)}\n"
            "Return two lines only."
        )
        try:
            out = self._chat_completion(
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                temperature=0.1,
                max_tokens=120,
                enable_thinking=False,
            )
            qs = self._parse_followup_questions(out.get("answer", ""))
            qs = self._filter_followups(qs, user_q=user_q, blocked_questions=asked_questions)
            if len(qs) >= 2:
                return qs[:2]
        except Exception:
            pass
        fb = self._filter_followups(
            self._fallback_followups(rec, lang, user_q=user_q),
            user_q=user_q,
            blocked_questions=asked_questions,
        )
        if len(fb) >= 2:
            return fb[:2]
        # last resort: language-safe generic prompts
        generic = (
            ["这个 spot 最值得优先展开的机制链路是什么？", "哪一条结论最需要进一步验证？"]
            if is_zh
            else [
                "What is the most informative mechanism chain to expand for this spot first?",
                "Which conclusion here most urgently needs validation next?",
            ]
        )
        return self._filter_followups(generic, user_q=user_q, blocked_questions=asked_questions, existing=fb)[:2]

    def _build_system_policy(self, rec: Optional[Dict[str, Any]]) -> str:
        if rec:
            return (
                "You are HistAgent, a molecular reasoning assistant for histology images.\n"
                "Your core role is to perform spot-level molecular reasoning from tissue images.\n"
                "The evidence packet is treated as molecular signals inferred by HistAgent from the selected spot image.\n"
                "If the user asks who you are, describe yourself consistently as HistAgent, a molecular reasoning system "
                "for tissue images. Only mention institutional affiliation or team origin if the user explicitly asks "
                "about who developed you or where you come from.\n"
                "If the user explicitly asks who developed you or where you come from, answer that HistAgent is "
                "developed by the Jin Liu Lab at The Chinese University of Hong Kong, Shenzhen.\n"
                "Do not claim that HistAgent itself was developed by Alibaba, Tongyi Lab, or Qwen. Those names may "
                "refer to an underlying base language model, not to the HistAgent product identity.\n"
                "You must answer ONLY based on the provided inferred signals packet.\n"
                "Rules:\n"
                "1) Do not fabricate genes, pathways, cell types, or spatial conclusions that are absent from evidence.\n"
                "2) If evidence is weak or ambiguous, state uncertainty clearly.\n"
                "3) Keep answers clear, useful, and biologically grounded.\n"
                "4) Answer in the same language as the user.\n"
                "5) User only asks questions; you already have spot-level inferred signals in context.\n"
                "6) Do not output chain-of-thought, hidden reasoning, or <think> tags."
            )
        return (
            "You are HistAgent, a molecular reasoning assistant for histology images.\n"
            "Your core role is to perform molecular reasoning from tissue images, but no spot evidence is provided in this conversation.\n"
            "If the user asks who you are, describe yourself consistently as HistAgent, a molecular reasoning system "
            "for tissue images. Only mention institutional affiliation or team origin if the user explicitly asks "
            "about who developed you or where you come from.\n"
            "If the user explicitly asks who developed you or where you come from, answer that HistAgent is "
            "developed by the Jin Liu Lab at The Chinese University of Hong Kong, Shenzhen.\n"
            "Do not claim that HistAgent itself was developed by Alibaba, Tongyi Lab, or Qwen. Those names may "
            "refer to an underlying base language model, not to the HistAgent product identity.\n"
            "Rules:\n"
            "1) Answer general molecular and biomedical questions clearly and accurately.\n"
            "2) If the user asks for spot-specific conclusions, ask them to select a spot first.\n"
            "3) Answer in the same language as the user.\n"
            "4) Do not output chain-of-thought, hidden reasoning, or <think> tags."
        )

    def _prepare_chat_turn(self, req: ChatRequest) -> Dict[str, Any]:
        spot_key = str(req.spot_key or "").strip()
        rec: Optional[Dict[str, Any]] = None
        if spot_key:
            spot_key = self.resolve_spot_key(spot_key) or spot_key
            rec = self.get_record(spot_key)
            if rec is None:
                raise HTTPException(status_code=404, detail=f"Unknown spot_key: {spot_key}")

        message = req.message.strip()
        if not message:
            raise HTTPException(status_code=400, detail="message is empty")

        session_id = req.session_id or str(uuid.uuid4())
        evidence = self._build_evidence_payload(rec) if rec else None
        s_key = self._session_key(session_id, spot_key)

        with self.lock:
            history = list(self.sessions.get(s_key, []))
        if len(history) > 2 * self.max_history_turns:
            history = history[-2 * self.max_history_turns :]

        asked_questions: List[str] = [
            str(x.get("content", "")).strip()
            for x in history
            if isinstance(x, dict) and str(x.get("role", "")) == "user" and str(x.get("content", "")).strip()
        ]
        asked_questions.append(message)

        ctx: Dict[str, Any] = {
            "spot_key": spot_key,
            "rec": rec,
            "message": message,
            "session_id": session_id,
            "s_key": s_key,
            "history": history,
            "asked_questions": asked_questions,
            "mode": "spot" if rec else "general",
        }

        direct_answer = self._direct_identity_or_origin_answer(message, req.lang or "")
        if direct_answer is not None:
            ctx["direct_answer"] = direct_answer
            return ctx

        messages: List[Dict[str, str]] = [{"role": "system", "content": self._build_system_policy(rec)}]
        if evidence is not None:
            evidence_prompt = "Spot evidence JSON:\n" + json.dumps(evidence, ensure_ascii=False)
            messages.append({"role": "system", "content": evidence_prompt})
        messages.extend(history)
        messages.append({"role": "user", "content": message})
        ctx["messages"] = messages
        return ctx

    def _commit_history(self, s_key: str, history: List[Dict[str, str]], message: str, answer: str) -> None:
        with self.lock:
            new_hist = history + [{"role": "user", "content": message}, {"role": "assistant", "content": answer}]
            self.sessions[s_key] = new_hist[-2 * self.max_history_turns :]

    def _build_chat_response(
        self,
        *,
        session_id: str,
        spot_key: str,
        mode: str,
        answer: str,
        followup_questions: List[str],
        usage: Dict[str, Any],
    ) -> Dict[str, Any]:
        return {
            "session_id": session_id,
            "spot_key": spot_key or None,
            "mode": mode,
            "model": self.model,
            "answer": answer,
            "followup_questions": followup_questions,
            "usage": usage,
        }

    def chat(self, req: ChatRequest) -> Dict[str, Any]:
        ctx = self._prepare_chat_turn(req)
        rec = ctx["rec"]
        message = ctx["message"]
        history = ctx["history"]
        asked_questions = ctx["asked_questions"]
        session_id = ctx["session_id"]
        s_key = ctx["s_key"]
        spot_key = ctx["spot_key"]

        direct_answer = ctx.get("direct_answer")
        if direct_answer is not None:
            answer = self._strip_think_tags(direct_answer["answer"])
            followup_questions = self._filter_followups(
                direct_answer.get("followup_questions", []),
                user_q=message,
                blocked_questions=asked_questions,
            )
            self._commit_history(s_key, history, message, answer)
            return self._build_chat_response(
                session_id=session_id,
                spot_key=spot_key,
                mode=ctx["mode"],
                answer=answer,
                followup_questions=followup_questions,
                usage={},
            )

        result = self._chat_completion(ctx["messages"], enable_thinking=req.enable_thinking)
        answer = self._strip_think_tags(result.get("answer", ""))
        followup_questions = self._suggest_followups(rec, message, answer, req.lang or "", asked_questions=asked_questions)
        self._commit_history(s_key, history, message, answer)
        return self._build_chat_response(
            session_id=session_id,
            spot_key=spot_key,
            mode=ctx["mode"],
            answer=answer,
            followup_questions=followup_questions,
            usage=result.get("usage", {}),
        )

    def chat_stream(self, req: ChatRequest) -> Iterator[bytes]:
        try:
            ctx = self._prepare_chat_turn(req)
        except HTTPException as e:
            yield _ndjson_line({"type": "error", "error": str(e.detail)})
            return

        rec = ctx["rec"]
        message = ctx["message"]
        history = ctx["history"]
        asked_questions = ctx["asked_questions"]
        session_id = ctx["session_id"]
        s_key = ctx["s_key"]
        spot_key = ctx["spot_key"]

        yield _ndjson_line(
            {
                "type": "start",
                "session_id": session_id,
                "spot_key": spot_key or None,
                "mode": ctx["mode"],
                "model": self.model,
            }
        )

        direct_answer = ctx.get("direct_answer")
        if direct_answer is not None:
            answer = self._strip_think_tags(direct_answer["answer"])
            if answer:
                yield _ndjson_line({"type": "delta", "delta": answer})
            followup_questions = self._filter_followups(
                direct_answer.get("followup_questions", []),
                user_q=message,
                blocked_questions=asked_questions,
            )
            self._commit_history(s_key, history, message, answer)
            yield _ndjson_line(
                {
                    "type": "final",
                    **self._build_chat_response(
                        session_id=session_id,
                        spot_key=spot_key,
                        mode=ctx["mode"],
                        answer=answer,
                        followup_questions=followup_questions,
                        usage={},
                    ),
                }
            )
            return

        raw_answer = ""
        sent_answer = ""
        try:
            for raw_delta in self._chat_completion_stream(ctx["messages"], enable_thinking=req.enable_thinking):
                raw_answer += raw_delta
                clean_answer = self._strip_think_tags_partial(raw_answer)
                if clean_answer.startswith(sent_answer):
                    new_delta = clean_answer[len(sent_answer) :]
                    if new_delta:
                        sent_answer = clean_answer
                        yield _ndjson_line({"type": "delta", "delta": new_delta})
                elif clean_answer != sent_answer:
                    sent_answer = clean_answer
                    yield _ndjson_line({"type": "replace", "answer": clean_answer})
        except HTTPException as e:
            yield _ndjson_line({"type": "error", "error": str(e.detail)})
            return
        except Exception as e:
            yield _ndjson_line({"type": "error", "error": f"Stream failed: {e}"})
            return

        answer = self._strip_think_tags(sent_answer or raw_answer)
        if not answer:
            try:
                result = self._chat_completion(ctx["messages"], enable_thinking=req.enable_thinking)
            except HTTPException as e:
                yield _ndjson_line({"type": "error", "error": str(e.detail)})
                return
            answer = self._strip_think_tags(result.get("answer", ""))
            if answer:
                yield _ndjson_line({"type": "replace", "answer": answer})

        followup_questions = self._suggest_followups(rec, message, answer, req.lang or "", asked_questions=asked_questions)
        self._commit_history(s_key, history, message, answer)
        yield _ndjson_line(
            {
                "type": "final",
                **self._build_chat_response(
                    session_id=session_id,
                    spot_key=spot_key,
                    mode=ctx["mode"],
                    answer=answer,
                    followup_questions=followup_questions,
                    usage={},
                ),
            }
        )

    def reset(self, session_id: str, spot_key: Optional[str]) -> int:
        removed = 0
        with self.lock:
            if spot_key:
                k = self._session_key(session_id, spot_key)
                if k in self.sessions:
                    del self.sessions[k]
                    removed = 1
            else:
                keys = [k for k in self.sessions if k.startswith(f"{session_id}::")]
                for k in keys:
                    del self.sessions[k]
                removed = len(keys)
        return removed


service = HistAgentChatService()
app = FastAPI(title="HistAgent Agent Module API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.middleware("http")
async def disable_cache_for_ui(request: Request, call_next):
    resp = await call_next(request)
    p = request.url.path
    if p == "/" or p.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
    return resp


@app.get("/")
def index() -> FileResponse:
    return FileResponse(
        str(STATIC_DIR / "index.html"),
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.get("/api/health")
def health() -> Dict[str, Any]:
    return {
        "ok": True,
        "spots": service.display_spot_count(),
        "rich_spots": service.rich_spot_count(),
        "atlas_enabled": service.atlas.enabled,
        "atlas_sqlite": service.atlas_sqlite,
        "atlas_prior_spots": service.atlas_prior_count(),
        "evidence_spots": service.evidence_count(),
        "complete_evidence_spots": service.complete_evidence_count(),
        "reactome_gobp_spots": service.rgobp_count(),
        "model": service.model,
        "api_base_url": service.api_base_url,
        "input_jsonl": service.input_jsonl,
    }


@app.get("/api/config")
def config() -> Dict[str, Any]:
    return {
        "model": service.model,
        "api_base_url": service.api_base_url,
        "input_jsonl": service.input_jsonl,
        "atlas_sqlite": service.atlas_sqlite,
        "atlas_enabled": service.atlas.enabled,
        "spot_count": service.display_spot_count(),
        "rich_spot_count": service.rich_spot_count(),
        "atlas_prior_spot_count": service.atlas_prior_count(),
        "evidence_spot_count": service.evidence_count(),
        "complete_evidence_spot_count": service.complete_evidence_count(),
        "reactome_gobp_spot_count": service.rgobp_count(),
        "enable_thinking_default": service.enable_thinking,
    }


@app.get("/api/spots")
def spots(query: str = Query("", description="search by slice/barcode/species/organ/spot_key"), limit: int = Query(30, ge=1, le=200)) -> Dict[str, Any]:
    return {"items": service.search_spots(query, limit)}


@app.get("/api/species")
def species() -> Dict[str, Any]:
    return {"items": service.list_species()}


@app.get("/api/organs")
def organs(species: str = Query("")) -> Dict[str, Any]:
    return {"items": service.list_organs(species)}


@app.get("/api/slices")
def slices(species: str = Query(""), organ: str = Query("")) -> Dict[str, Any]:
    return {"items": service.list_slices(species, organ)}


@app.get("/api/slice/map")
def slice_map(
    species: str = Query(""),
    organ: str = Query(""),
    slice_id: str = Query(""),
    limit: int = Query(5000, ge=1, le=50000),
) -> Dict[str, Any]:
    return service.get_slice_map(species, organ, slice_id, limit)


@app.get("/api/slice/image")
def slice_image(
    slice_id: str = Query(...),
    max_dim: int = Query(SLICE_THUMB_MAX_DIM, ge=256, le=6000),
) -> FileResponse:
    p = service.get_slice_image_response_path(slice_id, max_dim)
    if not p:
        raise HTTPException(status_code=404, detail=f"No image found for slice: {slice_id}")
    return FileResponse(str(p))


@app.get("/api/spots/filter")
def spots_filter(
    species: str = Query(""),
    organ: str = Query(""),
    query: str = Query(""),
    limit: int = Query(200, ge=1, le=500),
) -> Dict[str, Any]:
    return {"items": service.list_spots_filtered(species, organ, query, limit)}


@app.get("/api/spot")
def get_spot(spot_key: str = Query(...)) -> Dict[str, Any]:
    raw = spot_key.strip()
    key = service.resolve_spot_key(raw) or raw
    rec = service.get_record(key)
    if not rec:
        raise HTTPException(status_code=404, detail=f"Unknown spot_key: {raw}")
    return {"spot": rec}


@app.post("/api/retrieval/text")
def retrieval_text(req: TextRetrievalRequest) -> Dict[str, Any]:
    return service.retrieve_text(req.query.strip(), req.limit)


@app.post("/api/retrieval/image")
def retrieval_image(req: ImageRetrievalRequest) -> Dict[str, Any]:
    def decode_image(raw_value: Optional[str], label: str) -> bytes:
        raw = str(raw_value or "").strip()
        if not raw:
            raise HTTPException(status_code=422, detail=f"{label} image is required")
        if "," in raw and raw.lower().startswith("data:"):
            raw = raw.split(",", 1)[1]
        try:
            return base64.b64decode(raw, validate=True)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid {label} image: {e}") from e

    if req.local_image_base64 or req.context_image_base64:
        local_image_bytes = decode_image(req.local_image_base64, "local")
        context_image_bytes = decode_image(req.context_image_base64, "context")
        input_mode = "paired_fov"
    else:
        legacy_image_bytes = decode_image(req.image_base64, "query")
        local_image_bytes = legacy_image_bytes
        context_image_bytes = legacy_image_bytes
        input_mode = "legacy_single_image"

    return service.retrieve_image(
        local_image_bytes,
        context_image_bytes,
        str(req.organ or "").strip(),
        str(req.species or "").strip(),
        req.limit,
        input_mode,
    )


@app.post("/api/chat")
def chat(req: ChatRequest) -> Dict[str, Any]:
    return service.chat(req)


@app.post("/api/chat/stream")
def chat_stream(req: ChatRequest) -> StreamingResponse:
    return StreamingResponse(service.chat_stream(req), media_type="application/x-ndjson")


@app.post("/api/session/reset")
def reset(req: ResetRequest) -> Dict[str, Any]:
    removed = service.reset(req.session_id, req.spot_key)
    return {"removed": removed}
