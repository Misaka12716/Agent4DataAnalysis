"""EOG (Electrooculography) and blink detection operator.

Analyzes EOG signals to detect blinks, saccades, and fixations.
Backed by neurokit2.  Input is a CSV with EOG channel values.

Output: eog_metrics.csv, eog_events.csv, eog_stats.json.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from ...contract import ColumnMapping, Role, RoleSpec, SolverContract
from operator_pipeline.error_codes import OperatorInputError

try:
    import neurokit2 as nk
    _NK_OK = True
except ImportError:
    _NK_OK = False

CONTRACT = SolverContract(
    name="eog_analysis",
    capability="F16_biosignal_eog",
    description=(
        "Electrooculography (EOG) blink/saccade detection. "
        "Cleans the EOG signal, detects blinks, computes blink rate, and "
        "saccade metrics. Output: eog_metrics.csv, eog_events.csv. "
        "Use when: EOG, electrooculography, blink detection, blink rate, "
        "eye movement, saccade, fixation, ocular signal."
    ),
    roles={
        "signal_col": RoleSpec(Role.NUMERIC, "EOG signal column (microvolts)"),
        "timestamp_col": RoleSpec(
            Role.DATETIME,
            "Optional timestamp column.",
            optional=True,
        ),
        "subject_col": RoleSpec(
            Role.ID,
            "Optional subject/recording id column.",
            optional=True,
        ),
    },
    static_params={"sampling_rate": 100, "method": "neurokit"},
    output_files={
        "eog_metrics_csv": "eog_metrics.csv",
        "eog_events_csv": "eog_events.csv",
        "stats_json": "eog_stats.json",
    },
    output_kind={"eog_metrics_csv": "s", "eog_events_csv": "t", "stats_json": "s"},
)


class EOGSolver:
    contract = CONTRACT

    def __init__(self, sampling_rate: int = 100, method: str = "neurokit"):
        if not _NK_OK:
            raise ImportError("neurokit2 is required for eog_analysis")
        self.sampling_rate = sampling_rate
        self.method = method

    @staticmethod
    def _coerce_blink_indices(peaks_obj, n_samples: int) -> np.ndarray:
        """nk.eog_findpeaks may return a 1-D ndarray of sample indices, a
        dict with an ``EOG_Blinks`` key, or a DataFrame with a binary
        ``EOG_Blinks`` column.  Normalise to a 1-D int array of indices."""
        if peaks_obj is None:
            return np.zeros(0, dtype=int)
        if isinstance(peaks_obj, np.ndarray):
            arr = peaks_obj.astype(int).ravel()
            return arr[(arr >= 0) & (arr < n_samples)]
        if isinstance(peaks_obj, dict):
            arr = np.asarray(peaks_obj.get("EOG_Blinks", []), dtype=int).ravel()
            return arr[(arr >= 0) & (arr < n_samples)]
        if isinstance(peaks_obj, pd.DataFrame):
            if "EOG_Blinks" in peaks_obj.columns:
                vals = peaks_obj["EOG_Blinks"].to_numpy()
                if set(np.unique(vals[~pd.isna(vals)])).issubset({0, 1}):
                    return np.where(vals > 0)[0]
                return vals.astype(int).ravel()
            return np.zeros(0, dtype=int)
        try:
            return np.asarray(peaks_obj, dtype=int).ravel()
        except Exception:
            return np.zeros(0, dtype=int)

    def run(self, df, mapping, output_dir):
        signal_col = mapping.get("signal_col")
        subject_col = mapping.get("subject_col")
        if not signal_col:
            raise OperatorInputError("MISSING_REQUIRED_COLUMNS",
                                     solver="eog_analysis",
                                     hint="signal_col is required")
        fs = self.sampling_rate
        subjects = df[subject_col].unique() if subject_col else [None]
        all_metrics, all_events = [], []
        last_error: str = ""
        for subj in subjects:
            sub_df = df[df[subject_col] == subj].copy() if subject_col else df.copy()
            sid = str(subj) if subject_col else "all"
            sig = sub_df[signal_col].values.astype(float)
            sig = sig[np.isfinite(sig)]
            if len(sig) < fs * 2:
                continue
            try:
                cleaned = nk.eog_clean(sig, sampling_rate=fs, method=self.method)
                peaks_obj = nk.eog_findpeaks(cleaned, sampling_rate=fs,
                                              method=self.method)
                blink_idx = self._coerce_blink_indices(peaks_obj, len(cleaned))
                n_blinks = int(len(blink_idx))
                duration_s = len(sig) / fs
                blink_rate = n_blinks / duration_s if duration_s > 0 else 0.0
                all_metrics.append({
                    "subject": sid, "n_blinks": n_blinks,
                    "duration_s": duration_s, "blink_rate_hz": blink_rate,
                })
                for bs in blink_idx:
                    all_events.append({
                        "subject": sid, "sample_index": int(bs),
                        "time_s": float(bs) / fs, "event": "blink",
                    })
            except Exception as e:
                last_error = f"{type(e).__name__}: {e}"

        if not all_metrics and last_error:
            raise RuntimeError(f"eog_analysis: all subjects failed; "
                               f"last_error={last_error}")

        metrics_df = pd.DataFrame(all_metrics)
        metrics_path = output_dir / "eog_metrics.csv"
        metrics_df.to_csv(metrics_path, index=False)

        events_df = pd.DataFrame(all_events)
        events_path = output_dir / "eog_events.csv"
        events_df.to_csv(events_path, index=False)

        import json
        stats = {"n_subjects": len(subjects) if subject_col else 1,
                 "n_valid": len(all_metrics), "sampling_rate": fs}
        stats_path = output_dir / "eog_stats.json"
        with open(stats_path, "w", encoding="utf-8") as f:
            json.dump(stats, f, indent=2)

        return {"eog_metrics_csv": str(metrics_path),
                "eog_events_csv": str(events_path),
                "stats_json": str(stats_path)}


def get_solver(sampling_rate: int = 100, method: str = "neurokit"):
    return EOGSolver(sampling_rate=sampling_rate, method=method)
