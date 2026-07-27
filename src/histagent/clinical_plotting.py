from __future__ import annotations

from typing import TYPE_CHECKING

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
from matplotlib.cm import ScalarMappable
from matplotlib.colors import (
    LinearSegmentedColormap,
    Normalize,
    to_rgba,
)
from matplotlib.patches import Patch, Rectangle
from scipy.ndimage import maximum_filter

from .clinical import ClinicalPredictions, SurvivalAnalysis

if TYPE_CHECKING:
    from matplotlib.axes import Axes
    from matplotlib.figure import Figure

    from .clinical import HistAgentClinical


REGION_COLORS = {
    "Poor": "#D85F6B",
    "High": "#79A9D1",
    "Tumor": "#E32622",
    "Stroma": "#56A4E0",
}
SURVIVAL_COLORS = {
    "High risk": "#EF4635",
    "Low risk": "#39B8CE",
}
RISK_CMAP = LinearSegmentedColormap.from_list(
    "histagent_risk",
    ["#2178B5", "#F9BF59", "#FA681F", "#D62629", "#800080"],
)


def _slide_bounds(info: dict) -> tuple[float, float, float, float]:
    return (0, 0, info["slide_width"], info["slide_height"])


def _show_image(
    ax: Axes,
    data_dir,
    filename: str,
    bounds: tuple[float, float, float, float],
) -> None:
    xmin, ymin, xmax, ymax = bounds
    ax.imshow(
        plt.imread(data_dir / "figure4_wsi" / filename),
        extent=[xmin, xmax, ymax, ymin],
        origin="upper",
    )
    ax.set_xlim(xmin, xmax)
    ax.set_ylim(ymax, ymin)
    ax.set_aspect("equal", adjustable="box")
    ax.set_axis_off()


def _show_raster(
    ax: Axes,
    rgba: np.ndarray,
    bounds: tuple[float, float, float, float],
    *,
    interpolation: str = "nearest",
) -> None:
    xmin, ymin, xmax, ymax = bounds
    ax.imshow(
        rgba,
        extent=[xmin, xmax, ymax, ymin],
        origin="upper",
        interpolation=interpolation,
    )
    ax.set_xlim(xmin, xmax)
    ax.set_ylim(ymax, ymin)
    ax.set_aspect("equal", adjustable="box")
    ax.set_axis_off()


def _score_raster(
    model: HistAgentClinical,
    frame: pd.DataFrame,
    bounds: tuple[float, float, float, float],
    tile_size: float,
    *,
    width: int = 900,
    smooth: bool = False,
    valid_mask: np.ndarray | None = None,
    alpha: float = 1.0,
) -> np.ndarray:
    normalized, tissue = model.score_field(
        frame,
        bounds,
        tile_size,
        width=width,
        smooth=smooth,
    )
    if valid_mask is not None:
        if valid_mask.shape != tissue.shape:
            raise ValueError(
                f"Mask shape {valid_mask.shape} does not match "
                f"score field {tissue.shape}"
            )
        tissue &= valid_mask
    rgba = RISK_CMAP(normalized)
    rgba[..., 3] = np.where(tissue, alpha, 0.0)
    return rgba


def _raster_geometry(
    bounds: tuple[float, float, float, float], width: int = 900
) -> tuple[int, int]:
    xmin, ymin, xmax, ymax = bounds
    height = max(1, round(width * (ymax - ymin) / (xmax - xmin)))
    return int(width), int(height)


def _region_raster(
    frame: pd.DataFrame,
    bounds: tuple[float, float, float, float],
    *,
    width: int = 900,
) -> np.ndarray:
    xmin, ymin, xmax, ymax = bounds
    width, height = _raster_geometry(bounds, width)
    x_index = np.clip(
        (
            (frame["x"].to_numpy() - xmin)
            / (xmax - xmin)
            * (width - 1)
        ).astype(int),
        0,
        width - 1,
    )
    y_index = np.clip(
        (
            (frame["y"].to_numpy() - ymin)
            / (ymax - ymin)
            * (height - 1)
        ).astype(int),
        0,
        height - 1,
    )
    rgba = np.zeros((height, width, 4), dtype=float)
    for code, color in [
        (-1, "#D9D9D9"),
        (0, REGION_COLORS["Poor"]),
        (1, REGION_COLORS["High"]),
    ]:
        mask = np.zeros((height, width), dtype=np.uint8)
        selected = frame["region_code"].to_numpy() == code
        mask[y_index[selected], x_index[selected]] = 1
        mask = maximum_filter(mask, size=3) > 0
        rgba[mask] = to_rgba(color)
    return rgba


def _p_label(p_value: float) -> str:
    return "P<0.001" if p_value < 0.001 else f"P={p_value:.3f}"


def plot_stad(
    model: HistAgentClinical,
    predictions: ClinicalPredictions,
    region_tests: pd.DataFrame | None = None,
) -> Figure:
    tests = (
        region_tests
        if region_tests is not None
        else model.compare_regions(predictions)
    )
    stad_cases = [
        case
        for case in model.case_metadata.values()
        if case["cohort"] == "STAD"
    ]
    fig, axes = plt.subplots(
        2,
        4,
        figsize=(11.8, 5.75),
        gridspec_kw={"width_ratios": [1.08, 1.08, 1.08, 0.82]},
        constrained_layout=True,
    )
    column_titles = [
        "Whole-slide image (STAD)",
        "Region annotation",
        "HistAgent subtype score",
        "Score distribution by region",
    ]

    for row_index, info in enumerate(stad_cases):
        bounds = _slide_bounds(info)
        case_id = info["case"]
        frame = predictions.tiles.loc[
            predictions.tiles["cohort"].eq("STAD")
            & predictions.tiles["case"].eq(case_id)
        ].copy()
        annotated = frame.query("region_code >= 0").copy()

        _show_image(axes[row_index, 0], model.data_dir, info["thumbnail"], bounds)
        _show_raster(
            axes[row_index, 1],
            _region_raster(frame, bounds),
            bounds,
        )
        _show_raster(
            axes[row_index, 2],
            _score_raster(
                model,
                frame,
                bounds,
                info["tile_size_level0_px"],
            ),
            bounds,
            interpolation="bilinear",
        )

        order = ["Poor", "High"]
        point_sample = pd.concat(
            [
                part.sample(min(len(part), 48), random_state=11)
                for _, part in annotated.groupby(
                    "region", sort=False
                )
            ]
        )
        sns.boxplot(
            data=annotated,
            x="region",
            y="score",
            order=order,
            hue="region",
            palette=REGION_COLORS,
            showfliers=False,
            width=0.58,
            linewidth=1.05,
            legend=False,
            ax=axes[row_index, 3],
        )
        sns.stripplot(
            data=point_sample,
            x="region",
            y="score",
            order=order,
            hue="region",
            palette=REGION_COLORS,
            size=2.3,
            alpha=0.42,
            jitter=0.16,
            legend=False,
            ax=axes[row_index, 3],
        )
        p_value = tests.query(
            "cohort == 'STAD' and case == @case_id"
        )["Mann–Whitney P"].iloc[0]
        axes[row_index, 3].plot(
            [0, 0, 1, 1],
            [1.00, 1.025, 1.025, 1.00],
            color="black",
            linewidth=0.9,
        )
        axes[row_index, 3].text(
            0.5,
            1.035,
            _p_label(p_value),
            ha="center",
            va="bottom",
            fontsize=8.5,
        )
        axes[row_index, 3].set_ylim(-0.03, 1.085)
        axes[row_index, 3].set_xlabel("")
        axes[row_index, 3].set_ylabel(
            "HistAgent subtype score", fontsize=9
        )
        axes[row_index, 3].tick_params(
            labelsize=8.5, colors="black"
        )
        sns.despine(ax=axes[row_index, 3])

        axes[row_index, 0].text(
            -0.035,
            0.5,
            info["case"],
            transform=axes[row_index, 0].transAxes,
            rotation=90,
            va="center",
            ha="right",
            fontsize=8.5,
        )
        if row_index == 0:
            for ax, title in zip(axes[row_index], column_titles):
                ax.set_title(title, pad=6, fontsize=10.2)

    fig.legend(
        handles=[
            Patch(
                facecolor=REGION_COLORS["Poor"],
                label="Poor differentiation",
            ),
            Patch(
                facecolor=REGION_COLORS["High"],
                label="High differentiation",
            ),
            Patch(facecolor="#D9D9D9", label="Negative"),
        ],
        loc="lower left",
        bbox_to_anchor=(0.08, -0.01),
        ncol=3,
        frameon=False,
        fontsize=8.7,
    )
    colorbar = fig.colorbar(
        ScalarMappable(norm=Normalize(0, 1), cmap=RISK_CMAP),
        ax=axes[:, 2].tolist(),
        orientation="horizontal",
        shrink=0.58,
        pad=0.035,
        label="HistAgent subtype score",
        ticks=[0, 1],
    )
    colorbar.set_ticklabels(["Low", "High"])
    return fig


def plot_brca(
    model: HistAgentClinical,
    predictions: ClinicalPredictions,
    region_tests: pd.DataFrame | None = None,
) -> Figure:
    tests = (
        region_tests
        if region_tests is not None
        else model.compare_regions(predictions)
    )
    info = next(
        case
        for case in model.case_metadata.values()
        if case["cohort"] == "BRCA"
    )
    frame = predictions.tiles.query("cohort == 'BRCA'").copy()
    annotated = frame.query("region_code >= 0").copy()
    full_bounds = _slide_bounds(info)
    roi_bounds = tuple(
        info["roi_bounds"][key]
        for key in ["xmin", "ymin", "xmax", "ymax"]
    )
    roi_frame = frame.loc[
        frame["x"].between(roi_bounds[0], roi_bounds[2])
        & frame["y"].between(roi_bounds[1], roi_bounds[3])
    ].copy()

    fig = plt.figure(figsize=(11.8, 6.25), constrained_layout=True)
    grid = fig.add_gridspec(
        2,
        4,
        height_ratios=[1.04, 1],
        width_ratios=[1, 1, 1, 0.86],
    )
    axes = {
        "wsi": fig.add_subplot(grid[0, :2]),
        "wsi_score": fig.add_subplot(grid[0, 2:]),
        "roi": fig.add_subplot(grid[1, 0]),
        "roi_region": fig.add_subplot(grid[1, 1]),
        "roi_score": fig.add_subplot(grid[1, 2]),
        "distribution": fig.add_subplot(grid[1, 3]),
    }

    _show_image(axes["wsi"], model.data_dir, info["thumbnail"], full_bounds)
    axes["wsi"].set_title("Whole-slide image", pad=6, fontsize=10.2)
    axes["wsi"].add_patch(
        Rectangle(
            (roi_bounds[0], roi_bounds[1]),
            roi_bounds[2] - roi_bounds[0],
            roi_bounds[3] - roi_bounds[1],
            fill=False,
            edgecolor="#1B7C5C",
            linewidth=1.5,
        )
    )

    _show_raster(
        axes["wsi_score"],
        _score_raster(
            model,
            frame,
            full_bounds,
            info["tile_size_level0_px"],
            width=1000,
            smooth=True,
        ),
        full_bounds,
        interpolation="bilinear",
    )
    axes["wsi_score"].set_title(
        "HistAgent OS risk score", pad=6, fontsize=10.2
    )
    axes["wsi_score"].add_patch(
        Rectangle(
            (roi_bounds[0], roi_bounds[1]),
            roi_bounds[2] - roi_bounds[0],
            roi_bounds[3] - roi_bounds[1],
            fill=False,
            edgecolor="#1B7C5C",
            linewidth=1.5,
        )
    )

    wsi_dir = model.data_dir / "figure4_wsi"
    roi_image = plt.imread(wsi_dir / info["roi_image"])
    roi_annotation = plt.imread(wsi_dir / info["roi_annotation"])
    roi_valid_mask = np.any(roi_annotation[..., :3] < 0.98, axis=2)
    roi_image_masked = roi_image[..., :3].copy()
    roi_image_masked[~roi_valid_mask] = 0.96

    _show_raster(
        axes["roi"],
        roi_image_masked,
        roi_bounds,
    )
    axes["roi"].set_title("ROI", pad=6, fontsize=10.2)
    _show_image(
        axes["roi_region"],
        model.data_dir,
        info["roi_annotation"],
        roi_bounds,
    )
    axes["roi_region"].set_title(
        "ROI annotation", pad=6, fontsize=10.2
    )
    _show_raster(
        axes["roi_score"],
        _score_raster(
            model,
            roi_frame,
            roi_bounds,
            info["tile_size_level0_px"],
            width=roi_image.shape[1],
            smooth=True,
            valid_mask=roi_valid_mask,
            alpha=0.88,
        ),
        roi_bounds,
        interpolation="bilinear",
    )
    axes["roi_score"].set_title(
        "ROI risk score", pad=6, fontsize=10.2
    )

    score_low, score_high = np.percentile(
        annotated["score"], [2, 98]
    )
    annotated["display_score"] = np.clip(
        (annotated["score"] - score_low)
        / (score_high - score_low + 1e-9),
        0,
        1,
    )
    point_sample = pd.concat(
        [
            part.sample(min(len(part), 48), random_state=8)
            for _, part in annotated.groupby("region", sort=False)
        ]
    )
    distribution_ax = axes["distribution"]
    sns.boxplot(
        data=annotated,
        x="region",
        y="display_score",
        order=["Tumor", "Stroma"],
        hue="region",
        palette=REGION_COLORS,
        showfliers=False,
        width=0.58,
        linewidth=1.05,
        legend=False,
        ax=distribution_ax,
    )
    sns.stripplot(
        data=point_sample,
        x="region",
        y="display_score",
        order=["Tumor", "Stroma"],
        hue="region",
        palette=REGION_COLORS,
        size=2.5,
        alpha=0.45,
        jitter=0.16,
        legend=False,
        ax=distribution_ax,
    )
    p_value = tests.query("cohort == 'BRCA'")[
        "Mann–Whitney P"
    ].iloc[0]
    distribution_ax.set_title(
        "Score distribution by region", pad=6, fontsize=10.2
    )
    distribution_ax.set_xlabel("")
    distribution_ax.set_ylabel("HistAgent OS risk score")
    distribution_ax.plot(
        [0, 0, 1, 1],
        [1.00, 1.025, 1.025, 1.00],
        color="black",
        linewidth=0.9,
    )
    distribution_ax.text(
        0.5,
        1.035,
        _p_label(p_value),
        ha="center",
        va="bottom",
        fontsize=8.5,
    )
    distribution_ax.set_ylim(-0.03, 1.085)
    distribution_ax.tick_params(labelsize=8.5, colors="black")
    sns.despine(ax=distribution_ax)

    fig.legend(
        handles=[
            Patch(facecolor=REGION_COLORS["Tumor"], label="Tumor"),
            Patch(facecolor=REGION_COLORS["Stroma"], label="Stroma"),
        ],
        loc="lower left",
        bbox_to_anchor=(0.08, -0.008),
        ncol=2,
        frameon=False,
        fontsize=8.7,
    )
    colorbar = fig.colorbar(
        ScalarMappable(norm=Normalize(0, 1), cmap=RISK_CMAP),
        ax=[axes["wsi_score"], axes["roi_score"]],
        location="bottom",
        shrink=0.58,
        pad=0.04,
        label="HistAgent OS risk score",
        ticks=[0, 1],
    )
    colorbar.set_ticklabels(["Low", "High"])
    return fig


def plot_survival(
    model: HistAgentClinical, analysis: SurvivalAnalysis
) -> Figure:
    cohort_order = ["LIHC", "LGG"]
    time_ticks = {
        "LIHC": np.array([0, 25, 50, 75, 100]),
        "LGG": np.array([0, 50, 100, 150, 200]),
    }
    fig = plt.figure(figsize=(12.2, 5.55), facecolor="white")
    outer = fig.add_gridspec(
        1,
        2,
        left=0.075,
        right=0.925,
        top=0.94,
        bottom=0.10,
        wspace=0.42,
    )

    for column_index, cohort in enumerate(cohort_order):
        frame = analysis.patients.query("cohort == @cohort").copy()
        subgrid = outer[column_index].subgridspec(
            3,
            1,
            height_ratios=[3.35, 0.66, 1.18],
            hspace=0.08,
        )
        ax = fig.add_subplot(subgrid[0])
        risk_ax = fig.add_subplot(subgrid[1], sharex=ax)
        forest_ax = fig.add_subplot(subgrid[2])

        for group in ["High risk", "Low risk"]:
            color = SURVIVAL_COLORS[group]
            part = frame.query("risk_group == @group")
            x, y, low, high = model._kaplan_meier(
                part["time"], part["event"]
            )
            ax.fill_between(
                x,
                low,
                high,
                step="post",
                color=color,
                alpha=0.16,
                linewidth=0,
            )
            ax.step(
                x,
                y,
                where="post",
                label=group,
                color=color,
                linewidth=2.2,
            )

        metric = analysis.metrics.query("Cohort == @cohort").iloc[0]
        p_value = float(metric["Log-rank P"])
        c_index = float(metric["C-index"])
        ax.set_title(f"TCGA-{cohort} OS")
        ax.set_ylabel(
            "Overall survival probability" if column_index == 0 else ""
        )
        ax.set_ylim(0, 1.02)
        ax.set_xlim(0, time_ticks[cohort][-1])
        ax.set_xticks(time_ticks[cohort])
        p_label = (
            "P < 0.001" if p_value < 0.001 else f"P = {p_value:.3f}"
        )
        ax.text(
            0.04,
            0.08,
            f"{p_label}\nC-index = {c_index:.3f}",
            transform=ax.transAxes,
        )
        ax.legend(frameon=False, loc="upper right")
        sns.despine(ax=ax)

        risk_ax.set_ylim(0, 1)
        risk_ax.set_axis_off()
        risk_ax.text(
            -0.02,
            0.73,
            "No. at risk",
            transform=risk_ax.transAxes,
            ha="right",
            va="center",
            fontsize=8.5,
        )
        for group, y_position in [
            ("High risk", 0.43),
            ("Low risk", 0.09),
        ]:
            color = SURVIVAL_COLORS[group]
            group_mask = frame["risk_group"].eq(group).to_numpy()
            risk_ax.text(
                -0.06,
                y_position,
                group,
                transform=risk_ax.transAxes,
                ha="right",
                va="center",
                fontsize=8.3,
                color=color,
            )
            for tick in time_ticks[cohort]:
                count = int(
                    (
                        (frame["time"].to_numpy() >= tick)
                        & group_mask
                    ).sum()
                )
                risk_ax.text(
                    tick,
                    y_position,
                    str(count),
                    ha="center",
                    va="center",
                    fontsize=8.3,
                    color=color,
                )
        risk_ax.axhline(0.66, color="#D0D0D0", linewidth=0.8)

        cohort_forest = analysis.cox.query(
            "cohort == @cohort"
        ).reset_index(drop=True)
        forest_ax.axvline(
            1, color="#888888", linestyle="--", linewidth=0.9
        )
        for y_position, row in zip(
            [1, 0], cohort_forest.itertuples()
        ):
            is_histagent = str(row.quantity_or_covariate).startswith(
                "HistAgent"
            )
            color = "#2F67B1" if is_histagent else "#626B73"
            forest_ax.errorbar(
                row.hazard_ratio,
                y_position,
                xerr=[
                    [row.hazard_ratio - row.ci_low],
                    [row.ci_high - row.hazard_ratio],
                ],
                fmt="o",
                color=color,
                ecolor=color,
                capsize=2.5,
                markersize=5.5,
                linewidth=1.5,
            )
            label = (
                "HistAgent risk"
                if is_histagent
                else str(row.quantity_or_covariate)
            )
            forest_ax.text(
                0.47,
                y_position,
                label,
                ha="left",
                va="center",
                fontsize=8.2,
            )
            forest_ax.text(
                1.02,
                y_position,
                (
                    f"{row.hazard_ratio:.2f} "
                    f"[{row.ci_low:.2f}–{row.ci_high:.2f}]"
                ),
                transform=forest_ax.get_yaxis_transform(),
                ha="left",
                va="center",
                fontsize=7.6,
                color=color,
            )
        forest_ax.set_xscale("log")
        forest_ax.set_xlim(0.45, 4.1)
        forest_ax.set_xticks(
            [0.5, 1, 2, 4], labels=["0.5", "1", "2", "4"]
        )
        forest_ax.set_ylim(-0.65, 1.65)
        forest_ax.set_yticks([])
        forest_ax.set_xlabel("Hazard ratio")
        forest_ax.minorticks_off()
        sns.despine(ax=forest_ax, left=True)
    return fig
