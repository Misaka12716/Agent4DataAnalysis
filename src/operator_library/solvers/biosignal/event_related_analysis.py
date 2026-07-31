"""Event-related biosignal epoch extraction and analysis.

Given a biosignal CSV and an event marker column (or external events CSV),
extracts epochs around events and computes per-epoch statistics (mean, std,
AUC, peak, latency).  Supports ECG, EDA, EEG, RSP channels.

Output: epochs.csv (long table, one row per sample per epoch),
epoch_stats.csv (one row per epoch), events_summary.json.
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
    name="event_related_analysis",
    capability="F16_biosignal_event_related",
    description=(
        "Extract event-related epochs from continuous biosignal. "
        "Given signal columns + event onsets, computes peri-event windows "
        "and per-epoch statistics. Output: epochs.csv, epoch_stats.csv."
    ),
    roles={
        "signal_cols": RoleSpec(
            Role.NUMERIC_LIST,
            "List of signal columns to epochify (e.g. ['ECG','EDA']).",
        ),
        "timestamp_col": RoleSpec(
            Role.DATETIME,
            "Optional timestamp column.",
            optional=True,
        ),
        "event_col": RoleSpec(
            Role.NUMERIC,
            "Binary event marker column (0=no event, 1=event onset). "
            "Alternatively provide events_csv below.",
            optional=True,
        ),
        "events_csv": RoleSpec(
            Role.PARAMS,
            "Path to an events CSV with onset_sample column. "
            "Alternative to event_col.",
            optional=True,
        ),
        "subject_col": RoleSpec(
            Role.ID,
            "Optional subject id for per-subject analysis.",
            optional=True,
        ),
    },
    static_params={
        "sampling_rate": 250,
        "epoch_start_s": -0.5,
        "epoch_end_s": 2.0,
    },
    output_files={
        "epochs_csv": "epochs.csv",
        "epoch_stats_csv": "epoch_stats.csv",
        "stats_json": "epoch_stats.json",
    },
    output_kind={"epochs_csv": "t", "epoch_stats_csv": "s", "stats_json": "s"},
)


class EventRelatedSolver:
    contract = CONTRACT

    def __init__(self, sampling_rate: int = 250, epoch_start_s: float = -0.5,
                 epoch_end_s: float = 2.0):
        if not _NK_OK:
            raise ImportError("neurokit2 is required for event_related_analysis")
        self.sampling_rate = sampling_rate
        self.epoch_start_s = epoch_start_s
        self.epoch_end_s = epoch_end_s

    def run(self, df, mapping, output_dir):
        signal_cols = mapping.get("signal_cols")
        event_col = mapping.get("event_col")
        events_csv_path = mapping.get("events_csv")
        subject_col = mapping.get("subject_col")
        if not signal_cols:
            raise OperatorInputError("MISSING_REQUIRED_COLUMNS",
                                     solver="event_related_analysis",
                                     hint="signal_cols is required")

        fs = self.sampling_rate
        pre_samples = int(abs(self.epoch_start_s) * fs)
        post_samples = int(self.epoch_end_s * fs)
        epoch_len = pre_samples + post_samples

        if isinstance(signal_cols, str):
            signal_cols = [c.strip() for c in signal_cols.split(",")]

        subjects = df[subject_col].unique() if subject_col else [None]
        all_rows, all_stats = [], []

        for subj in subjects:
            subj_str = str(subj) if subject_col else "all"
            sub_df = df[df[subject_col] == subj].copy() if subject_col else df.copy()

            if event_col:
                onsets = np.where(sub_df[event_col].values > 0.5)[0]
            elif events_csv_path:
                ev_df = pd.read_csv(events_csv_path)
                onsets = ev_df["onset_sample"].values
            else:
                raise OperatorInputError("MISSING_REQUIRED_COLUMNS",
                    solver="event_related_analysis",
                    hint="provide event_col or events_csv")

            if len(onsets) == 0:
                continue

            for ei, onset in enumerate(onsets):
                onset = int(onset)
                start = onset - pre_samples
                end = onset + post_samples
                if start < 0 or end > len(sub_df):
                    continue
                epoch_stats = {"subject": subj_str, "epoch_idx": ei,
                               "onset_sample": onset}
                for sc in signal_cols:
                    if sc not in sub_df.columns:
                        continue
                    chunk = sub_df[sc].values[start:end]
                    chunk_clean = chunk[np.isfinite(chunk)]
                    if len(chunk_clean) < 2:
                        continue
                    epoch_stats[f"{sc}_mean"] = float(np.mean(chunk_clean))
                    epoch_stats[f"{sc}_std"] = float(np.std(chunk_clean))
                    epoch_stats[f"{sc}_min"] = float(np.min(chunk_clean))
                    epoch_stats[f"{sc}_max"] = float(np.max(chunk_clean))
                    epoch_stats[f"{sc}_auc"] = float(np.trapz(
                        np.abs(chunk_clean), dx=1.0/fs))

                    for ti in range(epoch_len):
                        idx = start + ti
                        if idx >= len(sub_df):
                            break
                        val = sub_df[sc].values[idx]
                        if np.isfinite(val):
                            all_rows.append({
                                "subject": subj_str, "epoch_idx": ei,
                                "channel": sc, "time_s": round(ti/fs - abs(self.epoch_start_s), 4),
                                "value": float(val),
                            })
                all_stats.append(epoch_stats)

        epochs_df = pd.DataFrame(all_rows)
        epochs_path = output_dir / "epochs.csv"
        epochs_df.to_csv(epochs_path, index=False)

        stats_df = pd.DataFrame(all_stats)
        stats_path = output_dir / "epoch_stats.csv"
        stats_df.to_csv(stats_path, index=False)

        import json
        summary = {"n_events": len(onsets) if len(subjects) > 0 else 0,
                   "n_epochs_extracted": len(all_stats),
                   "sampling_rate": fs, "epoch_window": [self.epoch_start_s, self.epoch_end_s]}
        json_path = output_dir / "epoch_stats.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2)

        return {"epochs_csv": str(epochs_path), "epoch_stats_csv": str(stats_path),
                "stats_json": str(json_path)}


def get_solver(sampling_rate: int = 250, epoch_start_s: float = -0.5,
               epoch_end_s: float = 2.0):
    return EventRelatedSolver(sampling_rate=sampling_rate,
                               epoch_start_s=epoch_start_s,
                               epoch_end_s=epoch_end_s)
