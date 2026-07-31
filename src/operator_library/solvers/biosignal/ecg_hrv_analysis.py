"""ECG Heart Rate Variability (HRV) analysis operator.

Given an ECG time-series CSV, computes standard HRV metrics (RMSSD, SDNN, pNN50,
LF/HF ratio, etc.) using neurokit2.  Supports both raw ECG (auto peak detection)
and pre-detected R-peak indices.

Output: hrv_metrics.csv (subject-level), hrv_timecourse.csv (per-window),
hrv_stats.json.
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
    name="ecg_hrv_analysis",
    capability="F16_biosignal_ecg_hrv",
        description=(
            "Heart rate variability (HRV) analysis from a raw ECG / EKG "
            "time-series signal. Internally detects R-peaks (QRS / R-wave) "
            "and derives RR / NN inter-beat intervals, then computes "
            "time-domain (RMSSD, SDNN, pNN50, mean HR) and frequency-domain "
            "(LF, HF, LF/HF) HRV metrics using neurokit2. Use this for any "
            "task mentioning ECG/EKG, R-peaks, RR intervals, heartbeat "
            "intervals, or heart-rate variability. "
            "Output: hrv_metrics.csv, hrv_timecourse.csv, hrv_stats.json."
        ),
    roles={
        "signal_col": RoleSpec(Role.NUMERIC, "ECG signal column (raw mV values)"),
        "timestamp_col": RoleSpec(
            Role.DATETIME,
            "Optional timestamp column for time-axis annotations.",
            optional=True,
        ),
        "subject_col": RoleSpec(
            Role.ID,
            "Optional subject/recording id column. When given, HRV is "
            "computed per subject.",
            optional=True,
        ),
    },
    static_params={
        "sampling_rate": 250,
        "window_seconds": 300,
        "overlap_seconds": 30,
    },
    output_files={
        "hrv_metrics_csv": "hrv_metrics.csv",
        "hrv_timecourse_csv": "hrv_timecourse.csv",
        "stats_json": "hrv_stats.json",
    },
    output_kind={
        "hrv_metrics_csv": "s",
        "hrv_timecourse_csv": "t",
        "stats_json": "s",
    },
)


class ECGHRVSolver:
    contract = CONTRACT

    def __init__(self, sampling_rate: int = 250, window_seconds: int = 300,
                 overlap_seconds: int = 30):
        if not _NK_OK:
            raise ImportError("neurokit2 is required for ecg_hrv_analysis")
        self.sampling_rate = sampling_rate
        self.window_seconds = window_seconds
        self.overlap_seconds = overlap_seconds

    def _compute_hrv(self, ecg_signal: np.ndarray, fs: int) -> Dict[str, float]:
        try:
            _, info = nk.ecg_process(ecg_signal, sampling_rate=fs)
            rpeaks = info.get("ECG_R_Peaks", None)
            if rpeaks is None or len(rpeaks) < 2:
                return {}
            hrv_indices = nk.hrv(rpeaks, sampling_rate=fs)
            return {
                k: float(v.iloc[0]) if hasattr(v, "iloc") else float(v)
                for k, v in hrv_indices.items()
            }
        except Exception:
            return {}

    def run(self, df, mapping, output_dir):
        signal_col = mapping.get("signal_col")
        subject_col = mapping.get("subject_col")
        if not signal_col:
            raise OperatorInputError("MISSING_REQUIRED_COLUMNS",
                                     solver="ecg_hrv_analysis",
                                     hint="signal_col is required")

        fs = self.sampling_rate
        window_s = self.window_seconds
        step_s = max(1, int(fs * (window_s - self.overlap_seconds)))
        window_n = int(fs * window_s)

        subjects = df[subject_col].unique() if subject_col else [None]

        all_metrics, all_windows = [], []
        for subj in subjects:
            if subject_col:
                sub_df = df[df[subject_col] == subj].copy()
            else:
                sub_df = df.copy()
                subj = "all"

            sig = sub_df[signal_col].values
            sig = sig[np.isfinite(sig)]
            if len(sig) < fs * 10:
                continue

            metrics = self._compute_hrv(sig, fs)
            if metrics:
                metrics["subject"] = str(subj)
                all_metrics.append(metrics)

            for start in range(0, max(1, len(sig) - window_n), step_s):
                end = min(start + window_n, len(sig))
                if end - start < fs * 30:
                    continue
                chunk = sig[start:end]
                chunk_metrics = self._compute_hrv(chunk, fs)
                if chunk_metrics:
                    chunk_metrics["subject"] = str(subj)
                    chunk_metrics["window_start_s"] = start / fs
                    chunk_metrics["window_end_s"] = end / fs
                    all_windows.append(chunk_metrics)

        metrics_df = pd.DataFrame(all_metrics)
        metrics_path = output_dir / "hrv_metrics.csv"
        metrics_df.to_csv(metrics_path, index=False)

        windows_df = pd.DataFrame(all_windows)
        windows_path = output_dir / "hrv_timecourse.csv"
        windows_df.to_csv(windows_path, index=False)

        import json
        stats = {"n_subjects": len(subjects) if subject_col else 1,
                 "n_valid": len(all_metrics), "n_windows": len(all_windows),
                 "sampling_rate": fs}
        stats_path = output_dir / "hrv_stats.json"
        with open(stats_path, "w", encoding="utf-8") as f:
            json.dump(stats, f, indent=2)

        return {"hrv_metrics_csv": str(metrics_path),
                "hrv_timecourse_csv": str(windows_path),
                "stats_json": str(stats_path)}


def get_solver(sampling_rate: int = 250, window_seconds: int = 300,
               overlap_seconds: int = 30):
    return ECGHRVSolver(sampling_rate=sampling_rate,
                         window_seconds=window_seconds,
                         overlap_seconds=overlap_seconds)
