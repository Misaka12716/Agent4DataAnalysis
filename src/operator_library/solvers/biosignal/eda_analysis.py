"""EDA (Electrodermal Activity / GSR) analysis operator.

Analyzes skin conductance / EDA signals to extract tonic (SCL) and phasic (SCR)
components, peak counts, and amplitude metrics.  Backed by neurokit2.

Output: eda_metrics.csv, eda_peaks.csv, eda_stats.json.
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
    name="eda_analysis",
    capability="F16_biosignal_eda",
    description=(
        "Electrodermal activity (EDA/GSR) analysis. Decomposes signal into "
        "tonic (SCL) and phasic (SCR) components, detects peaks, computes "
        "amplitude/rise time metrics. Output: eda_metrics.csv, eda_peaks.csv. "
        "Use when: EDA, electrodermal, skin conductance, SCR, SCL, GSR, "
        "galvanic skin response, sweat / sudomotor activity."
    ),
    roles={
        "signal_col": RoleSpec(Role.NUMERIC, "EDA signal column (microsiemens)"),
        "timestamp_col": RoleSpec(
            Role.DATETIME,
            "Optional timestamp column.",
            optional=True,
        ),
        "subject_col": RoleSpec(
            Role.ID,
            "Optional subject id column for per-subject analysis.",
            optional=True,
        ),
    },
    static_params={"sampling_rate": 250},
    output_files={
        "eda_metrics_csv": "eda_metrics.csv",
        "eda_peaks_csv": "eda_peaks.csv",
        "stats_json": "eda_stats.json",
    },
    output_kind={"eda_metrics_csv": "s", "eda_peaks_csv": "t", "stats_json": "s"},
)


class EDASolver:
    contract = CONTRACT

    def __init__(self, sampling_rate: int = 250):
        if not _NK_OK:
            raise ImportError("neurokit2 is required for eda_analysis")
        self.sampling_rate = sampling_rate

    def _analyze_eda(self, signal: np.ndarray, fs: int) -> Dict[str, Any]:
        try:
            signals, info = nk.eda_process(signal, sampling_rate=fs)
            peaks = info.get("SCR_Peaks", np.array([], dtype=int))
            amp = info.get("SCR_Amplitude", np.array([]))
            rt = info.get("SCR_RiseTime", np.array([]))
            tonic_mean = float(signals["EDA_Tonic"].mean())
            phasic_std = float(signals["EDA_Phasic"].std())
            n_peaks = len(peaks)
            amp_mean = float(np.mean(amp)) if len(amp) > 0 else np.nan
            return {
                "tonic_mean": tonic_mean,
                "phasic_std": phasic_std,
                "n_scr_peaks": n_peaks,
                "scr_amplitude_mean": amp_mean,
            }
        except Exception:
            return {}

    def run(self, df, mapping, output_dir):
        signal_col = mapping.get("signal_col")
        subject_col = mapping.get("subject_col")
        if not signal_col:
            raise OperatorInputError("MISSING_REQUIRED_COLUMNS",
                                     solver="eda_analysis",
                                     hint="signal_col is required")
        fs = self.sampling_rate
        subjects = df[subject_col].unique() if subject_col else [None]
        all_metrics, all_peaks = [], []
        for subj in subjects:
            sub_df = df[df[subject_col] == subj].copy() if subject_col else df.copy()
            sid = str(subj) if subject_col else "all"
            sig = sub_df[signal_col].values
            sig = sig[np.isfinite(sig)]
            if len(sig) < fs * 5:
                continue
            m = self._analyze_eda(sig, fs)
            if m:
                m["subject"] = sid
                all_metrics.append(m)
            try:
                _, info = nk.eda_process(sig, sampling_rate=fs)
                pk_idx = info.get("SCR_Peaks", np.array([], dtype=int))
                amp_arr = info.get("SCR_Amplitude", np.array([]))
                for k, idx in enumerate(pk_idx):
                    all_peaks.append({
                        "subject": sid, "peak_index": int(idx),
                        "amplitude": float(amp_arr[k]) if k < len(amp_arr) else np.nan,
                    })
            except Exception:
                pass

        metrics_df = pd.DataFrame(all_metrics)
        metrics_path = output_dir / "eda_metrics.csv"
        metrics_df.to_csv(metrics_path, index=False)

        peaks_df = pd.DataFrame(all_peaks)
        peaks_path = output_dir / "eda_peaks.csv"
        peaks_df.to_csv(peaks_path, index=False)

        import json
        stats = {"n_subjects": len(subjects) if subject_col else 1,
                 "n_valid": len(all_metrics), "sampling_rate": fs}
        stats_path = output_dir / "eda_stats.json"
        with open(stats_path, "w", encoding="utf-8") as f:
            json.dump(stats, f, indent=2)

        return {"eda_metrics_csv": str(metrics_path),
                "eda_peaks_csv": str(peaks_path),
                "stats_json": str(stats_path)}


def get_solver(sampling_rate: int = 250):
    return EDASolver(sampling_rate=sampling_rate)
