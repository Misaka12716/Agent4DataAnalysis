"""Molecular / tabular supervised property prediction (QSAR-style).

This operator fills the gap that made the coder reach for heavy, often
*uninstalled* deep-learning stacks (``deepchem`` / ``DeepPurpose``) and
then crash: training a supervised model on labelled molecules and
emitting predictions.  It is deliberately **sklearn-only** so it never
fails on a missing optional dependency, and **auto-detects
classification vs regression** so one operator covers binary toxicity
(DILI / HIV / BBB), continuous property regression (formation energy,
solubility), etc.

Design goals (same bar as the rest of the operator library)
===========================================================
- **Generalizable**: works on ANY (SMILES → label) or (numeric features
  → label) table; no task-specific column names baked in.
- **Robust**: graceful when RDKit is absent (falls back to numeric
  feature columns), drops unparseable SMILES, label-encodes string
  targets, guards single-class / tiny-sample edge cases, and — crucially
  — **never leaks**: if no genuine held-out test set is wired it returns
  honest out-of-fold (cross-validated) predictions on the training rows
  instead of predicting on the data it trained on.
- **Token-cheap for the coder**: the heavy, must-be-correct ML lives
  here; the coder only reads ``property_predictions.csv`` and reshapes it
  into whatever exact output filename / column names the task wants.

What it does NOT do (honest scope)
==================================
- Multi-task / neural-net models (e.g. clintox multi-endpoint NN) — use
  a ``__coder__`` step.
- Drug–Target Interaction needing **protein-sequence** featurization
  (e.g. DAVIS) — there is no protein encoder here; only the drug side is
  featurized.  Route DTI to ``__coder__``.

Output
======
- ``property_predictions.csv``: ``[<id>, prediction, (probability)]``
  aligned to the predicted rows (held-out test rows, or all training
  rows when using the CV fallback).
- ``property_metrics.json``: classification → ``{auroc, f1, accuracy}``;
  regression → ``{rmse, mae, r2}``; plus ``task``, ``model``, ``n``,
  ``mode`` ("holdout" | "cv"), and ``feature_source``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from ...contract import ColumnMapping, Role, RoleSpec, SolverContract
from operator_pipeline.error_codes import OperatorInputError

try:
    from rdkit import Chem
    from rdkit.Chem import AllChem
    from rdkit import RDLogger, DataStructs
    RDLogger.logger().setLevel(RDLogger.ERROR)
    _RDKIT_OK = True
except ImportError:
    _RDKIT_OK = False


CONTRACT = SolverContract(
    name="molecular_property_predict",
    capability="F16_supervised_property_prediction",
    description=(
        "Train a supervised model and PREDICT a molecular/material property "
        "(QSAR). Auto-detects classification (binary/categorical label) vs "
        "regression (continuous target). Features come from a SMILES column "
        "(internally featurized to ECFP4/Morgan 2048-bit) OR from precomputed "
        "numeric feature columns (e.g. an upstream fingerprint/descriptor "
        "matrix). sklearn-only (RandomForest by default) so it never needs "
        "deepchem/DeepPurpose. If a separate held-out test_csv is wired it "
        "trains on the input table and predicts the test rows; otherwise it "
        "returns leakage-free 5-fold out-of-fold predictions on the input "
        "rows. Output: property_predictions.csv (id + prediction [+ "
        "probability]) and property_metrics.json (auroc/f1/acc or "
        "rmse/mae/r2). "
        "Use when: QSAR, molecular property prediction, toxicity / activity / "
        "permeability / solubility prediction, ADMET, train a model and "
        "predict labels for compounds (classification or regression)."
    ),
    roles={
        "id_col": RoleSpec(
            Role.ID,
            "Row identifier carried into the predictions (e.g. smiles, "
            "compound id, name). Optional — a 0..N-1 row_id is synthesized "
            "if absent.",
            optional=True,
        ),
        "smiles_col": RoleSpec(
            Role.TEXT,
            "SMILES column to featurize into ECFP fingerprints. Provide "
            "this OR feature_columns.",
            optional=True,
        ),
        "feature_columns": RoleSpec(
            Role.NUMERIC_LIST,
            "Precomputed numeric predictor columns (e.g. fingerprint bits or "
            "descriptors). Used when no smiles_col is given.",
            optional=True,
        ),
        "target_col": RoleSpec(
            Role.NUMERIC_TARGET,
            "Supervised target in the input table: a 0/1 or class label "
            "(classification) or a continuous value (regression). If omitted "
            "the operator auto-detects a label-like column.",
            optional=True,
        ),
    },
    static_params={
        # held-out test set; *_csv so the pipeline can autowire/override it.
        # When it resolves to the SAME content as the training table the
        # operator detects that and falls back to CV (no leakage).
        "test_csv": None,
        "model": "auto",          # auto | rf | gbdt | logreg | linear | svm
        "task": "auto",           # auto | classification | regression
        "radius": 2,
        "n_bits": 2048,
        "cv_folds": 5,
        "random_state": 42,
    },
    output_files={
        "predictions_csv": "property_predictions.csv",
        "metrics_json": "property_metrics.json",
    },
    output_kind={"predictions_csv": "t", "metrics_json": "s"},
)


_LABEL_NAME_HINTS = (
    "label", "target", "class", "outcome", "activity", "active", "y",
    "toxic", "toxicity", "permeab", "penetrat", "property", "value",
    "energy", "solub", "response", "dili", "hiv", "bbb",
)


class MolecularPropertyPredictSolver:
    contract = CONTRACT

    def __init__(self, model: str = "auto", task: str = "auto",
                 test_csv: Optional[str] = None, radius: int = 2,
                 n_bits: int = 2048, cv_folds: int = 5,
                 random_state: int = 42):
        self.model = str(model or "auto").lower()
        self.task = str(task or "auto").lower()
        self.test_csv = test_csv or None
        self.radius = int(radius)
        self.n_bits = int(n_bits)
        self.cv_folds = int(cv_folds)
        self.random_state = int(random_state)

    # ------------------------------------------------------------------
    # feature construction
    # ------------------------------------------------------------------
    def _ecfp(self, smiles: List[str]) -> Tuple[np.ndarray, np.ndarray]:
        """Return (matrix [n, n_bits], valid_mask). Invalid → all-zero row."""
        if not _RDKIT_OK:
            raise OperatorInputError(
                "MISSING_DEPENDENCY",
                solver=CONTRACT.name,
                hint="rdkit not installed; supply feature_columns instead of "
                     "smiles_col",
            )
        mat = np.zeros((len(smiles), self.n_bits), dtype=np.float64)
        valid = np.zeros(len(smiles), dtype=bool)
        for i, smi in enumerate(smiles):
            s = (smi or "").strip()
            if not s:
                continue
            mol = Chem.MolFromSmiles(s)
            if mol is None:
                continue
            fp = AllChem.GetMorganFingerprintAsBitVect(
                mol, self.radius, nBits=self.n_bits)
            arr = np.zeros(self.n_bits, dtype=np.float64)
            DataStructs.ConvertToNumpyArray(fp, arr)
            mat[i] = arr
            valid[i] = True
        return mat, valid

    def _features(self, df: pd.DataFrame, smiles_col: Optional[str],
                  feature_columns: Optional[List[str]],
                  exclude: List[str]) -> Tuple[np.ndarray, np.ndarray, str]:
        """Return (X, valid_mask, feature_source)."""
        if smiles_col and smiles_col in df.columns:
            X, valid = self._ecfp(df[smiles_col].astype(str).tolist())
            return X, valid, "ecfp_from_smiles"
        feats = [c for c in (feature_columns or []) if c in df.columns]
        if not feats:
            # auto: every numeric column that is not id/target/smiles
            feats = [c for c in df.columns
                     if c not in exclude
                     and pd.api.types.is_numeric_dtype(df[c])]
        if not feats:
            raise OperatorInputError(
                "INSUFFICIENT_FEATURES",
                solver=CONTRACT.name,
                hint="no smiles_col and no usable numeric feature_columns",
            )
        X = df[feats].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
        valid = ~np.isnan(X).any(axis=1)
        # impute remaining NaNs (in valid rows none; keep robust anyway)
        X = np.nan_to_num(X, nan=0.0)
        return X, valid, "numeric_columns"

    # ------------------------------------------------------------------
    # target resolution
    # ------------------------------------------------------------------
    def _resolve_target(self, df: pd.DataFrame, mapping: ColumnMapping,
                        exclude: List[str]) -> Optional[str]:
        t = mapping.get("target_col")
        if t and t in df.columns:
            return t
        # auto-detect: prefer name hints, then a low-cardinality / non-id col
        cands = [c for c in df.columns if c not in exclude]
        named = [c for c in cands
                 if any(h in str(c).lower() for h in _LABEL_NAME_HINTS)]
        for pool in (named, cands):
            for c in pool:
                nun = df[c].nunique(dropna=True)
                if 2 <= nun <= max(20, int(0.5 * len(df))) or \
                        pd.api.types.is_numeric_dtype(df[c]):
                    return c
        return None

    def _detect_task(self, y: pd.Series) -> str:
        if self.task in ("classification", "regression"):
            return self.task
        if not pd.api.types.is_numeric_dtype(y):
            return "classification"
        nun = y.nunique(dropna=True)
        # few distinct integer-ish values → classification
        if nun <= 10 and np.allclose(y.dropna() % 1, 0):
            return "classification"
        return "regression"

    def _make_model(self, task: str):
        from sklearn.ensemble import (RandomForestClassifier,
                                       RandomForestRegressor,
                                       HistGradientBoostingClassifier,
                                       HistGradientBoostingRegressor)
        m = self.model
        rs = self.random_state
        if task == "classification":
            table = {
                "auto": RandomForestClassifier(n_estimators=300, n_jobs=1,
                                               random_state=rs),
                "rf": RandomForestClassifier(n_estimators=300, n_jobs=1,
                                             random_state=rs),
                "gbdt": HistGradientBoostingClassifier(random_state=rs),
            }
            if m == "logreg":
                from sklearn.linear_model import LogisticRegression
                return LogisticRegression(max_iter=2000)
            if m == "svm":
                from sklearn.svm import SVC
                return SVC(probability=True, random_state=rs)
            return table.get(m, table["auto"])
        else:
            table = {
                "auto": RandomForestRegressor(n_estimators=300, n_jobs=1,
                                              random_state=rs),
                "rf": RandomForestRegressor(n_estimators=300, n_jobs=1,
                                            random_state=rs),
                "gbdt": HistGradientBoostingRegressor(random_state=rs),
            }
            if m in ("linear", "logreg"):
                from sklearn.linear_model import Ridge
                return Ridge()
            if m == "svm":
                from sklearn.svm import SVR
                return SVR()
            return table.get(m, table["auto"])

    # ------------------------------------------------------------------
    def _load_test(self, df_train: pd.DataFrame,
                   target_col: Optional[str]) -> Optional[pd.DataFrame]:
        """Load held-out test set if a genuinely different one is wired."""
        if not self.test_csv:
            return None
        p = Path(self.test_csv)
        if not p.is_file():
            return None
        try:
            df_test = pd.read_csv(p)
        except Exception:
            return None
        # leakage guard: same shape + same columns + (if target present)
        # identical target ⇒ this is just the training table autowired back.
        if (df_test.shape == df_train.shape
                and list(df_test.columns) == list(df_train.columns)):
            try:
                if target_col and target_col in df_test.columns:
                    if df_test[target_col].reset_index(drop=True).equals(
                            df_train[target_col].reset_index(drop=True)):
                        return None
                else:
                    return None
            except Exception:
                return None
        return df_test

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
            output_dir: Path) -> Dict[str, Any]:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        id_col = mapping.get("id_col")
        smiles_col = mapping.get("smiles_col")
        feature_columns = mapping.get("feature_columns") or []
        if isinstance(feature_columns, str):
            feature_columns = [feature_columns]

        exclude = [c for c in (id_col, smiles_col) if c]
        target_col = self._resolve_target(df, mapping, exclude)
        if target_col is None:
            raise OperatorInputError(
                "MISSING_REQUIRED_COLUMNS", solver=CONTRACT.name,
                hint="could not resolve a target/label column for training")
        exclude = exclude + [target_col]

        df_test = self._load_test(df, target_col)
        mode = "holdout" if df_test is not None else "cv"

        # --- training features / target ---
        X_tr, valid_tr, feat_src = self._features(
            df, smiles_col, feature_columns, exclude)
        y_raw = df[target_col]
        # keep only rows with valid features and non-null target
        keep = valid_tr & y_raw.notna().to_numpy()
        if keep.sum() < 5:
            raise OperatorInputError(
                "INSUFFICIENT_SAMPLES", solver=CONTRACT.name,
                required_n=5, actual_n=int(keep.sum()))
        X_tr = X_tr[keep]
        y_tr = y_raw[keep].reset_index(drop=True)

        task = self._detect_task(y_tr)

        # encode classification labels (string or arbitrary) → ints
        classes = None
        if task == "classification":
            classes = sorted(pd.Series(y_tr).dropna().unique().tolist(),
                             key=lambda v: str(v))
            class_to_int = {c: i for i, c in enumerate(classes)}
            y_enc = pd.Series(y_tr).map(class_to_int).to_numpy()
        else:
            y_enc = pd.to_numeric(y_tr, errors="coerce").to_numpy(dtype=float)

        metrics: Dict[str, Any] = {"task": task, "model": self.model,
                                   "mode": mode, "feature_source": feat_src,
                                   "n_train": int(len(y_enc))}

        if mode == "holdout":
            X_te, valid_te, _ = self._features(
                df_test, smiles_col, feature_columns, exclude)
            model = self._make_model(task)
            model.fit(X_tr, y_enc)
            pred_int = model.predict(X_te)
            pred_df = self._pred_frame(df_test, id_col, smiles_col, task,
                                       classes, model, X_te, pred_int)
            # metrics only if test carries the true target
            if target_col in df_test.columns:
                self._score(metrics, task, df_test[target_col],
                            classes, pred_int, model, X_te)
            metrics["n_test"] = int(len(df_test))
        else:
            pred_int, proba = self._cv_predict(task, X_tr, y_enc)
            pred_df = self._pred_frame(
                df.loc[keep].reset_index(drop=True), id_col, smiles_col,
                task, classes, None, None, pred_int, proba=proba)
            self._score(metrics, task, y_tr, classes, pred_int,
                        None, None, proba=proba)

        pc = output_dir / CONTRACT.output_files["predictions_csv"]
        pred_df.to_csv(pc, index=False)
        mj = output_dir / CONTRACT.output_files["metrics_json"]
        mj.write_text(json.dumps(metrics, ensure_ascii=False, indent=2,
                                 default=str), encoding="utf-8")
        return {"predictions_csv": str(pc), "metrics_json": str(mj),
                "metrics_dict": metrics}

    # ------------------------------------------------------------------
    def _cv_predict(self, task: str, X: np.ndarray, y: np.ndarray):
        from sklearn.model_selection import (StratifiedKFold, KFold,
                                             cross_val_predict)
        n = len(y)
        k = max(2, min(self.cv_folds, n))
        if task == "classification":
            # cap folds by smallest class
            import collections
            min_c = min(collections.Counter(y).values())
            k = max(2, min(k, min_c))
            cv = StratifiedKFold(n_splits=k, shuffle=True,
                                 random_state=self.random_state)
            model = self._make_model(task)
            pred = cross_val_predict(model, X, y, cv=cv)
            proba = None
            if hasattr(self._make_model(task), "predict_proba"):
                try:
                    proba = cross_val_predict(self._make_model(task), X, y,
                                              cv=cv, method="predict_proba")
                except Exception:
                    proba = None
            return pred, proba
        cv = KFold(n_splits=k, shuffle=True, random_state=self.random_state)
        pred = cross_val_predict(self._make_model(task), X, y, cv=cv)
        return pred, None

    def _pred_frame(self, src: pd.DataFrame, id_col, smiles_col, task,
                    classes, model, X, pred_int, proba=None) -> pd.DataFrame:
        out = pd.DataFrame()
        if id_col and id_col in src.columns:
            out[id_col] = src[id_col].values
        elif smiles_col and smiles_col in src.columns:
            out[smiles_col] = src[smiles_col].values
        else:
            out["row_id"] = list(range(len(src)))
        if task == "classification" and classes is not None:
            int_to_class = {i: c for i, c in enumerate(classes)}
            out["prediction"] = [int_to_class.get(int(v), v) for v in pred_int]
            # positive-class probability for binary problems
            pos_proba = self._positive_proba(model, X, proba, len(classes))
            if pos_proba is not None and len(pos_proba) == len(out):
                out["probability"] = pos_proba
        else:
            out["prediction"] = np.asarray(pred_int, dtype=float)
        return out

    @staticmethod
    def _positive_proba(model, X, proba, n_classes):
        if n_classes != 2:
            return None
        if proba is not None:
            try:
                return np.asarray(proba)[:, 1]
            except Exception:
                return None
        if model is not None and hasattr(model, "predict_proba") and X is not None:
            try:
                return model.predict_proba(X)[:, 1]
            except Exception:
                return None
        return None

    def _score(self, metrics, task, y_true_raw, classes, pred_int,
               model, X, proba=None):
        from sklearn.metrics import (roc_auc_score, f1_score, accuracy_score,
                                      mean_squared_error, mean_absolute_error,
                                      r2_score)
        try:
            if task == "classification" and classes is not None:
                c2i = {c: i for i, c in enumerate(classes)}
                yt = pd.Series(y_true_raw).map(
                    lambda v: c2i.get(v, np.nan)).to_numpy()
                m = ~pd.isna(yt)
                yt = yt[m].astype(int)
                yp = np.asarray(pred_int)[m].astype(int)
                metrics["accuracy"] = float(accuracy_score(yt, yp))
                metrics["f1"] = float(
                    f1_score(yt, yp, average="binary" if len(classes) == 2
                             else "macro"))
                pos = self._positive_proba(model, X, proba, len(classes))
                if pos is not None and len(classes) == 2:
                    try:
                        metrics["auroc"] = float(roc_auc_score(yt, pos[m]))
                    except Exception:
                        pass
            else:
                yt = pd.to_numeric(y_true_raw, errors="coerce").to_numpy()
                m = ~np.isnan(yt)
                yt = yt[m]
                yp = np.asarray(pred_int, dtype=float)[m]
                metrics["rmse"] = float(np.sqrt(mean_squared_error(yt, yp)))
                metrics["mae"] = float(mean_absolute_error(yt, yp))
                metrics["r2"] = float(r2_score(yt, yp))
        except Exception as e:
            metrics["score_error"] = f"{type(e).__name__}: {e}"


def get_solver(model: str = "auto", task: str = "auto",
               test_csv: Optional[str] = None, radius: int = 2,
               n_bits: int = 2048, cv_folds: int = 5,
               random_state: int = 42):
    return MolecularPropertyPredictSolver(
        model=model, task=task, test_csv=test_csv, radius=radius,
        n_bits=n_bits, cv_folds=cv_folds, random_state=random_state)


def selftest() -> Dict[str, Any]:
    """Factory self-check: classification (with SMILES→ECFP if rdkit) and
    regression (numeric features) on synthetic data."""
    import tempfile
    diffs: List[str] = []
    rng = np.random.default_rng(0)

    # --- regression on numeric features (always runs, no rdkit needed) ---
    n = 200
    Xr = rng.normal(size=(n, 6))
    yreg = Xr[:, 0] * 2.0 - Xr[:, 1] + 0.5 * Xr[:, 2] + rng.normal(scale=0.1, size=n)
    dfr = pd.DataFrame({f"f{i}": Xr[:, i] for i in range(6)})
    dfr["cid"] = [f"C{i}" for i in range(n)]
    dfr["energy"] = yreg
    mr = ColumnMapping({"id_col": "cid",
                        "feature_columns": [f"f{i}" for i in range(6)],
                        "target_col": "energy"})
    with tempfile.TemporaryDirectory() as tmp:
        out = get_solver().run(df=dfr, mapping=mr, output_dir=Path(tmp))
        r2 = out["metrics_dict"].get("r2", -1)
        if out["metrics_dict"]["task"] != "regression":
            diffs.append("regression task not detected")
        if r2 < 0.8:
            diffs.append(f"regression r2 too low: {r2}")

    # --- classification on numeric features ---
    yc = (yreg > np.median(yreg)).astype(int)
    dfc = dfr.copy()
    dfc["label"] = yc
    dfc = dfc.drop(columns=["energy"])
    mc = ColumnMapping({"id_col": "cid",
                        "feature_columns": [f"f{i}" for i in range(6)],
                        "target_col": "label"})
    with tempfile.TemporaryDirectory() as tmp:
        out = get_solver().run(df=dfc, mapping=mc, output_dir=Path(tmp))
        if out["metrics_dict"]["task"] != "classification":
            diffs.append("classification task not detected")
        if out["metrics_dict"].get("accuracy", 0) < 0.75:
            diffs.append(f"clf accuracy too low: {out['metrics_dict'].get('accuracy')}")

    # --- string labels + ECFP from SMILES (only if rdkit present) ---
    if _RDKIT_OK:
        smis = (["CCO", "CCN", "c1ccccc1", "CC(=O)O", "CCCl", "CCBr",
                 "C1CCCCC1", "CCOCC", "CN", "CO"] * 6)[:50]
        dfs = pd.DataFrame({"smiles": smis})
        dfs["activity"] = (["DILI", "NO"] * 25)[:50]
        ms = ColumnMapping({"id_col": "smiles", "smiles_col": "smiles",
                            "target_col": "activity"})
        with tempfile.TemporaryDirectory() as tmp:
            out = get_solver().run(df=dfs, mapping=ms, output_dir=Path(tmp))
            pred = pd.read_csv(out["predictions_csv"])
            if "prediction" not in pred.columns:
                diffs.append("no prediction column for smiles classification")
            if out["metrics_dict"]["feature_source"] != "ecfp_from_smiles":
                diffs.append("ecfp featurization not used for smiles input")

    return {"ok": not diffs,
            "summary": "molecular_property_predict ok" if not diffs
                       else f"{len(diffs)} issue(s)",
            "details": {"diffs": diffs, "rdkit": _RDKIT_OK}}
