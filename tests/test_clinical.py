import json
from pathlib import Path

import numpy as np
import pandas as pd
import torch

from histagent.clinical import _ABMIL, HistAgentClinical


def _write_case(
    root: Path,
    case_id: str,
    cohort: str,
    n_classes: int,
) -> dict:
    rng = np.random.default_rng(7)
    embeddings = rng.normal(size=(12, 512)).astype(np.float32)
    coords = np.array(
        [[x * 10, y * 10] for y in range(3) for x in range(4)]
    )
    regions = np.array([0, 1] * 6, dtype=np.int8)
    embedding_name = f"{case_id}.npz"
    np.savez_compressed(
        root / embedding_name,
        embeddings=embeddings,
        coords=coords,
        regions=regions,
    )
    checkpoint_name = f"{case_id}.pt"
    torch.manual_seed(9)
    torch.save(
        _ABMIL(n_classes=n_classes).state_dict(),
        root / checkpoint_name,
    )
    return {
        "case": case_id,
        "cohort": cohort,
        "embedding_file": embedding_name,
        "checkpoint": checkpoint_name,
        "n_classes": n_classes,
        "target_index": 0,
        "region_names": (
            ["Poor", "High"]
            if cohort == "STAD"
            else ["Tumor", "Stroma"]
        ),
        "slide_width": 40,
        "slide_height": 30,
        "tile_size_level0_px": 10,
    }


def test_clinical_model_call_and_region_test(tmp_path: Path) -> None:
    cases = {
        "cases": [
            _write_case(tmp_path, "STAD-1", "STAD", 2),
            _write_case(tmp_path, "BRCA-1", "BRCA", 1),
        ]
    }
    (tmp_path / "clinical_cases.json").write_text(json.dumps(cases))
    model = HistAgentClinical.from_data_dir(tmp_path, batch_size=5)

    predictions = model()
    assert len(predictions.tiles) == 24
    assert set(predictions.tiles["case"]) == {"STAD-1", "BRCA-1"}
    assert predictions.tiles["score"].notna().all()
    assert predictions.tiles["attention"].notna().all()

    summary = model.case_summary(predictions)
    assert "slide_score" not in summary.columns
    assert summary["tiles"].tolist() == [12, 12]

    tests = model.compare_regions(predictions)
    assert len(tests) == 2
    assert tests["Mann–Whitney P"].notna().all()


def test_score_field_preserves_bounds_aspect_ratio() -> None:
    frame = pd.DataFrame(
        {
            "x": [0, 10, 20],
            "y": [0, 10, 20],
            "score": [0.1, 0.5, 0.9],
        }
    )
    field, tissue = HistAgentClinical.score_field(
        frame,
        (0, 0, 40, 20),
        10,
        width=200,
    )
    assert field.shape == (100, 200)
    assert tissue.shape == field.shape
    assert tissue.any()


def test_survival_analysis_returns_risk_groups() -> None:
    survival = pd.DataFrame(
        {
            "task": ["TCGA-LIHC__OS"] * 6,
            "case_id": [f"P{i}" for i in range(6)],
            "time": [10, 20, 30, 40, 50, 60],
            "event": [1, 1, 0, 1, 0, 1],
            "risk": [0.9, 0.8, 0.7, 0.3, 0.2, 0.1],
        }
    )
    reported = pd.DataFrame(
        [
            {
                "cohort": "LIHC",
                "analysis": "Kaplan–Meier",
                "quantity_or_covariate": "C-index",
                "estimate": 0.72,
            },
            {
                "cohort": "LIHC",
                "analysis": "Cox",
                "quantity_or_covariate": "HistAgent risk",
                "hazard_ratio": 2.0,
                "ci_low": 1.2,
                "ci_high": 3.1,
            },
        ]
    )
    model = HistAgentClinical(
        ".",
        {"cases": []},
    )
    analysis = model.analyze_survival(survival, reported)
    assert set(analysis.patients["risk_group"]) == {
        "High risk",
        "Low risk",
    }
    assert analysis.metrics.loc[0, "C-index"] == 0.72
    assert np.isfinite(analysis.metrics.loc[0, "Log-rank P"])
