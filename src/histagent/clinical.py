from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd
import torch
from scipy.ndimage import gaussian_filter
from scipy.stats import chi2, mannwhitneyu
from torch import nn

if TYPE_CHECKING:
    from matplotlib.figure import Figure


@dataclass(frozen=True)
class ClinicalPredictions:
    """Tile-level clinical predictions returned by :class:`HistAgentClinical`."""

    tiles: pd.DataFrame


@dataclass(frozen=True)
class SurvivalAnalysis:
    """Patient-level risk groups and survival statistics."""

    patients: pd.DataFrame
    metrics: pd.DataFrame
    cox: pd.DataFrame


class _GatedAttention(nn.Module):
    def __init__(self, in_dim: int = 256, hidden: int = 256) -> None:
        super().__init__()
        self.v = nn.Linear(in_dim, hidden)
        self.u = nn.Linear(in_dim, hidden)
        self.w = nn.Linear(hidden, 1)

    def forward(self, h: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        logits = self.w(
            torch.tanh(self.v(h)) * torch.sigmoid(self.u(h))
        ).squeeze(-1)
        weights = torch.softmax(logits, dim=0)
        return torch.sum(h * weights[:, None], dim=0), weights


class _ABMIL(nn.Module):
    def __init__(self, in_dim: int = 512, n_classes: int = 1) -> None:
        super().__init__()
        self.encoder = nn.Sequential(
            nn.Linear(in_dim, 512),
            nn.LayerNorm(512),
            nn.ReLU(),
            nn.Dropout(0.25),
            nn.Linear(512, 256),
            nn.ReLU(),
        )
        self.attn = _GatedAttention()
        self.head = nn.Linear(256, n_classes)

    def forward(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        h = self.encoder(x)
        slide, attention = self.attn(h)
        return self.head(slide), attention


class HistAgentClinical:
    """Run the released HistAgent clinical-prediction tutorial models.

    The interface starts from the released HistAgent tile representations and
    trained ABMIL checkpoints. It performs tile inference, attention
    aggregation, spatial score mapping, region tests and survival analysis.
    """

    def __init__(
        self,
        data_dir: str | Path,
        case_metadata: Mapping[str, Any],
        *,
        device: str | torch.device = "cpu",
        batch_size: int = 4096,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.case_metadata = {
            row["case"]: dict(row) for row in case_metadata["cases"]
        }
        self.device = torch.device(device)
        self.batch_size = int(batch_size)
        self._tile_predictions: dict[str, pd.DataFrame] = {}
        self._slide_predictions: dict[str, float] = {}

    @classmethod
    def from_data_dir(
        cls,
        data_dir: str | Path,
        *,
        device: str | torch.device = "cpu",
        batch_size: int = 4096,
    ) -> HistAgentClinical:
        """Create a clinical model from a downloaded tutorial data directory."""

        data_dir = Path(data_dir)
        metadata = json.loads((data_dir / "clinical_cases.json").read_text())
        return cls(
            data_dir,
            metadata,
            device=device,
            batch_size=batch_size,
        )

    def __call__(
        self, case_ids: Sequence[str] | None = None
    ) -> ClinicalPredictions:
        return self.predict(case_ids)

    def _case(self, case_id: str) -> dict[str, Any]:
        if case_id not in self.case_metadata:
            raise KeyError(f"Unknown clinical case: {case_id}")
        return self.case_metadata[case_id]

    def load_case(self, case_id: str) -> dict[str, np.ndarray]:
        """Load one slide's representations, coordinates and ROI labels."""

        info = self._case(case_id)
        with np.load(self.data_dir / info["embedding_file"]) as arrays:
            return {
                "embeddings": arrays["embeddings"].astype(np.float32),
                "coords": arrays["coords"].copy(),
                "region_codes": arrays["regions"].copy(),
            }

    @staticmethod
    def _checkpoint_files(info: Mapping[str, Any]) -> list[str]:
        if "checkpoints" in info:
            return list(info["checkpoints"])
        return [str(info["checkpoint"])]

    def _load_abmil(
        self, info: Mapping[str, Any], checkpoint_file: str
    ) -> _ABMIL:
        model = _ABMIL(n_classes=int(info["n_classes"])).to(self.device)
        state = torch.load(
            self.data_dir / checkpoint_file,
            map_location=self.device,
            weights_only=True,
        )
        model.load_state_dict(state)
        return model.eval()

    def _encode_tiles(
        self, model: _ABMIL, embeddings: np.ndarray
    ) -> torch.Tensor:
        batches = []
        for start in range(0, len(embeddings), self.batch_size):
            x = torch.from_numpy(
                embeddings[start : start + self.batch_size]
            ).to(self.device)
            batches.append(model.encoder(x))
        return torch.cat(batches)

    def _forward_checkpoint(
        self,
        model: _ABMIL,
        embeddings: np.ndarray,
        info: Mapping[str, Any],
    ) -> tuple[np.ndarray, np.ndarray, float]:
        with torch.inference_mode():
            encoded = self._encode_tiles(model, embeddings)
            tile_logits = model.head(encoded)
            slide_representation, attention = model.attn(encoded)
            slide_logits = model.head(slide_representation)
            if int(info["n_classes"]) > 1:
                target_index = int(info["target_index"])
                tile_score = torch.softmax(tile_logits, dim=1)[
                    :, target_index
                ]
                slide_score = torch.softmax(slide_logits, dim=0)[target_index]
            else:
                tile_score = tile_logits[:, 0]
                slide_score = slide_logits[0]
        return (
            tile_score.cpu().numpy(),
            attention.cpu().numpy(),
            float(slide_score.cpu()),
        )

    def _predict_case(self, case_id: str) -> pd.DataFrame:
        if case_id in self._tile_predictions:
            return self._tile_predictions[case_id].copy()

        info = self._case(case_id)
        case = self.load_case(case_id)
        score_sum = np.zeros(len(case["embeddings"]), dtype=np.float64)
        attention_sum = np.zeros_like(score_sum)
        slide_scores = []
        checkpoints = self._checkpoint_files(info)

        for checkpoint_file in checkpoints:
            model = self._load_abmil(info, checkpoint_file)
            score, attention, slide_score = self._forward_checkpoint(
                model, case["embeddings"], info
            )
            score_sum += score
            attention_sum += attention
            slide_scores.append(slide_score)

        region_labels = [
            "Unannotated" if code < 0 else info["region_names"][int(code)]
            for code in case["region_codes"]
        ]
        frame = pd.DataFrame(
            {
                "cohort": info["cohort"],
                "case": case_id,
                "x": case["coords"][:, 0],
                "y": case["coords"][:, 1],
                "region_code": case["region_codes"],
                "region": region_labels,
                "score": score_sum / len(checkpoints),
                "attention": attention_sum / len(checkpoints),
            }
        )
        self._tile_predictions[case_id] = frame
        self._slide_predictions[case_id] = float(np.mean(slide_scores))
        return frame.copy()

    def predict(
        self, case_ids: Sequence[str] | None = None
    ) -> ClinicalPredictions:
        """Run tile- and slide-level ABMIL inference.

        The returned public result contains tile predictions used by the
        tutorial figures. Attention-aggregated slide scores are retained
        internally and are not included in the tutorial tables.
        """

        selected = list(case_ids) if case_ids is not None else list(
            self.case_metadata
        )
        frames = [self._predict_case(case_id) for case_id in selected]
        return ClinicalPredictions(
            tiles=pd.concat(frames, ignore_index=True)
        )

    def case_summary(
        self, predictions: ClinicalPredictions
    ) -> pd.DataFrame:
        """Summarize released cases without exposing slide-level scores."""

        rows = []
        for (cohort, case_id), frame in predictions.tiles.groupby(
            ["cohort", "case"], sort=False
        ):
            info = self._case(case_id)
            rows.append(
                {
                    "cohort": cohort,
                    "case": case_id,
                    "tiles": len(frame),
                    "annotated_tiles": int(
                        (frame["region_code"] >= 0).sum()
                    ),
                    "models": len(self._checkpoint_files(info)),
                }
            )
        return pd.DataFrame(rows)

    def compare_regions(
        self,
        predictions: ClinicalPredictions,
        region_pairs: Mapping[str, tuple[str, str]] | None = None,
    ) -> pd.DataFrame:
        """Compare tile scores between manuscript tissue regions."""

        pairs = region_pairs or {
            "STAD": ("Poor", "High"),
            "BRCA": ("Tumor", "Stroma"),
        }
        rows = []
        for (cohort, case_id), frame in predictions.tiles.groupby(
            ["cohort", "case"], sort=False
        ):
            region_1, region_2 = pairs[cohort]
            score_1 = frame.loc[
                frame["region"] == region_1, "score"
            ].to_numpy()
            score_2 = frame.loc[
                frame["region"] == region_2, "score"
            ].to_numpy()
            test = mannwhitneyu(
                score_1, score_2, alternative="greater"
            )
            rows.append(
                {
                    "cohort": cohort,
                    "case": case_id,
                    "comparison": f"{region_1} > {region_2}",
                    "n_1": len(score_1),
                    "n_2": len(score_2),
                    "Mann–Whitney P": test.pvalue,
                }
            )
        return pd.DataFrame(rows)

    @staticmethod
    def score_field(
        frame: pd.DataFrame,
        bounds: tuple[float, float, float, float],
        tile_size: float,
        *,
        width: int = 900,
        smooth: bool = False,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Map tile footprints to a field in the original WSI coordinates."""

        xmin, ymin, xmax, ymax = bounds
        height = max(1, round(width * (ymax - ymin) / (xmax - xmin)))
        weighted = np.zeros((height, width), dtype=np.float32)
        counts = np.zeros((height, width), dtype=np.float32)

        for row in frame.itertuples():
            left = int(
                np.floor((row.x - xmin) / (xmax - xmin) * width)
            )
            right = int(
                np.ceil(
                    (row.x + tile_size - xmin)
                    / (xmax - xmin)
                    * width
                )
            )
            top = int(
                np.floor((row.y - ymin) / (ymax - ymin) * height)
            )
            bottom = int(
                np.ceil(
                    (row.y + tile_size - ymin)
                    / (ymax - ymin)
                    * height
                )
            )
            left, right = max(0, left), min(width, right)
            top, bottom = max(0, top), min(height, bottom)
            if left >= right or top >= bottom:
                continue
            weighted[top:bottom, left:right] += row.score
            counts[top:bottom, left:right] += 1

        tissue = counts > 0
        field = np.divide(
            weighted,
            counts,
            out=np.zeros_like(weighted),
            where=tissue,
        )
        if smooth:
            sigma = max(
                0.8, tile_size / (xmax - xmin) * width * 0.6
            )
            smoothed_weight = gaussian_filter(
                field * tissue, sigma=sigma
            )
            smoothed_mask = gaussian_filter(
                tissue.astype(float), sigma=sigma
            )
            field = np.divide(
                smoothed_weight,
                smoothed_mask,
                out=np.zeros_like(field),
                where=smoothed_mask > 1e-5,
            )
        low, high = np.percentile(field[tissue], [2, 98])
        normalized = np.clip(
            (field - low) / (high - low + 1e-9), 0, 1
        )
        return normalized, tissue

    @staticmethod
    def _kaplan_meier(
        time: pd.Series, event: pd.Series
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        order = np.argsort(time)
        time_values = np.asarray(time)[order]
        event_values = np.asarray(event)[order].astype(int)
        event_times = np.unique(time_values[event_values == 1])
        survival = 1.0
        variance_sum = 0.0
        xs, ys, lower, upper = [0.0], [1.0], [1.0], [1.0]

        for current_time in event_times:
            at_risk = np.sum(time_values >= current_time)
            deaths = np.sum(
                (time_values == current_time) & (event_values == 1)
            )
            se_before = survival * np.sqrt(variance_sum)
            xs.extend([current_time, current_time])
            ys.extend(
                [ys[-1], survival * (1 - deaths / at_risk)]
            )
            lower.append(max(0, survival - 1.96 * se_before))
            upper.append(min(1, survival + 1.96 * se_before))
            if at_risk - deaths > 0:
                variance_sum += deaths / (
                    at_risk * (at_risk - deaths)
                )
            survival = ys[-1]
            se_after = survival * np.sqrt(variance_sum)
            lower.append(max(0, survival - 1.96 * se_after))
            upper.append(min(1, survival + 1.96 * se_after))
        return (
            np.asarray(xs),
            np.asarray(ys),
            np.asarray(lower),
            np.asarray(upper),
        )

    @staticmethod
    def _logrank_p(
        time: pd.Series, event: pd.Series, group: pd.Series
    ) -> float:
        time_values = np.asarray(time)
        event_values = np.asarray(event).astype(int)
        group_values = np.asarray(group).astype(bool)
        observed = expected = variance = 0.0

        for current_time in np.unique(
            time_values[event_values == 1]
        ):
            at_risk = time_values >= current_time
            n = at_risk.sum()
            n1 = (at_risk & group_values).sum()
            deaths = (
                (time_values == current_time) & (event_values == 1)
            ).sum()
            deaths_1 = (
                (time_values == current_time)
                & (event_values == 1)
                & group_values
            ).sum()
            if n <= 1:
                continue
            observed += deaths_1
            expected += deaths * n1 / n
            variance += (
                deaths
                * (n1 / n)
                * (1 - n1 / n)
                * ((n - deaths) / (n - 1))
            )
        if variance <= 0:
            return float("nan")
        statistic = (observed - expected) ** 2 / variance
        return float(chi2.sf(statistic, 1))

    def analyze_survival(
        self, survival: pd.DataFrame, reported: pd.DataFrame
    ) -> SurvivalAnalysis:
        """Calculate median-risk groups and log-rank statistics."""

        patients = survival.copy()
        patients["cohort"] = patients["task"].str.replace(
            "TCGA-", "", regex=False
        )
        patients["cohort"] = patients["cohort"].str.replace(
            "__OS", "", regex=False
        )
        patients["risk_group"] = patients.groupby("cohort")[
            "risk"
        ].transform(
            lambda values: np.where(
                values >= values.median(), "High risk", "Low risk"
            )
        )

        metrics = []
        for cohort, frame in patients.groupby("cohort", sort=False):
            p_value = self._logrank_p(
                frame["time"],
                frame["event"],
                frame["risk_group"].eq("High risk"),
            )
            c_index = float(
                reported.query(
                    "cohort == @cohort "
                    "and analysis == 'Kaplan–Meier' "
                    "and quantity_or_covariate == 'C-index'"
                )["estimate"].iloc[0]
            )
            metrics.append(
                {
                    "Cohort": cohort,
                    "C-index": c_index,
                    "Log-rank P": p_value,
                }
            )
        cox = reported.query("analysis == 'Cox'").copy()
        return SurvivalAnalysis(
            patients=patients,
            metrics=pd.DataFrame(metrics),
            cox=cox,
        )

    def plot_stad(
        self,
        predictions: ClinicalPredictions,
        region_tests: pd.DataFrame | None = None,
    ) -> Figure:
        from .clinical_plotting import plot_stad

        return plot_stad(self, predictions, region_tests)

    def plot_brca(
        self,
        predictions: ClinicalPredictions,
        region_tests: pd.DataFrame | None = None,
    ) -> Figure:
        from .clinical_plotting import plot_brca

        return plot_brca(self, predictions, region_tests)

    def plot_survival(
        self, analysis: SurvivalAnalysis
    ) -> Figure:
        from .clinical_plotting import plot_survival

        return plot_survival(self, analysis)

