# viz/chart_renderer.py — Nature 期刊风格统计图表（≥20 种）
"""
出图规范对齐 Nature Final guide to authors + Okabe–Ito 色盲安全色板。
详见 nature_style.py。
"""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from viz.nature_style import (
    CMAP_DIVERGING,
    CMAP_MISSING,
    CMAP_SEQUENTIAL,
    FIGSIZE_1_5COL,
    FIGSIZE_HEAT,
    FIGSIZE_SINGLE,
    FIGSIZE_SINGLE_SQUARE,
    JOURNAL_COLORS,
    NATURE_MUTED,
    VOLCANO_NS,
    VOLCANO_SIG,
    apply_nature_style,
    color_at,
    polish_axes,
)

_LOG = logging.getLogger(__name__)


def _import_plt():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    apply_nature_style(plt)
    return plt


def _save_fig(
    fig,
    out_path: Path,
    title: str,
    chart_type: str = "",
    *,
    columns: Optional[List[str]] = None,
    x: Optional[str] = None,
    y: Optional[str] = None,
    hue: Optional[str] = None,
    facts: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, bbox_inches="tight", facecolor="white", dpi=300)
    import matplotlib.pyplot as plt

    plt.close(fig)
    b64 = base64.b64encode(out_path.read_bytes()).decode("ascii")
    kind = chart_type or (out_path.stem.split("_")[0] if "_" in out_path.stem else "chart")
    out: Dict[str, Any] = {
        "chart_type": kind,
        "title": title,
        "path": str(out_path),
        "filename": out_path.name,
        "base64": b64,
        "style": "nature",
    }
    if columns:
        out["columns"] = [str(c) for c in columns if c]
    if x:
        out["x"] = str(x)
    if y:
        out["y"] = str(y)
    if hue:
        out["hue"] = str(hue)
    if facts:
        out["facts"] = facts
    return out


def _series_dist_facts(series: pd.Series, bins: int = 20) -> Dict[str, Any]:
    """Facts aligned with a histogram/KDE of ``series``."""
    s = _numeric(series)
    name = str(series.name or "value")
    if s.empty:
        return {"variable": name, "n": 0}
    n_bins = int(min(max(bins, 5), max(5, len(s))))
    counts, edges = np.histogram(s.values, bins=n_bins)
    cv = float(np.std(counts) / (np.mean(counts) + 1e-9)) if len(counts) else 0.0
    skew = float(s.skew()) if len(s) >= 8 else 0.0
    if cv < 0.25:
        shape = "近似均匀（各区间计数接近）"
    elif abs(skew) < 0.35:
        shape = "大致对称"
    elif skew > 0:
        shape = "右偏"
    else:
        shape = "左偏"
    peak_i = int(np.argmax(counts)) if len(counts) else 0
    return {
        "variable": name,
        "n": int(len(s)),
        "mean": round(float(s.mean()), 6),
        "median": round(float(s.median()), 6),
        "std": round(float(s.std()), 6),
        "min": round(float(s.min()), 6),
        "max": round(float(s.max()), 6),
        "skewness": round(skew, 4),
        "bins": n_bins,
        "bin_count_min": int(counts.min()) if len(counts) else 0,
        "bin_count_max": int(counts.max()) if len(counts) else 0,
        "peak_bin": [round(float(edges[peak_i]), 6), round(float(edges[peak_i + 1]), 6)] if len(edges) > peak_i + 1 else [],
        "shape": shape,
    }


def _hist_csv_facts(col: str, sub: pd.DataFrame) -> Dict[str, Any]:
    """Facts from the exact histogram bins drawn on the figure."""
    counts = pd.to_numeric(sub["count"], errors="coerce").fillna(0)
    cv = float(counts.std() / (counts.mean() + 1e-9)) if len(counts) else 0.0
    if cv < 0.25:
        shape = "近似均匀（各区间计数接近）"
    elif cv < 0.6:
        shape = "中等起伏"
    else:
        shape = "峰谷明显（非均匀）"
    peak = sub.loc[counts.idxmax()] if len(counts) else None
    facts: Dict[str, Any] = {
        "variable": str(col),
        "n_bins": int(len(sub)),
        "total_count": int(counts.sum()),
        "bin_count_min": int(counts.min()) if len(counts) else 0,
        "bin_count_max": int(counts.max()) if len(counts) else 0,
        "bin_count_cv": round(cv, 4),
        "shape": shape,
        "source": "histogram_bins",
    }
    if peak is not None:
        facts["peak_bin_left"] = float(peak.get("bin_left", 0))
        if "bin_right" in sub.columns:
            facts["peak_bin_right"] = float(peak.get("bin_right", 0))
    return facts


def _numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").dropna()


# 内部/标识列：不应作为分布图或相关分析的主变量
_SKIP_COL_EXACT = {
    "__row_id__", "row_id", "rowid", "index", "unnamed: 0",
    "sample_id", "patient_id", "subject_id", "id",
}
_SKIP_COL_SUFFIXES = ("_id", "_idx", "_index", "_uid", "_uuid")


def is_skip_chart_column(name: Any, series: Optional[pd.Series] = None) -> bool:
    """True = 不宜单独出分布/相关图（行号、主键、近乎唯一标识等）。"""
    col = str(name or "").strip()
    if not col:
        return True
    low = col.lower()
    if low in _SKIP_COL_EXACT or (low.startswith("__") and low.endswith("__")):
        return True
    if low.startswith("unnamed"):
        return True
    if any(low.endswith(suf) for suf in _SKIP_COL_SUFFIXES):
        # gene_id / probe_id：几乎每行唯一则跳过；仅有列名时按标识列跳过
        if series is None:
            return True
        if len(series) > 20:
            nuniq = series.nunique(dropna=True)
            if nuniq >= max(20, int(0.9 * len(series))):
                return True
    if series is not None and len(series) > 50:
        # 单调递增整数序列（典型行号）
        num = pd.to_numeric(series, errors="coerce")
        if num.notna().mean() > 0.95:
            vals = num.dropna()
            if len(vals) > 20:
                diffs = vals.diff().dropna()
                if (diffs > 0).mean() > 0.98 and vals.nunique() >= int(0.9 * len(vals)):
                    return True
    return False


def filter_chartable_numeric_cols(df: pd.DataFrame, cols: Optional[List[str]] = None) -> List[str]:
    num = df.select_dtypes(include=[np.number])
    names = list(cols) if cols else list(num.columns)
    out: List[str] = []
    for c in names:
        if c not in df.columns:
            continue
        if is_skip_chart_column(c, df[c]):
            continue
        if c not in num.columns and not pd.api.types.is_numeric_dtype(df[c]):
            continue
        out.append(c)
    return out


class ChartRenderer:
    """从 DataFrame 或算子 artifact CSV 生成 Nature 规范图表。"""

    SUPPORTED = [
        "bar", "grouped_bar", "stacked_bar", "violin", "box", "strip",
        "histogram", "kde", "scatter", "line", "heatmap", "correlation_heatmap",
        "volcano", "missing_heatmap", "qq", "residual", "pca_scatter",
        "forest", "pie", "dot", "ridge", "km_curve",
    ]

    def __init__(self, output_dir: Path):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._counter = 0

    def _next_name(self, kind: str) -> Path:
        self._counter += 1
        return self.output_dir / f"{kind}_{self._counter:02d}.png"

    # ── 基础图 ──────────────────────────────────────────────

    def render_bar(self, df: pd.DataFrame, x: str, y: str, title: str = "Bar chart") -> Dict[str, Any]:
        plt = _import_plt()
        fig, ax = plt.subplots(figsize=FIGSIZE_SINGLE)
        agg = df.groupby(x, observed=True)[y].agg(["mean", "sem"])
        agg = agg.sort_values("mean", ascending=False)
        colors = [color_at(i, NATURE_MUTED) for i in range(len(agg))]
        ax.bar(range(len(agg)), agg["mean"], yerr=agg["sem"], color=colors,
               edgecolor="white", linewidth=0.4, capsize=2, error_kw={"lw": 0.6})
        ax.set_xticks(range(len(agg)))
        ax.set_xticklabels([str(i) for i in agg.index], rotation=45, ha="right")
        ax.set_xlabel(x)
        ax.set_ylabel(f"Mean {y}")
        ax.set_title(title)
        polish_axes(ax)
        top = [
            {"group": str(idx), "mean": round(float(row["mean"]), 6)}
            for idx, row in agg.head(5).iterrows()
        ]
        facts = {
            "x": x, "y": y, "n_groups": int(len(agg)),
            "top_groups_by_mean": top,
            "highest": top[0] if top else None,
            "lowest": {"group": str(agg.index[-1]), "mean": round(float(agg["mean"].iloc[-1]), 6)} if len(agg) else None,
        }
        return _save_fig(fig, self._next_name("bar"), title, "bar",
                         columns=[x, y], x=x, y=y, facts=facts)

    def render_grouped_bar(self, df: pd.DataFrame, x: str, y: str, hue: str,
                           title: str = "Grouped bar") -> Dict[str, Any]:
        plt = _import_plt()
        fig, ax = plt.subplots(figsize=FIGSIZE_1_5COL)
        pivot = df.pivot_table(index=x, columns=hue, values=y, aggfunc="mean")
        n_groups, n_hue = pivot.shape
        width = 0.8 / max(n_hue, 1)
        x_idx = np.arange(n_groups)
        for i, col in enumerate(pivot.columns):
            ax.bar(x_idx + i * width, pivot[col].values, width=width,
                   label=str(col), color=color_at(i), edgecolor="white", linewidth=0.3)
        ax.set_xticks(x_idx + width * (n_hue - 1) / 2)
        ax.set_xticklabels([str(i) for i in pivot.index], rotation=45, ha="right")
        ax.set_ylabel(f"Mean {y}")
        ax.set_title(title)
        ax.legend(loc="best")
        polish_axes(ax)
        return _save_fig(fig, self._next_name("grouped_bar"), title, "grouped_bar")

    def render_stacked_bar(self, df: pd.DataFrame, x: str, y: str, hue: str,
                           title: str = "Stacked bar") -> Dict[str, Any]:
        plt = _import_plt()
        fig, ax = plt.subplots(figsize=FIGSIZE_1_5COL)
        pivot = df.pivot_table(index=x, columns=hue, values=y, aggfunc="sum", fill_value=0)
        bottom = np.zeros(len(pivot))
        x_idx = np.arange(len(pivot))
        for i, col in enumerate(pivot.columns):
            ax.bar(x_idx, pivot[col].values, bottom=bottom, label=str(col),
                   color=color_at(i), edgecolor="white", linewidth=0.3)
            bottom = bottom + pivot[col].values
        ax.set_xticks(x_idx)
        ax.set_xticklabels([str(i) for i in pivot.index], rotation=45, ha="right")
        ax.set_title(title)
        ax.legend(loc="best")
        polish_axes(ax)
        return _save_fig(fig, self._next_name("stacked_bar"), title, "stacked_bar")

    def render_violin(self, df: pd.DataFrame, x: str, y: str, title: str = "Violin plot") -> Dict[str, Any]:
        plt = _import_plt()
        fig, ax = plt.subplots(figsize=FIGSIZE_SINGLE)
        groups = [g.dropna().values for _, g in df.groupby(x, observed=True)[y]]
        labels = [str(k) for k in df.groupby(x, observed=True).groups.keys()]
        parts = ax.violinplot(groups, showmeans=False, showmedians=True, showextrema=True)
        for i, pc in enumerate(parts["bodies"]):
            pc.set_facecolor(color_at(i))
            pc.set_edgecolor("#222222")
            pc.set_alpha(0.75)
            pc.set_linewidth(0.4)
        for key in ("cbars", "cmins", "cmaxes", "cmedians"):
            if key in parts:
                parts[key].set_color("#222222")
                parts[key].set_linewidth(0.6)
        ax.set_xticks(range(1, len(labels) + 1))
        ax.set_xticklabels(labels, rotation=45, ha="right")
        ax.set_ylabel(y)
        ax.set_title(title)
        polish_axes(ax)
        gstats = []
        for lab, g in zip(labels, groups):
            s = pd.Series(g)
            if s.empty:
                continue
            gstats.append({
                "group": lab, "n": int(len(s)),
                "median": round(float(s.median()), 6),
                "mean": round(float(s.mean()), 6),
            })
        facts = {"x": x, "y": y, "groups": gstats}
        return _save_fig(fig, self._next_name("violin"), title, "violin",
                         columns=[x, y], x=x, y=y, facts=facts)

    def render_box(self, df: pd.DataFrame, x: str, y: str, title: str = "Box plot") -> Dict[str, Any]:
        plt = _import_plt()
        fig, ax = plt.subplots(figsize=FIGSIZE_SINGLE)
        groups = [g.dropna().values for _, g in df.groupby(x, observed=True)[y]]
        labels = [str(k) for k in df.groupby(x, observed=True).groups.keys()]
        bp = ax.boxplot(groups, patch_artist=True, widths=0.55,
                        medianprops={"color": "#222222", "linewidth": 0.8},
                        whiskerprops={"linewidth": 0.6},
                        capprops={"linewidth": 0.6},
                        flierprops={"marker": "o", "markersize": 2, "alpha": 0.5})
        for i, patch in enumerate(bp["boxes"]):
            patch.set_facecolor(color_at(i))
            patch.set_alpha(0.65)
            patch.set_edgecolor("#222222")
            patch.set_linewidth(0.5)
        ax.set_xticklabels(labels, rotation=45, ha="right")
        ax.set_ylabel(y)
        ax.set_title(title)
        polish_axes(ax)
        gstats = []
        for lab, g in zip(labels, groups):
            s = pd.Series(g)
            if s.empty:
                continue
            gstats.append({
                "group": lab, "n": int(len(s)),
                "median": round(float(s.median()), 6),
                "q1": round(float(s.quantile(0.25)), 6),
                "q3": round(float(s.quantile(0.75)), 6),
            })
        facts = {"x": x, "y": y, "groups": gstats}
        return _save_fig(fig, self._next_name("box"), title, "box",
                         columns=[x, y], x=x, y=y, facts=facts)

    def render_strip(self, df: pd.DataFrame, x: str, y: str, title: str = "Strip plot") -> Dict[str, Any]:
        plt = _import_plt()
        fig, ax = plt.subplots(figsize=FIGSIZE_SINGLE)
        labels = []
        for i, (name, grp) in enumerate(df.groupby(x, observed=True)):
            vals = _numeric(grp[y]).values
            jitter = np.random.default_rng(42 + i).uniform(-0.18, 0.18, size=len(vals))
            ax.scatter(np.full(len(vals), i + 1) + jitter, vals,
                       s=8, alpha=0.55, color=color_at(i), edgecolors="none")
            labels.append(str(name))
        ax.set_xticks(range(1, len(labels) + 1))
        ax.set_xticklabels(labels, rotation=45, ha="right")
        ax.set_ylabel(y)
        ax.set_title(title)
        polish_axes(ax)
        return _save_fig(fig, self._next_name("strip"), title, "strip")

    def render_histogram(self, series: pd.Series, title: str = "Histogram", bins: int = 20) -> Dict[str, Any]:
        plt = _import_plt()
        fig, ax = plt.subplots(figsize=FIGSIZE_SINGLE)
        s = _numeric(series)
        ax.hist(s, bins=bins, color=color_at(0), edgecolor="white", linewidth=0.4, alpha=0.9)
        ax.set_xlabel(series.name or "value")
        ax.set_ylabel("Count")
        ax.set_title(title)
        polish_axes(ax)
        col = str(series.name or "value")
        return _save_fig(
            fig, self._next_name("histogram"), title, "histogram",
            columns=[col], x=col, facts=_series_dist_facts(series, bins=bins),
        )

    def render_kde(self, series: pd.Series, title: str = "Density (KDE)") -> Dict[str, Any]:
        plt = _import_plt()
        from scipy.stats import gaussian_kde

        s = _numeric(series)
        if len(s) < 5:
            return {}
        fig, ax = plt.subplots(figsize=FIGSIZE_SINGLE)
        xs = np.linspace(float(s.min()), float(s.max()), 200)
        kde = gaussian_kde(s.values)
        ax.fill_between(xs, kde(xs), color=color_at(0), alpha=0.35)
        ax.plot(xs, kde(xs), color=color_at(0), lw=1.2)
        ax.set_xlabel(series.name or "value")
        ax.set_ylabel("Density")
        ax.set_title(title)
        polish_axes(ax)
        col = str(series.name or "value")
        return _save_fig(
            fig, self._next_name("kde"), title, "kde",
            columns=[col], x=col, facts=_series_dist_facts(series),
        )

    def render_scatter(self, df: pd.DataFrame, x: str, y: str, hue: Optional[str] = None,
                       title: str = "Scatter plot") -> Dict[str, Any]:
        plt = _import_plt()
        fig, ax = plt.subplots(figsize=FIGSIZE_SINGLE_SQUARE)
        if hue and hue in df.columns:
            for i, (name, grp) in enumerate(df.groupby(hue, observed=True)):
                ax.scatter(grp[x], grp[y], label=str(name),
                           color=color_at(i), alpha=0.7, s=14, edgecolors="none")
            ax.legend(loc="best", markerscale=1.2)
        else:
            ax.scatter(df[x], df[y], color=color_at(0), alpha=0.7, s=14, edgecolors="none")
        ax.set_xlabel(x)
        ax.set_ylabel(y)
        ax.set_title(title)
        polish_axes(ax)
        xs = _numeric(df[x])
        ys = _numeric(df[y])
        n = int(min(len(xs), len(ys)))
        r = None
        if n >= 3:
            aligned = pd.DataFrame({"x": df[x], "y": df[y]}).apply(pd.to_numeric, errors="coerce").dropna()
            n = int(len(aligned))
            if n >= 3:
                r = round(float(aligned["x"].corr(aligned["y"], method="pearson")), 4)
        facts = {"x": x, "y": y, "n": n, "pearson_r": r, "hue": hue}
        cols = [x, y] + ([hue] if hue else [])
        return _save_fig(fig, self._next_name("scatter"), title, "scatter",
                         columns=cols, x=x, y=y, hue=hue, facts=facts)

    def render_line(self, df: pd.DataFrame, x: str, y: str, hue: Optional[str] = None,
                    title: str = "Line plot") -> Dict[str, Any]:
        plt = _import_plt()
        fig, ax = plt.subplots(figsize=FIGSIZE_1_5COL)
        if hue and hue in df.columns:
            for i, (name, grp) in enumerate(df.groupby(hue, observed=True)):
                g = grp.sort_values(x)
                ax.plot(g[x], g[y], label=str(name), color=color_at(i), marker="o",
                        markersize=2.5, linewidth=1.0)
            ax.legend(loc="best")
        else:
            g = df.sort_values(x)
            ax.plot(g[x], g[y], color=color_at(0), marker="o", markersize=2.5, linewidth=1.0)
        ax.set_xlabel(x)
        ax.set_ylabel(y)
        ax.set_title(title)
        polish_axes(ax)
        return _save_fig(fig, self._next_name("line"), title, "line")

    def render_heatmap(self, matrix: pd.DataFrame, title: str = "Heatmap") -> Dict[str, Any]:
        plt = _import_plt()
        fig, ax = plt.subplots(figsize=FIGSIZE_HEAT)
        data = matrix.values.astype(float)
        vmax = float(np.nanmax(np.abs(data))) if np.isfinite(data).any() else 1.0
        vmax = vmax if vmax > 0 else 1.0
        im = ax.imshow(data, cmap=CMAP_DIVERGING, aspect="auto", vmin=-vmax, vmax=vmax)
        ax.set_xticks(range(len(matrix.columns)))
        ax.set_yticks(range(len(matrix.index)))
        ax.set_xticklabels(matrix.columns, rotation=45, ha="right")
        ax.set_yticklabels(matrix.index)
        ax.set_title(title)
        cbar = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
        cbar.ax.tick_params(labelsize=6, width=0.5, length=2)
        polish_axes(ax)
        # Top |value| off-diagonal pairs when this is a square correlation-like matrix
        top_pairs = []
        try:
            mat = matrix.astype(float)
            names = [str(c) for c in mat.columns]
            for i, a in enumerate(names):
                for j, b in enumerate(names):
                    if j <= i:
                        continue
                    v = mat.iloc[i, j]
                    if pd.isna(v):
                        continue
                    top_pairs.append({"a": a, "b": b, "value": round(float(v), 4)})
            top_pairs.sort(key=lambda d: abs(d["value"]), reverse=True)
            top_pairs = top_pairs[:8]
        except Exception:
            top_pairs = []
        facts = {
            "n_rows": int(matrix.shape[0]),
            "n_cols": int(matrix.shape[1]),
            "variables": [str(c) for c in list(matrix.columns)[:20]],
            "top_pairs_by_abs": top_pairs,
        }
        return _save_fig(
            fig, self._next_name("heatmap"), title, "heatmap",
            columns=[str(c) for c in list(matrix.columns)[:20]], facts=facts,
        )

    def render_correlation_heatmap(self, df: pd.DataFrame, cols: Optional[List[str]] = None,
                                   title: str = "Correlation heatmap") -> Dict[str, Any]:
        keep = filter_chartable_numeric_cols(df, cols)
        if len(keep) < 2:
            return {}
        num = df[keep].apply(pd.to_numeric, errors="coerce")
        return self.render_heatmap(num.corr(method="pearson"), title)

    def render_volcano(self, deg_df: pd.DataFrame,
                       logfc_col: str = "logFC", p_col: str = "P.Value",
                       title: str = "Volcano plot") -> Dict[str, Any]:
        plt = _import_plt()
        df = deg_df.copy()
        if logfc_col not in df.columns or p_col not in df.columns:
            for alt_l, alt_p in [("log2FoldChange", "padj"), ("logfc", "pvalue"), ("logFC", "adj.P.Val")]:
                if alt_l in df.columns and alt_p in df.columns:
                    logfc_col, p_col = alt_l, alt_p
                    break
        if logfc_col not in df.columns or p_col not in df.columns:
            return {}
        df["_mlogp"] = -np.log10(pd.to_numeric(df[p_col], errors="coerce").clip(lower=1e-300))
        df["_lfc"] = pd.to_numeric(df[logfc_col], errors="coerce")
        fig, ax = plt.subplots(figsize=FIGSIZE_SINGLE_SQUARE)
        sig = (df["_mlogp"] > -np.log10(0.05)) & (df["_lfc"].abs() > 1)
        ax.scatter(df.loc[~sig, "_lfc"], df.loc[~sig, "_mlogp"], c=VOLCANO_NS, s=8, alpha=0.45, edgecolors="none")
        ax.scatter(df.loc[sig, "_lfc"], df.loc[sig, "_mlogp"], c=VOLCANO_SIG, s=10, alpha=0.85, edgecolors="none")
        ax.axhline(-np.log10(0.05), ls="--", color="#666666", lw=0.6)
        ax.axvline(-1, ls="--", color="#666666", lw=0.6)
        ax.axvline(1, ls="--", color="#666666", lw=0.6)
        ax.set_xlabel("log$_2$ fold change")
        ax.set_ylabel("$-$log$_{10}$($P$)")
        ax.set_title(title)
        polish_axes(ax)
        n_up = int(((sig) & (df["_lfc"] > 0)).sum())
        n_down = int(((sig) & (df["_lfc"] < 0)).sum())
        facts = {
            "logfc_col": logfc_col,
            "p_col": p_col,
            "n_total": int(len(df)),
            "threshold": {"abs_logfc": 1.0, "p": 0.05},
            "n_significant": int(sig.sum()),
            "n_up": n_up,
            "n_down": n_down,
        }
        return _save_fig(
            fig, self._next_name("volcano"), title, "volcano",
            columns=[logfc_col, p_col], x=logfc_col, y=p_col, facts=facts,
        )

    def render_missing_heatmap(self, df: pd.DataFrame, title: str = "Missing value pattern") -> Dict[str, Any]:
        plt = _import_plt()
        miss = df.isnull().astype(int)
        if miss.sum().sum() == 0:
            return {}
        fig, ax = plt.subplots(figsize=(FIGSIZE_1_5COL[0], max(2.2, min(5.5, len(df) / 80))))
        ax.imshow(miss.values[:200], aspect="auto", cmap=CMAP_MISSING, interpolation="nearest")
        ax.set_yticks([])
        ax.set_xticks(range(len(df.columns)))
        ax.set_xticklabels(df.columns, rotation=90)
        ax.set_title(title)
        polish_axes(ax)
        return _save_fig(fig, self._next_name("missing_heatmap"), title, "missing_heatmap")

    def render_qq(self, series: pd.Series, title: str = "Q–Q plot") -> Dict[str, Any]:
        plt = _import_plt()
        from scipy import stats

        s = _numeric(series)
        if len(s) < 5:
            return {}
        fig, ax = plt.subplots(figsize=FIGSIZE_SINGLE_SQUARE)
        stats.probplot(s, dist="norm", plot=ax)
        ax.set_title(title)
        ax.get_lines()[0].set_markerfacecolor(color_at(0))
        ax.get_lines()[0].set_markeredgecolor(color_at(0))
        ax.get_lines()[0].set_markersize(3)
        ax.get_lines()[0].set_alpha(0.7)
        ax.get_lines()[1].set_color(color_at(5))
        ax.get_lines()[1].set_linewidth(1.0)
        polish_axes(ax)
        return _save_fig(fig, self._next_name("qq"), title, "qq")

    def render_residual(self, y_true: pd.Series, y_pred: pd.Series,
                        title: str = "Residual plot") -> Dict[str, Any]:
        plt = _import_plt()
        yt = _numeric(y_true)
        yp = _numeric(y_pred)
        n = min(len(yt), len(yp))
        if n < 5:
            return {}
        resid = yt.values[:n] - yp.values[:n]
        fig, ax = plt.subplots(figsize=FIGSIZE_SINGLE)
        ax.scatter(yp.values[:n], resid, s=12, alpha=0.65, color=color_at(0), edgecolors="none")
        ax.axhline(0, color="#666666", lw=0.7, ls="--")
        ax.set_xlabel("Fitted")
        ax.set_ylabel("Residual")
        ax.set_title(title)
        polish_axes(ax)
        return _save_fig(fig, self._next_name("residual"), title, "residual")

    def render_pca_scatter(self, df: pd.DataFrame, cols: Optional[List[str]] = None,
                           title: str = "PCA scatter") -> Dict[str, Any]:
        from sklearn.decomposition import PCA
        from sklearn.preprocessing import StandardScaler

        num = df.select_dtypes(include=[np.number])
        if cols:
            num = num[[c for c in cols if c in num.columns]]
        num = num.dropna(axis=1, how="all").dropna()
        if num.shape[1] < 2 or len(num) < 3:
            return {}
        X = StandardScaler().fit_transform(num)
        pca = PCA(n_components=2)
        pcs = pca.fit_transform(X)
        plt = _import_plt()
        fig, ax = plt.subplots(figsize=FIGSIZE_SINGLE_SQUARE)
        ax.scatter(pcs[:, 0], pcs[:, 1], c=color_at(2), alpha=0.7, s=16, edgecolors="none")
        ax.set_xlabel(f"PC1 ({pca.explained_variance_ratio_[0]*100:.1f}%)")
        ax.set_ylabel(f"PC2 ({pca.explained_variance_ratio_[1]*100:.1f}%)")
        ax.set_title(title)
        polish_axes(ax)
        return _save_fig(fig, self._next_name("pca_scatter"), title, "pca_scatter")

    def render_pie(self, series: pd.Series, title: str = "Category distribution") -> Dict[str, Any]:
        plt = _import_plt()
        counts = series.value_counts().head(8)
        fig, ax = plt.subplots(figsize=FIGSIZE_SINGLE_SQUARE)
        colors = [color_at(i, NATURE_MUTED) for i in range(len(counts))]
        wedges, texts, autotexts = ax.pie(
            counts.values, labels=[str(x) for x in counts.index], autopct="%1.0f%%",
            colors=colors, startangle=90, wedgeprops={"linewidth": 0.5, "edgecolor": "white"},
            textprops={"fontsize": 6},
        )
        for t in autotexts:
            t.set_fontsize(5.5)
        ax.set_title(title)
        return _save_fig(fig, self._next_name("pie"), title, "pie")

    def render_dot(self, df: pd.DataFrame, x: str, y: str, title: str = "Dot plot") -> Dict[str, Any]:
        """Mean ± SEM Cleveland-style dot plot (Nature 组间比较常用)."""
        plt = _import_plt()
        fig, ax = plt.subplots(figsize=FIGSIZE_SINGLE)
        agg = df.groupby(x, observed=True)[y].agg(["mean", "sem"]).sort_values("mean")
        y_pos = np.arange(len(agg))
        ax.errorbar(agg["mean"], y_pos, xerr=agg["sem"], fmt="o", color=color_at(0),
                    ecolor="#555555", elinewidth=0.7, capsize=2, markersize=4.5)
        ax.set_yticks(y_pos)
        ax.set_yticklabels([str(i) for i in agg.index])
        ax.set_xlabel(f"Mean {y}")
        ax.set_title(title)
        polish_axes(ax)
        return _save_fig(fig, self._next_name("dot"), title, "dot")

    def render_forest(self, effects: pd.DataFrame,
                      title: str = "Forest plot") -> Dict[str, Any]:
        """effects: columns label, estimate, low, high."""
        plt = _import_plt()
        need = {"label", "estimate", "low", "high"}
        cols = {c.lower(): c for c in effects.columns}
        if not need.issubset(cols.keys()) and not need.issubset(set(effects.columns)):
            # try common aliases
            rename = {}
            for a, b in [("var", "label"), ("name", "label"), ("effect", "estimate"),
                         ("ci_low", "low"), ("ci_high", "high"), ("lower", "low"), ("upper", "high")]:
                if a in effects.columns and b not in effects.columns:
                    rename[a] = b
            effects = effects.rename(columns=rename)
        if not {"label", "estimate", "low", "high"}.issubset(effects.columns):
            return {}
        fig, ax = plt.subplots(figsize=FIGSIZE_1_5COL)
        y = np.arange(len(effects))[::-1]
        est = effects["estimate"].astype(float)
        low = effects["low"].astype(float)
        high = effects["high"].astype(float)
        ax.errorbar(est, y, xerr=[est - low, high - est], fmt="s", color=color_at(0),
                    ecolor="#444444", elinewidth=0.8, capsize=2.5, markersize=4)
        ax.axvline(0, color="#888888", lw=0.6, ls="--")
        ax.set_yticks(y)
        ax.set_yticklabels(effects["label"].astype(str))
        ax.set_xlabel("Effect (95% CI)")
        ax.set_title(title)
        polish_axes(ax)
        top = []
        for _, row in effects.head(8).iterrows():
            top.append({
                "label": str(row["label"]),
                "estimate": round(float(row["estimate"]), 4),
                "low": round(float(row["low"]), 4),
                "high": round(float(row["high"]), 4),
            })
        facts = {
            "n_effects": int(len(effects)),
            "effects": top,
            "strongest_abs": max(top, key=lambda d: abs(d["estimate"])) if top else None,
        }
        return _save_fig(fig, self._next_name("forest"), title, "forest", facts=facts)

    def render_ridge(self, df: pd.DataFrame, x: str, y: str, title: str = "Ridge plot") -> Dict[str, Any]:
        plt = _import_plt()
        from scipy.stats import gaussian_kde

        groups = list(df.groupby(x, observed=True))
        if len(groups) < 2:
            return {}
        fig, axes = plt.subplots(len(groups), 1, figsize=(FIGSIZE_SINGLE[0], 0.55 * len(groups) + 0.8),
                                 sharex=True)
        if len(groups) == 1:
            axes = [axes]
        global_min, global_max = np.inf, -np.inf
        series_list = []
        for name, grp in groups:
            s = _numeric(grp[y])
            series_list.append((str(name), s))
            if len(s):
                global_min = min(global_min, float(s.min()))
                global_max = max(global_max, float(s.max()))
        xs = np.linspace(global_min, global_max, 200) if global_max > global_min else np.linspace(0, 1, 200)
        for i, (ax, (name, s)) in enumerate(zip(axes, series_list)):
            if len(s) >= 5:
                dens = gaussian_kde(s.values)(xs)
                ax.fill_between(xs, dens, color=color_at(i), alpha=0.55)
                ax.plot(xs, dens, color=color_at(i), lw=0.8)
            ax.set_yticks([])
            ax.set_ylabel(name, rotation=0, ha="right", va="center", fontsize=6)
            ax.spines["left"].set_visible(False)
            polish_axes(ax)
        axes[-1].set_xlabel(y)
        axes[0].set_title(title)
        fig.subplots_adjust(hspace=-0.15)
        return _save_fig(fig, self._next_name("ridge"), title, "ridge")

    def render_km_curve(self, times: pd.Series, events: pd.Series,
                        group: Optional[pd.Series] = None,
                        title: str = "Kaplan–Meier curve") -> Dict[str, Any]:
        """简易 KM：S(t) 阶梯曲线（无需 lifelines）。"""
        plt = _import_plt()
        fig, ax = plt.subplots(figsize=FIGSIZE_1_5COL)

        def _km(t: np.ndarray, e: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
            order = np.argsort(t)
            t, e = t[order], e[order]
            n = len(t)
            at_risk = n
            surv = 1.0
            xs, ys = [0.0], [1.0]
            for ti, ei in zip(t, e):
                if ei:
                    surv *= (at_risk - 1) / at_risk if at_risk else surv
                    xs.append(float(ti))
                    ys.append(surv)
                at_risk -= 1
            return np.array(xs), np.array(ys)

        t_all = pd.to_numeric(times, errors="coerce")
        e_all = pd.to_numeric(events, errors="coerce").fillna(0).astype(int)
        mask = t_all.notna()
        if group is not None:
            for i, gname in enumerate(pd.Series(group)[mask].unique()[:6]):
                m = mask & (group == gname)
                xs, ys = _km(t_all[m].values, e_all[m].values)
                ax.step(xs, ys, where="post", label=str(gname), color=color_at(i), lw=1.2)
            ax.legend(loc="best")
        else:
            xs, ys = _km(t_all[mask].values, e_all[mask].values)
            ax.step(xs, ys, where="post", color=color_at(0), lw=1.2)
        ax.set_xlabel("Time")
        ax.set_ylabel("Survival probability")
        ax.set_ylim(0, 1.05)
        ax.set_title(title)
        polish_axes(ax)
        return _save_fig(fig, self._next_name("km_curve"), title, "km_curve")

    # ── 产物驱动 ────────────────────────────────────────────

    def render_from_histogram_csv(self, csv_path: Path) -> List[Dict[str, Any]]:
        charts: List[Dict[str, Any]] = []
        try:
            hist = pd.read_csv(csv_path)
        except Exception:
            return charts
        if "column" in hist.columns and "count" in hist.columns and "bin_left" in hist.columns:
            for col in hist["column"].unique():
                if is_skip_chart_column(col):
                    continue
                if len(charts) >= 6:
                    break
                sub = hist[hist["column"] == col]
                plt = _import_plt()
                fig, ax = plt.subplots(figsize=FIGSIZE_SINGLE)
                if "bin_right" in sub.columns:
                    centers = (sub["bin_left"] + sub["bin_right"]) / 2
                    width = (sub["bin_right"] - sub["bin_left"]).median() or 1.0
                else:
                    centers = sub["bin_left"]
                    width = 1.0
                ax.bar(centers, sub["count"], width=width * 0.9, color=color_at(0),
                       edgecolor="white", linewidth=0.3)
                ax.set_title(f"Distribution: {col}")
                ax.set_xlabel(str(col))
                ax.set_ylabel("Count")
                polish_axes(ax)
                charts.append(_save_fig(
                    fig, self._next_name("histogram"), f"Distribution: {col}", "histogram",
                    columns=[str(col)], x=str(col), facts=_hist_csv_facts(str(col), sub),
                ))
        return charts

    def render_from_correlation_csv(self, csv_path: Path) -> List[Dict[str, Any]]:
        charts: List[Dict[str, Any]] = []

        def _without_internal_ids(mat: pd.DataFrame) -> pd.DataFrame:
            labels = [
                c for c in mat.columns
                if c in mat.index and not is_skip_chart_column(c)
            ]
            return mat.loc[labels, labels] if len(labels) >= 2 else pd.DataFrame()

        try:
            if "matrix" in csv_path.name:
                mat = pd.read_csv(csv_path, index_col=0)
                mat = _without_internal_ids(mat)
                c = self.render_heatmap(mat, "Pearson correlation")
                if c:
                    charts.append(c)
            elif "pairs" in csv_path.name:
                pairs = pd.read_csv(csv_path)
                if {"var_a", "var_b", "r"}.issubset(pairs.columns):
                    vars_ = sorted(
                        v for v in (set(pairs["var_a"]) | set(pairs["var_b"]))
                        if not is_skip_chart_column(v)
                    )
                    if len(vars_) < 2:
                        return charts
                    mat = pd.DataFrame(np.eye(len(vars_)), index=vars_, columns=vars_)
                    for _, row in pairs.iterrows():
                        if row["var_a"] in mat.index and row["var_b"] in mat.columns:
                            mat.loc[row["var_a"], row["var_b"]] = row["r"]
                            mat.loc[row["var_b"], row["var_a"]] = row["r"]
                    c = self.render_heatmap(mat, "Correlation matrix")
                    if c:
                        charts.append(c)
        except Exception as exc:
            _LOG.debug("correlation chart skip: %s", exc)
        return charts

    def render_spec(self, df: pd.DataFrame, spec: Dict[str, Any]) -> Dict[str, Any]:
        """Render one user chart spec. Raises ValueError on invalid fields."""
        return _chart_renderer_render_spec(self, df, spec)

    def auto_render_dataframe(self, df: pd.DataFrame) -> List[Dict[str, Any]]:
        charts: List[Dict[str, Any]] = []
        num_cols = filter_chartable_numeric_cols(df)
        cat_cols = [
            c for c in df.select_dtypes(include=["object", "category", "bool"]).columns.tolist()
            if not is_skip_chart_column(c, df[c])
        ]
        # Prefer low-cardinality grouping vars (skip id-like columns)
        group_cols = [
            c for c in cat_cols
            if 2 <= df[c].nunique(dropna=True) <= 12
            and not str(c).lower().endswith("_id")
            and "id" != str(c).lower()
        ]

        def _add(c):
            if c:
                charts.append(c)

        if df.isnull().any().any():
            _add(self.render_missing_heatmap(df.head(500)))

        for col in num_cols[:3]:
            _add(self.render_histogram(df[col], f"Histogram: {col}"))
            _add(self.render_kde(df[col], f"Density: {col}"))

        if len(num_cols) >= 2:
            _add(self.render_correlation_heatmap(df, num_cols[:12]))
            _add(self.render_scatter(df, num_cols[0], num_cols[1],
                                     title=f"{num_cols[0]} vs {num_cols[1]}"))
            # residual-style: simple linear fit residuals
            try:
                x = _numeric(df[num_cols[0]])
                y = _numeric(df[num_cols[1]])
                n = min(len(x), len(y))
                if n >= 8:
                    coef = np.polyfit(x.values[:n], y.values[:n], 1)
                    pred = np.polyval(coef, x.values[:n])
                    _add(self.render_residual(pd.Series(y.values[:n]), pd.Series(pred),
                                              title=f"Residuals: {num_cols[1]} ~ {num_cols[0]}"))
            except Exception:
                pass

        if group_cols and num_cols:
            x, y = group_cols[0], num_cols[0]
            if 2 <= df[x].nunique() <= 12:
                for fn, prefix in [
                    (self.render_violin, "Violin"),
                    (self.render_box, "Box"),
                    (self.render_bar, "Bar"),
                    (self.render_strip, "Strip"),
                    (self.render_dot, "Dot"),
                ]:
                    try:
                        _add(fn(df, x, y, f"{prefix}: {y} by {x}"))
                    except Exception:
                        pass
                if df[x].nunique() <= 8:
                    try:
                        _add(self.render_ridge(df, x, y, f"Ridge: {y} by {x}"))
                    except Exception:
                        pass
            if len(group_cols) >= 2 and 2 <= df[group_cols[1]].nunique() <= 6:
                try:
                    _add(self.render_grouped_bar(df, group_cols[0], num_cols[0], group_cols[1],
                                                 f"Grouped bar: {num_cols[0]}"))
                except Exception:
                    pass

        pie_col = group_cols[0] if group_cols else (cat_cols[0] if cat_cols else None)
        if pie_col and df[pie_col].nunique() <= 12:
            _add(self.render_pie(df[pie_col], f"Distribution: {pie_col}"))

        if len(num_cols) >= 3:
            _add(self.render_pca_scatter(df, num_cols))

        for col in num_cols[:2]:
            _add(self.render_qq(df[col], f"Q–Q: {col}"))

        # synthetic forest from pairwise correlations (effect = r)
        if len(num_cols) >= 3:
            try:
                corr = df[num_cols[:6]].corr()
                rows = []
                for i, a in enumerate(corr.columns):
                    for b in corr.columns[i + 1:]:
                        r = float(corr.loc[a, b])
                        # approximate CI via Fisher (n from complete cases)
                        n = int(df[[a, b]].dropna().shape[0])
                        if n < 8:
                            continue
                        z = np.arctanh(np.clip(r, -0.999, 0.999))
                        se = 1 / np.sqrt(max(n - 3, 1))
                        lo, hi = np.tanh(z - 1.96 * se), np.tanh(z + 1.96 * se)
                        rows.append({"label": f"{a}–{b}", "estimate": r, "low": lo, "high": hi})
                if rows:
                    _add(self.render_forest(pd.DataFrame(rows[:12]), "Forest: pairwise correlations"))
            except Exception:
                pass

        # KM if time-like + event-like columns exist
        time_cands = [c for c in num_cols if any(k in c.lower() for k in ("time", "day", "month", "follow"))]
        event_cands = [c for c in df.columns if any(k in str(c).lower() for k in ("event", "censor", "status", "death", "relapse"))]
        if time_cands and event_cands:
            try:
                g = cat_cols[0] if cat_cols else None
                _add(self.render_km_curve(df[time_cands[0]], df[event_cands[0]],
                                          df[g] if g else None,
                                          title=f"Kaplan–Meier: {time_cands[0]}"))
            except Exception:
                pass

        return charts

    def auto_render_artifacts(self, pipeline_output: Path, source_df: Optional[pd.DataFrame] = None) -> List[Dict[str, Any]]:
        charts: List[Dict[str, Any]] = []
        if pipeline_output.is_dir():
            for csv_path in sorted(pipeline_output.rglob("*.csv")):
                name = csv_path.name.lower()
                if "distribution_histogram" in name:
                    charts.extend(self.render_from_histogram_csv(csv_path))
                elif "pearson" in name or "correlation" in name or "matrix" in name:
                    charts.extend(self.render_from_correlation_csv(csv_path))
                elif "deg" in name or "limma" in name or "deseq" in name:
                    try:
                        deg = pd.read_csv(csv_path)
                        c = self.render_volcano(deg)
                        if c:
                            charts.append(c)
                    except Exception:
                        pass

        # Always enrich with dataframe-driven charts so users see the full
        # Nature catalog (violin/box/heatmap/…), not only pipeline histograms.
        if source_df is not None:
            try:
                charts.extend(self.auto_render_dataframe(source_df))
            except Exception as exc:
                _LOG.warning("auto_render_dataframe failed: %s", exc)

        seen = set()
        unique = []
        for c in charts:
            title = str(c.get("title") or "")
            # 丢弃内部行号等无意义图（标题形如 Distribution: __row_id__）
            if any(tok in title.lower() for tok in ("__row_id__", "row_id", "unnamed: 0")):
                continue
            p = c.get("path")
            if p and p not in seen:
                seen.add(p)
                unique.append(c)
        return unique


def render_charts_from_run(run_dir: Path, source_csv: Optional[Path] = None) -> List[Dict[str, Any]]:
    run_dir = Path(run_dir)
    renderer = ChartRenderer(run_dir / "charts")
    source_df = None
    if source_csv and Path(source_csv).is_file():
        try:
            p = Path(source_csv)
            source_df = pd.read_csv(p) if p.suffix.lower() == ".csv" else pd.read_excel(p)
        except Exception:
            source_df = None
    charts = renderer.auto_render_artifacts(run_dir / "pipeline_output", source_df)
    meta_path = run_dir / "charts.json"
    slim = [{k: v for k, v in c.items() if k != "base64"} for c in charts]
    meta_path.write_text(json.dumps(slim, ensure_ascii=False, indent=2), encoding="utf-8")
    # style note for UI
    (run_dir / "charts" / "STYLE_NATURE.md").write_text(
        "# Nature-style figures\n\n"
        "- Palette: Okabe–Ito (colorblind-safe) + Nature muted categorical\n"
        "- Fonts: Arial/Helvetica, ~6–8 pt; 300 dpi RGB PNG\n"
        "- Sizes: single-column ~89 mm; no top/right spines\n"
        "- Avoid red–green; diverging RdBu_r; sequential viridis\n"
        "- Refs: Nature Final guide to authors; Okabe–Ito 2008\n",
        encoding="utf-8",
    )
    return charts


def render_charts_from_specs(
    run_dir: Path,
    source_csv: Path,
    specs: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Render only user-specified chart specs into run_dir/charts."""
    run_dir = Path(run_dir)
    renderer = ChartRenderer(run_dir / "charts")
    p = Path(source_csv)
    if p.suffix.lower() in (".xlsx", ".xls"):
        df = pd.read_excel(p)
    else:
        df = pd.read_csv(p)
    charts: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    for i, spec in enumerate(specs or []):
        try:
            c = renderer.render_spec(df, spec)
            if c:
                charts.append(c)
            else:
                errors.append({"index": i, "spec": spec, "error": "empty render result"})
        except Exception as exc:
            errors.append({"index": i, "spec": spec, "error": str(exc)})
    meta_path = run_dir / "charts.json"
    slim = [{k: v for k, v in c.items() if k != "base64"} for c in charts]
    meta_path.write_text(
        json.dumps({"charts": slim, "errors": errors, "mode": "user_specs"}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return charts


# Attach render_spec to ChartRenderer class
def _chart_renderer_render_spec(self: "ChartRenderer", df: pd.DataFrame, spec: Dict[str, Any]) -> Dict[str, Any]:
    """Render one user chart spec. Raises ValueError on invalid fields."""
    if not isinstance(spec, dict):
        raise ValueError("chart spec 必须是对象")
    ctype = str(spec.get("type") or spec.get("chart_type") or "").strip().lower()
    if not ctype:
        raise ValueError("缺少 type")
    x = spec.get("x")
    y = spec.get("y")
    hue = spec.get("hue")
    cols = spec.get("cols")
    title = str(spec.get("title") or "").strip()
    params = spec.get("params") if isinstance(spec.get("params"), dict) else {}

    def _need_cols(*names: str) -> None:
        for n in names:
            if n and n not in df.columns:
                raise ValueError(f"列不存在: {n}")

    if ctype in ("histogram", "kde", "qq"):
        col = y or x
        if not col:
            raise ValueError(f"{ctype} 需要 y 或 x（数值列）")
        _need_cols(col)
        title = title or f"{ctype}: {col}"
        if ctype == "histogram":
            return self.render_histogram(df[col], title, bins=int(params.get("bins") or 20)) or {}
        if ctype == "kde":
            return self.render_kde(df[col], title) or {}
        return self.render_qq(df[col], title) or {}

    if ctype == "pie":
        col = x or y
        if not col:
            raise ValueError("pie 需要 x 或 y（分类列）")
        _need_cols(col)
        return self.render_pie(df[col], title or f"Pie: {col}") or {}

    if ctype == "missing_heatmap":
        return self.render_missing_heatmap(df, title or "Missing value pattern") or {}

    if ctype in ("violin", "box", "bar", "strip", "dot", "ridge"):
        if not x or not y:
            raise ValueError(f"{ctype} 需要 x（分组列）与 y（数值列）")
        _need_cols(x, y)
        title = title or f"{ctype}: {y} by {x}"
        fn = {
            "violin": self.render_violin,
            "box": self.render_box,
            "bar": self.render_bar,
            "strip": self.render_strip,
            "dot": self.render_dot,
            "ridge": self.render_ridge,
        }[ctype]
        return fn(df, x, y, title) or {}

    if ctype in ("scatter", "line"):
        if not x or not y:
            raise ValueError(f"{ctype} 需要 x 与 y")
        _need_cols(x, y)
        if hue:
            _need_cols(hue)
        title = title or f"{ctype}: {x} vs {y}"
        if ctype == "scatter":
            return self.render_scatter(df, x, y, hue=hue, title=title) or {}
        return self.render_line(df, x, y, hue=hue, title=title) or {}

    if ctype in ("grouped_bar", "stacked_bar"):
        if not x or not y or not hue:
            raise ValueError(f"{ctype} 需要 x、y、hue")
        _need_cols(x, y, hue)
        title = title or f"{ctype}: {y}"
        if ctype == "grouped_bar":
            return self.render_grouped_bar(df, x, y, hue, title) or {}
        return self.render_stacked_bar(df, x, y, hue, title) or {}

    if ctype in ("correlation_heatmap", "heatmap"):
        use_cols = list(cols) if isinstance(cols, list) and cols else None
        if use_cols:
            for c in use_cols:
                _need_cols(c)
        title = title or "Correlation heatmap"
        return self.render_correlation_heatmap(df, use_cols, title) or {}

    if ctype == "pca_scatter":
        use_cols = list(cols) if isinstance(cols, list) and cols else None
        if use_cols:
            for c in use_cols:
                _need_cols(c)
        return self.render_pca_scatter(df, use_cols, title or "PCA scatter") or {}

    if ctype == "volcano":
        logfc = params.get("logfc_col") or spec.get("logfc_col") or "logFC"
        pcol = params.get("p_col") or spec.get("p_col") or "P.Value"
        # allow x/y aliases
        if x and not params.get("logfc_col"):
            logfc = x
        if y and not params.get("p_col"):
            pcol = y
        _need_cols(logfc, pcol)
        return self.render_volcano(df, logfc_col=logfc, p_col=pcol, title=title or "Volcano plot") or {}

    if ctype == "km_curve":
        time_c = x
        event_c = y
        if not time_c or not event_c:
            raise ValueError("km_curve 需要 x=时间列, y=事件列, 可选 hue=分组列")
        _need_cols(time_c, event_c)
        g = None
        if hue:
            _need_cols(hue)
            g = df[hue]
        return self.render_km_curve(df[time_c], df[event_c], g, title or f"Kaplan–Meier: {time_c}") or {}

    if ctype == "residual":
        if not x or not y:
            raise ValueError("residual 需要 x（预测/自变量）与 y（因变量）；将做简单线性拟合残差")
        _need_cols(x, y)
        sub = df[[x, y]].dropna()
        if len(sub) < 8:
            raise ValueError("residual 需要至少 8 行完整配对")
        coef = np.polyfit(sub[x].astype(float), sub[y].astype(float), 1)
        pred = np.polyval(coef, sub[x].astype(float))
        return self.render_residual(sub[y], pd.Series(pred), title or f"Residuals: {y} ~ {x}") or {}

    if ctype == "forest":
        # Build pairwise correlation forest from cols or numeric cols
        use_cols = list(cols) if isinstance(cols, list) and cols else df.select_dtypes(include=[np.number]).columns.tolist()[:6]
        for c in use_cols:
            _need_cols(c)
        if len(use_cols) < 2:
            raise ValueError("forest 至少需要 2 个数值列（cols）")
        corr = df[use_cols].corr()
        rows = []
        for i, a in enumerate(corr.columns):
            for b in corr.columns[i + 1:]:
                r = float(corr.loc[a, b])
                n = int(df[[a, b]].dropna().shape[0])
                if n < 8:
                    continue
                z = np.arctanh(np.clip(r, -0.999, 0.999))
                se = 1 / np.sqrt(max(n - 3, 1))
                lo, hi = np.tanh(z - 1.96 * se), np.tanh(z + 1.96 * se)
                rows.append({"label": f"{a}–{b}", "estimate": r, "low": lo, "high": hi})
        if not rows:
            raise ValueError("forest 无法从所选列生成效应量")
        return self.render_forest(pd.DataFrame(rows[:12]), title or "Forest: pairwise correlations") or {}

    raise ValueError(f"不支持的图种: {ctype}")


