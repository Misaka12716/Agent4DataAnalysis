"""Tree-based classifier solvers (F06 / Q11 / Q12).

  - random_forest_classifier_cv  (Q11)
  - gradient_boosting_classifier_cv (sklearn HistGradientBoosting → Q12)
  - xgboost_classifier_cv (optional; only if xgboost is installed)

All use 5-fold stratified CV with fixed seed=42 and report
``auroc, accuracy, f1`` plus per-feature importance.

中文说明
========
四个树模型 solver，**共享同一套 CV / 评估骨架**（_TreeCVBase），
只换 ``model_factory``：

  - ``random_forest_cv``   sklearn RandomForestClassifier (n_est=200)
  - ``hist_gbdt_cv``       sklearn HistGradientBoostingClassifier
  - ``xgboost_cv``         XGBClassifier  （xgboost 装了才有）
  - ``lightgbm_cv``        LGBMClassifier （lightgbm 装了才有）

XGBoost / LightGBM 的工厂函数是延迟 import，模块加载时不会因为这
两个 optional 依赖缺失而 crash。

公共流程：
  1. 解析 X, y（同 logistic_regression.py：df 内 target 优先，否则
     external_label_csv merge）
  2. ``StandardScaler(with_mean=False) -> tree`` 包成 Pipeline
     （树本身不需要 scaling，scaler 只为了和其他 solver 行为一致；
     with_mean=False 保证 sparse 输入也能用）
  3. StratifiedKFold(5, shuffle, seed=42) + cross_val_predict 出 OOF
  4. 全量 refit 一次拿 ``feature_importances_``

CV 折数对小样本的影响（与 LR 一致）
====================================
- n < 100：5 折每折验证 < 20，方差很大；AUROC ±0.05 是常态
- n ∈ [100, 500]：5 折是稳定的甜点，单折偶尔抖动可接受
- n > 1000：5 折非常稳，10 折收益边际

输出
====
- ``{name}_metrics.json``：{auroc, accuracy, f1, n, n_pos,
                             feature_importances: {feat: weight}}
- ``{name}_predictions.csv``：[id, y_pred, y_pred_proba]
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from sklearn.ensemble import (
    HistGradientBoostingClassifier,
    RandomForestClassifier,
)
from sklearn.metrics import accuracy_score, f1_score, roc_auc_score
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.pipeline import Pipeline as SKPipeline
from sklearn.preprocessing import StandardScaler

from ..contract import ColumnMapping, Role, RoleSpec, SolverContract


def _bootstrap_classifier_ci(*, X, y, proba, pred, pipe_factory, feats,
                              n_bootstrap: int, ci_level: float,
                              base_seed: int):
    """V8 Pattern E — bootstrap CI for AUROC/F1/Accuracy and per-feature
    importance.  Returns ``(rows, metric_ci_dict)``.

    Metric CI is computed by **resampling indices on the OOF predictions**
    (fast, no refit).  Feature-importance CI is computed by refitting
    the supplied pipeline on each bootstrap sample (slower, but the
    only honest way for tree-importance distributions).  When the
    classifier exposes no ``feature_importances_``, the per-feature CI
    table contains only metric rows.

    The default ``n_bootstrap=200`` keeps wall time well below 1 minute
    on n<2000 datasets; set to 0 to skip entirely (CI columns become
    NaN and the CSV is empty bar metric rows).
    """
    import math

    rows = []
    metric_ci = {}

    if n_bootstrap <= 0 or len(y) < 10:
        return rows, metric_ci

    rng = np.random.default_rng(base_seed)
    n = len(y)
    alpha = (1.0 - ci_level) / 2.0

    aurocs = []
    f1s = []
    accs = []
    for _ in range(n_bootstrap):
        idx = rng.integers(0, n, n)
        yb = y[idx]
        # Skip bootstrap iterations where the resample is degenerate.
        if yb.sum() == 0 or yb.sum() == len(yb):
            continue
        try:
            aurocs.append(roc_auc_score(yb, proba[idx]))
        except Exception:
            pass
        try:
            f1s.append(f1_score(yb, pred[idx]))
        except Exception:
            pass
        try:
            accs.append(accuracy_score(yb, pred[idx]))
        except Exception:
            pass

    def _ci(arr):
        if not arr:
            return (float("nan"), float("nan"))
        lo = float(np.quantile(arr, alpha))
        hi = float(np.quantile(arr, 1.0 - alpha))
        return (lo, hi)

    if aurocs:
        lo, hi = _ci(aurocs)
        metric_ci["auroc"] = {"lower": lo, "upper": hi}
        rows.append({"feature": "__metric_auroc__", "estimate": float(np.mean(aurocs)),
                     "ci_lower": lo, "ci_upper": hi,
                     "kind": "metric", "n_boot": len(aurocs)})
    if f1s:
        lo, hi = _ci(f1s)
        metric_ci["f1"] = {"lower": lo, "upper": hi}
        rows.append({"feature": "__metric_f1__", "estimate": float(np.mean(f1s)),
                     "ci_lower": lo, "ci_upper": hi,
                     "kind": "metric", "n_boot": len(f1s)})
    if accs:
        lo, hi = _ci(accs)
        metric_ci["accuracy"] = {"lower": lo, "upper": hi}
        rows.append({"feature": "__metric_accuracy__", "estimate": float(np.mean(accs)),
                     "ci_lower": lo, "ci_upper": hi,
                     "kind": "metric", "n_boot": len(accs)})

    # Feature importance CI — refit on bootstrap samples.  Cap to keep
    # wall time bounded; use min(n_bootstrap, 50) refits which is enough
    # for a stable 95% interval on most datasets.
    n_refit = min(n_bootstrap, 50)
    boot_importances: Dict[str, list] = {f: [] for f in feats}
    for _ in range(n_refit):
        idx = rng.integers(0, n, n)
        yb = y[idx]
        if yb.sum() == 0 or yb.sum() == len(yb):
            continue
        try:
            pipe = pipe_factory(int(rng.integers(0, 2**31 - 1)))
            pipe.fit(X[idx], yb)
            clf = pipe.named_steps["clf"]
            if hasattr(clf, "feature_importances_"):
                for f, w in zip(feats, clf.feature_importances_):
                    boot_importances[f].append(float(w))
        except Exception:
            continue

    for f in feats:
        arr = boot_importances.get(f) or []
        lo, hi = _ci(arr)
        rows.append({
            "feature": f,
            "estimate": float(np.mean(arr)) if arr else float("nan"),
            "ci_lower": lo,
            "ci_upper": hi,
            "kind": "feature_importance",
            "n_boot": len(arr),
        })

    return rows, metric_ci


# Contract 工厂：4 个树模型的 contract 结构完全一致，只是 name /
# 输出文件名不同；统一在 _make_contract 里造，避免重复。
# Roles 暴露给 LLM 的语义与 logistic_regression 一致：id + 数值特征
# + （可选）二分类目标。
def _make_contract(name: str, model_label: str) -> SolverContract:
    return SolverContract(
        name=name,
        capability="F06_supervised_classification",
        description=(
            f"{model_label} for a binary outcome with 5-fold stratified "
            f"CV.  Feature columns may be raw numeric predictors OR a "
            f"molecular fingerprint / descriptor matrix produced upstream "
            f"by morgan_fingerprint / molecular_descriptors.  Output: "
            f"metrics.json (auroc/accuracy/f1 + feature importance), "
            f"predictions.csv, ci.csv (bootstrap 95%CI)."),
        roles={
            "id_col":          RoleSpec(Role.ID, "row identifier"),
            "feature_columns": RoleSpec(Role.NUMERIC_LIST,
                                          "numeric / 0-1 predictor columns"),
            "target_col":      RoleSpec(
                Role.BINARY_TARGET,
                "0/1 outcome (omit → external_label_csv)",
                optional=True),
        },
        static_params={
            "random_state": 42, "cv_folds": 5,
            "external_label_csv": None,
            # V8 Pattern E — bootstrap CI for the headline metrics and
            # per-feature importance.  Defaults are modest so the
            # default-config run stays fast (~+1-2s for n=300, k=200).
            "n_bootstrap": 200,
            "ci_level": 0.95,
        },
        output_files={
            "metrics_json":     f"{name}_metrics.json",
            "predictions_csv":  f"{name}_predictions.csv",
            "ci_csv":           f"{name}_ci.csv",
        },
        output_kind={"metrics_json": "s", "predictions_csv": "t",
                      "ci_csv": "s"},
    )


class _TreeCVBase:
    contract: SolverContract
    model_factory: Any   # lambda random_state: estimator

    def __init__(self, random_state: int = 42, cv_folds: int = 5,
                 external_label_csv: Optional[str] = None,
                 external_label_col: str = "y_true",
                 n_bootstrap: int = 200, ci_level: float = 0.95):
        """中文：所有树模型 solver 的公共 __init__。

        :param random_state: 固定 seed=42。同时控制 CV split 和树模型
                             内部的 bootstrap / 列采样，保证可复现。
        :param cv_folds:     CV 折数，默认 5（参见模块顶部"CV 折数
                             对小样本的影响"）。
        :param external_label_csv / external_label_col: 同
                             logistic_regression.py，外部标签文件。
        :param n_bootstrap:  V8 Pattern E — bootstrap 次数, 默认 200。
                             0 关掉 bootstrap (向后兼容 + 调试用)。
        :param ci_level:     CI 名义置信水平, 默认 0.95 (双侧分位数)。
        """
        self.random_state = random_state
        self.cv_folds = cv_folds
        self.external_label_csv = external_label_csv
        self.external_label_col = external_label_col
        self.n_bootstrap = int(n_bootstrap)
        self.ci_level = float(ci_level)

    def _resolve_y(self, df, mapping):
        id_col = mapping["id_col"]
        target_col = mapping.get("target_col")
        if target_col and target_col in df.columns:
            return df, df[target_col].astype(int).to_numpy()
        if not self.external_label_csv:
            raise ValueError("no target column resolved and no "
                              "external_label_csv set")
        label_df = pd.read_csv(self.external_label_csv)
        df_xy = df.merge(label_df, on=id_col, how="inner")
        return df_xy, df_xy[self.external_label_col].astype(int).to_numpy()

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
            output_dir: Path) -> Dict[str, Any]:
        id_col = mapping["id_col"]
        feats = mapping["feature_columns"]
        df_xy, y = self._resolve_y(df, mapping)
        X = df_xy[feats].astype(float).to_numpy()

        est = self.model_factory(random_state=self.random_state)
        # 树模型本身不依赖 scaling，但保留 scaler 是为了和 LR/SVM 走
        # 同一个 pipeline 形状（评估代码也好统一）；with_mean=False 让
        # sparse 特征也能用，不会因为减均值变 dense 把内存撑爆
        pipe = SKPipeline([("scaler", StandardScaler(with_mean=False)),
                           ("clf", est)])
        skf = StratifiedKFold(n_splits=self.cv_folds, shuffle=True,
                              random_state=self.random_state)
        # OOF 概率 → 无信息泄漏的 metrics
        proba = cross_val_predict(pipe, X, y, cv=skf, method="predict_proba")[:, 1]
        pred = (proba >= 0.5).astype(int)

        auroc = float(roc_auc_score(y, proba))
        acc = float(accuracy_score(y, pred))
        f1  = float(f1_score(y, pred))

        # CV 结束后再用全量数据 refit 一次，**只为了拿稳定的
        # feature_importances_**（CV 折内的模型多个 importance 不好平均）；
        # 这一步的模型不再用于评估，所以不会有信息泄漏问题
        pipe.fit(X, y)
        clf = pipe.named_steps["clf"]
        importances: Dict[str, float] = {}
        if hasattr(clf, "feature_importances_"):
            for f, w in zip(feats, clf.feature_importances_):
                importances[f] = float(w)

        # V8 Pattern E — bootstrap CI for headline metrics + per-feature
        # importance.  Resample WITH replacement on the OOF predictions
        # for metric CI (fast, no refit); refit on bootstrap samples for
        # importance CI (slower but the only honest way for trees).
        ci_rows, metric_ci = _bootstrap_classifier_ci(
            X=X, y=y, proba=proba, pred=pred,
            pipe_factory=lambda rs: SKPipeline([
                ("scaler", StandardScaler(with_mean=False)),
                ("clf", self.model_factory(random_state=rs)),
            ]),
            feats=feats,
            n_bootstrap=self.n_bootstrap,
            ci_level=self.ci_level,
            base_seed=self.random_state,
        )

        metrics = {
            "auroc": auroc, "accuracy": acc, "f1": f1,
            "n": int(len(y)), "n_pos": int(y.sum()),
            "feature_importances": importances,
            # V8 Pattern E — uncertainty bounds for metrics.  Empty when
            # n_bootstrap <= 0.
            "metric_ci": metric_ci,
            "ci_level": self.ci_level,
            "n_bootstrap": self.n_bootstrap,
        }
        mj = Path(output_dir) / self.contract.output_files["metrics_json"]
        mj.write_text(__import__("json").dumps(metrics, ensure_ascii=False,
                                                  indent=2, default=str),
                      encoding="utf-8")

        pred_df = pd.DataFrame({
            id_col:        df_xy[id_col].values,
            "y_pred":      pred,
            "y_pred_proba": proba,
        })
        # Carry the TRUE label through so the downstream coder never has to
        # re-merge the original csv (and risk dropping it).  Exposed both
        # under a stable ``y_true`` name and under the target's original
        # column name when that name is known and not already taken.
        pred_df["y_true"] = y
        target_col = mapping.get("target_col")
        if (target_col and target_col not in pred_df.columns
                and target_col in df_xy.columns):
            pred_df[target_col] = df_xy[target_col].values
        pc = Path(output_dir) / self.contract.output_files["predictions_csv"]
        pred_df.to_csv(pc, index=False)

        # V8 Pattern E — per-feature CI table (always written; empty
        # body when n_bootstrap <= 0 so downstream code never branches).
        ci_path = Path(output_dir) / self.contract.output_files["ci_csv"]
        pd.DataFrame(ci_rows).to_csv(ci_path, index=False)

        return {"metrics_json": str(mj), "metrics_dict": metrics,
                "predictions_csv": str(pc),
                "ci_csv": str(ci_path), "ci_rows": ci_rows}


class RandomForestCVSolver(_TreeCVBase):
    contract = _make_contract("random_forest_cv", "Random Forest")
    model_factory = staticmethod(
        lambda random_state: RandomForestClassifier(
            n_estimators=200, max_depth=None, n_jobs=1,
            random_state=random_state),
    )


class HistGBCVSolver(_TreeCVBase):
    contract = _make_contract("hist_gbdt_cv", "HistGradientBoosting (sklearn)")
    model_factory = staticmethod(
        lambda random_state: HistGradientBoostingClassifier(
            max_iter=200, random_state=random_state),
    )


def get_random_forest_solver(**kw):
    return RandomForestCVSolver(**kw)


def get_hist_gbdt_solver(**kw):
    return HistGBCVSolver(**kw)


# Optional XGBoost adapter — kept inside the function so the module
# loads even when xgboost is not installed.
def get_xgboost_solver(**kw):
    try:
        from xgboost import XGBClassifier
    except Exception as e:
        raise RuntimeError(f"xgboost not available: {e}")

    class XGBCVSolver(_TreeCVBase):
        contract = _make_contract("xgboost_cv", "XGBoost")
        model_factory = staticmethod(
            lambda random_state: XGBClassifier(
                n_estimators=300, max_depth=6, learning_rate=0.1,
                eval_metric="logloss", random_state=random_state,
                use_label_encoder=False, n_jobs=1),
        )
    return XGBCVSolver(**kw)


def get_lightgbm_solver(**kw):
    try:
        from lightgbm import LGBMClassifier
    except Exception as e:
        raise RuntimeError(f"lightgbm not available: {e}")

    class LGBMCVSolver(_TreeCVBase):
        contract = _make_contract("lightgbm_cv", "LightGBM")
        model_factory = staticmethod(
            lambda random_state: LGBMClassifier(
                n_estimators=300, max_depth=-1, learning_rate=0.1,
                num_leaves=31, random_state=random_state, n_jobs=1,
                verbose=-1),
        )
    return LGBMCVSolver(**kw)


# ---------------------------------------------------------------------------
# Selftest — train RF + HistGBDT on the same self_harm task setup; both
# should beat the LR baseline (AUROC > 0.85 vs LR's 0.86).  This is a
# quick functional smoke that uses the bench data directly.
# ---------------------------------------------------------------------------
def selftest() -> Dict[str, Any]:
    """中文：4 个树 solver 的"出厂自检"（XGB / LGBM 装了才跑）。

    Fixture：400 行 × 5 个特征（seed=123），构造一个 *非线性* 目标：
        logits = f0·f1 + 0.5·f2 - 0.3·f3
        y      = 1[logits > median(logits)]
    特意有 f0·f1 交互项 → LR 难做，树模型应该能抓到。

    通过判定：RF / HistGBDT 在 5 折 CV 上的 AUROC ≥ 0.85
    （树模型在该非线性 fixture 上的下界）。XGBoost / LightGBM 只在
    安装时机会主义地跑，不影响主流程。
    """
    import tempfile

    rng = np.random.default_rng(123)
    n = 400
    X = rng.normal(size=(n, 5))
    # construct a target that depends non-linearly on the first 2 features
    logits = (X[:, 0] * X[:, 1]) + 0.5 * X[:, 2] - 0.3 * X[:, 3]
    y = (logits > np.median(logits)).astype(int)

    df = pd.DataFrame({f"f{i}": X[:, i] for i in range(5)})
    df["PatientID"] = [f"P{i}" for i in range(n)]
    df["target"] = y
    mapping = ColumnMapping({
        "id_col": "PatientID",
        "feature_columns": [f"f{i}" for i in range(5)],
        "target_col": "target",
    })

    diffs = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        rf = get_random_forest_solver()
        out_rf = rf.run(df=df, mapping=mapping, output_dir=tmp)
        if out_rf["metrics_dict"]["auroc"] < 0.85:
            diffs.append(f"RF AUROC < 0.85 on synthetic non-linear "
                         f"target: got {out_rf['metrics_dict']['auroc']:.3f}")

        hg = get_hist_gbdt_solver()
        out_hg = hg.run(df=df, mapping=mapping, output_dir=tmp)
        if out_hg["metrics_dict"]["auroc"] < 0.85:
            diffs.append(f"HistGBDT AUROC < 0.85 on synthetic non-linear "
                         f"target: got {out_hg['metrics_dict']['auroc']:.3f}")

        tested = ["random_forest_cv", "hist_gbdt_cv"]
        # Opportunistic XGBoost / LightGBM
        for name, getter in [("xgboost_cv",  get_xgboost_solver),
                             ("lightgbm_cv", get_lightgbm_solver)]:
            try:
                solver = getter()
            except RuntimeError:
                continue   # not installed → skip silently
            try:
                out_x = solver.run(df=df, mapping=mapping, output_dir=tmp)
                if out_x["metrics_dict"]["auroc"] < 0.85:
                    diffs.append(f"{name} AUROC < 0.85: "
                                 f"{out_x['metrics_dict']['auroc']:.3f}")
                tested.append(name)
            except Exception as e:
                diffs.append(f"{name} crashed: {type(e).__name__}: {e}")

    return {
        "ok": len(diffs) == 0,
        "summary": (f"{', '.join(tested)} pass AUROC >= 0.85 on synthetic "
                    "non-linear target" if not diffs
                    else f"{len(diffs)} mismatch(es)"),
        "details": {"diffs": diffs, "tested": tested},
    }
