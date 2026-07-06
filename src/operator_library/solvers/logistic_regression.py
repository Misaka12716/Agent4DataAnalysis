"""Logistic-regression classifier solver (F06 / Q10 / Q26).

Trains a binary classifier with 5-fold stratified CV (固定 seed=42) and
reports AUROC / AUPRC / F1.  Backed by sklearn — fully deterministic.

Why a custom solver: the csv-copied operators ``logistic_regression`` /
``logistic_regression_classifier_training`` *do* train an sklearn LR,
but they only return a model object, no metrics.  This solver wraps the
full train + 5-fold CV + threshold-based deliverables flow into one
authoritative implementation suitable for direct GT comparison.

Usage notes:
  - If the input csv has no label column, supply ``external_label_csv``
    pointing to a 2-column ``[id, y_true]`` csv (typical when the
    ground-truth label is held out in ``gt/labels.csv``).

中文说明
========
逻辑回归二分类 + 5 折分层交叉验证。

Pipeline：``StandardScaler -> LogisticRegression(max_iter=1000)``
评估：``StratifiedKFold(n_splits=5, shuffle=True, random_state=42)``
     ``cross_val_predict(method='predict_proba')`` → 出每行的 OOF 概率
判定阈值：proba ≥ 0.5 → y_pred = 1
指标：AUROC + AUPRC + F1（基于 OOF 预测，因此 *无信息泄漏*）

为什么 5 折？
=============
- **小样本（n < 200）**：5 折几乎没法用，每折验证集 < 40，AUC
  方差大；这种场景考虑 LeaveOneOut 或重复 5 折（CV repeats）
- **中等样本（200..2000）**：5 折是甜点；10 折更准但更慢
- **大样本（n > 5000）**：5 折足够稳，10 折收益边际递减

Stratified 是为了保证每折正负样本比例与总体一致，否则极端不平衡
（pos rate < 5%）时某些折的验证集可能全是负样本。

输入约定
========
- ``id_col``           必填：写进 predictions.csv
- ``feature_columns``  必填：数值 / 0-1 特征列
- ``target_col``       optional：在 df 内时直接用
- 否则用 ``external_label_csv``（``[id, y_true]``）merge

输出
====
- ``metrics_json``    = ``metrics.json``    {auroc, auprc, f1, n, n_pos}
- ``predictions_csv`` = ``predictions.csv`` [id, y_pred, y_pred_proba]
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import f1_score, roc_auc_score, average_precision_score
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from ..contract import ColumnMapping, Role, RoleSpec, SolverContract


def _bootstrap_lr_ci(*, X, y, proba, pred, feats, random_state,
                       n_bootstrap, ci_level):
    """V8 Pattern E — bootstrap CI for AUROC/AUPRC/F1 + per-feature
    standardized LR coefficients.  Returns
    ``(rows, metric_ci_dict, coef_ci_dict)``.

    Metric CI: resample OOF predictions WITH replacement; nondegenerate
    folds only.  Coef CI: refit on bootstrap samples (capped at 50
    refits to keep wall time bounded).
    """
    rows: list = []
    metric_ci: Dict[str, Any] = {}
    coef_ci: Dict[str, Any] = {}

    if n_bootstrap <= 0 or len(y) < 10:
        return rows, metric_ci, coef_ci

    rng = np.random.default_rng(random_state)
    n = len(y)
    alpha = (1.0 - ci_level) / 2.0

    aurocs = []
    auprcs = []
    f1s = []
    for _ in range(n_bootstrap):
        idx = rng.integers(0, n, n)
        yb = y[idx]
        if yb.sum() == 0 or yb.sum() == len(yb):
            continue
        try:
            aurocs.append(roc_auc_score(yb, proba[idx]))
        except Exception:
            pass
        try:
            auprcs.append(average_precision_score(yb, proba[idx]))
        except Exception:
            pass
        try:
            f1s.append(f1_score(yb, pred[idx]))
        except Exception:
            pass

    def _ci(arr):
        if not arr:
            return (float("nan"), float("nan"))
        return (float(np.quantile(arr, alpha)),
                float(np.quantile(arr, 1.0 - alpha)))

    if aurocs:
        lo, hi = _ci(aurocs); metric_ci["auroc"] = {"lower": lo, "upper": hi}
        rows.append({"feature": "__metric_auroc__", "estimate": float(np.mean(aurocs)),
                     "ci_lower": lo, "ci_upper": hi, "kind": "metric",
                     "n_boot": len(aurocs)})
    if auprcs:
        lo, hi = _ci(auprcs); metric_ci["auprc"] = {"lower": lo, "upper": hi}
        rows.append({"feature": "__metric_auprc__", "estimate": float(np.mean(auprcs)),
                     "ci_lower": lo, "ci_upper": hi, "kind": "metric",
                     "n_boot": len(auprcs)})
    if f1s:
        lo, hi = _ci(f1s); metric_ci["f1"] = {"lower": lo, "upper": hi}
        rows.append({"feature": "__metric_f1__", "estimate": float(np.mean(f1s)),
                     "ci_lower": lo, "ci_upper": hi, "kind": "metric",
                     "n_boot": len(f1s)})

    # Coefficient CI — refit on bootstrap samples (cap to 50).
    n_refit = min(n_bootstrap, 50)
    boot_coefs: Dict[str, list] = {f: [] for f in feats}
    for _ in range(n_refit):
        idx = rng.integers(0, n, n)
        yb = y[idx]
        if yb.sum() == 0 or yb.sum() == len(yb):
            continue
        try:
            pipe_b = Pipeline([
                ("scaler", StandardScaler()),
                ("lr", LogisticRegression(max_iter=1000,
                                           random_state=int(
                                               rng.integers(0, 2**31 - 1)))),
            ])
            pipe_b.fit(X[idx], yb)
            coefs = pipe_b.named_steps["lr"].coef_.ravel()
            for f, w in zip(feats, coefs):
                boot_coefs[f].append(float(w))
        except Exception:
            continue

    for f in feats:
        arr = boot_coefs.get(f) or []
        lo, hi = _ci(arr)
        coef_ci[f] = {"lower": lo, "upper": hi,
                       "estimate": float(np.mean(arr)) if arr else float("nan")}
        rows.append({"feature": f,
                      "estimate": float(np.mean(arr)) if arr else float("nan"),
                      "ci_lower": lo, "ci_upper": hi,
                      "kind": "standardized_coef",
                      "n_boot": len(arr)})

    return rows, metric_ci, coef_ci


# Contract 说明：
#   - id_col / feature_columns 必填
#   - target_col optional：当数据 df 自带 0/1 标签时使用；否则
#     调用方在 static_params 里塞 external_label_csv（path -> 2-col csv）
#   - static_params:
#       random_state          固定 seed，保证可复现
#       cv_folds              CV 折数（默认 5；样本 <100 慎重）
#       external_label_csv    外部标签文件路径（与 id_col 做 merge）
CONTRACT = SolverContract(
    name="logistic_regression_cv",
    capability="F06_supervised_classification",
    description=(
        "Logistic regression for a binary outcome with 5-fold stratified "
        "CV.  Feature columns may be raw numeric predictors OR a molecular "
        "fingerprint / descriptor matrix produced upstream by "
        "morgan_fingerprint / molecular_descriptors.  Output: metrics.json "
        "(auroc/auprc/f1), predictions.csv, ci.csv (bootstrap 95%CI on "
        "metrics + standardized coefficients)."
    ),
    roles={
        "id_col": RoleSpec(Role.ID, "row identifier"),
        "feature_columns": RoleSpec(
            Role.NUMERIC_LIST, "numeric / 0-1 predictor columns",
        ),
        "target_col": RoleSpec(
            Role.BINARY_TARGET,
            "0/1 outcome (omit → pass external_label_csv in static_params)",
            optional=True,
        ),
    },
    static_params={
        "random_state": 42,
        "cv_folds": 5,
        "external_label_csv": None,
        # V8 Pattern E — bootstrap CI for headline metrics +
        # standardized coefficients.
        "n_bootstrap": 200,
        "ci_level": 0.95,
    },
    output_files={
        "metrics_json":     "metrics.json",
        "predictions_csv":  "predictions.csv",
        "ci_csv":           "ci.csv",
    },
    output_kind={"metrics_json": "s", "predictions_csv": "t",
                  "ci_csv": "s"},
)


class LogisticRegressionCVSolver:
    contract = CONTRACT

    def __init__(self, random_state: int = 42, cv_folds: int = 5,
                 external_label_csv: Optional[str] = None,
                 external_label_col: str = "y_true",
                 n_bootstrap: int = 200, ci_level: float = 0.95):
        """中文：

        :param random_state: 固定 seed（默认 42）。同时控制 CV 划分
                             和 LR 内部初始化，保证完全可复现。
        :param cv_folds:     CV 折数。默认 5：样本 200..2000 上甜点；
                             小样本（<100）建议改成 3，否则方差太大
                             甚至有的折 pos=0；大样本（>5000）保持 5
                             即可。
        :param external_label_csv: 当 df 自带 target 时填 None；否则
                             给一个 ``[id_col, y_true]`` 的 csv 路径，
                             solver 会按 id_col 内连接。
        :param external_label_col: 外部 csv 里 y 列的列名，默认 "y_true"。
        """
        self.random_state = random_state
        self.cv_folds = cv_folds
        self.external_label_csv = external_label_csv
        self.external_label_col = external_label_col
        self.n_bootstrap = int(n_bootstrap)
        self.ci_level = float(ci_level)

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
            output_dir: Path) -> Dict[str, Any]:
        id_col = mapping["id_col"]
        feats = list(mapping["feature_columns"])

        # 解析 y：优先用 df 内的 target_col，没有再 fallback 到外部 csv
        # inner join 保证 X / y 行数一致（df 里多余的 id 会被丢掉）
        target_col = mapping.get("target_col")
        if target_col and target_col in df.columns:
            y = df[target_col].astype(int).to_numpy()
            df_xy = df
        elif self.external_label_csv:
            label_df = pd.read_csv(self.external_label_csv)
            # join on id
            df_xy = df.merge(label_df, on=id_col, how="inner")
            y = df_xy[self.external_label_col].astype(int).to_numpy()
        else:
            raise ValueError("No target column resolved and no "
                             "external_label_csv provided.")

        X = df_xy[feats].astype(float).to_numpy()

        # Pipeline = StandardScaler -> LR；scaler 在 CV 内每折独立
        # 拟合（cross_val_predict 自动处理），不会把验证集统计量泄漏到
        # 训练集。max_iter=1000 是为了 LBFGS 在小数据上稳定收敛
        pipe = Pipeline([
            ("scaler", StandardScaler()),
            ("lr", LogisticRegression(max_iter=1000,
                                      random_state=self.random_state)),
        ])
        # StratifiedKFold：保持每折正负比例一致；shuffle 后再划分，
        # 避免数据天然按某顺序排列时折内分布偏倚
        skf = StratifiedKFold(n_splits=self.cv_folds, shuffle=True,
                              random_state=self.random_state)

        # cross_val_predict + method='predict_proba'：每个样本只在
        # 它**作为验证集时**被预测一次，最后拼成与 y 同长的 OOF 概率
        # → 拿 OOF 概率算 AUROC / AUPRC / F1 没有信息泄漏，可代表
        # 模型在新数据上的表现
        proba = cross_val_predict(pipe, X, y, cv=skf, method="predict_proba")[:, 1]
        pred = (proba >= 0.5).astype(int)

        auroc = float(roc_auc_score(y, proba))
        auprc = float(average_precision_score(y, proba))
        f1 = float(f1_score(y, pred))

        # V8 Pattern E — bootstrap CI on AUROC / AUPRC / F1 + per-feature
        # standardized coefficient.  Same fast OOF-resample strategy for
        # metric CI; refit-based for coefficients.
        ci_rows, metric_ci, coef_ci = _bootstrap_lr_ci(
            X=X, y=y, proba=proba, pred=pred, feats=feats,
            random_state=self.random_state,
            n_bootstrap=self.n_bootstrap,
            ci_level=self.ci_level,
        )

        metrics = {"auroc": auroc, "auprc": auprc, "f1": f1,
                   "n": int(len(y)), "n_pos": int(y.sum()),
                   "metric_ci": metric_ci,
                   "coef_ci": coef_ci,
                   "ci_level": self.ci_level,
                   "n_bootstrap": self.n_bootstrap}
        mj = Path(output_dir) / CONTRACT.output_files["metrics_json"]
        mj.write_text(__import__("json").dumps(metrics, ensure_ascii=False,
                                                 indent=2, default=str),
                      encoding="utf-8")

        pred_df = pd.DataFrame({
            id_col:        df_xy[id_col].values,
            "y_pred":      pred,
            "y_pred_proba": proba,
        })
        pc = Path(output_dir) / CONTRACT.output_files["predictions_csv"]
        pred_df.to_csv(pc, index=False)

        ci_path = Path(output_dir) / CONTRACT.output_files["ci_csv"]
        pd.DataFrame(ci_rows).to_csv(ci_path, index=False)

        return {"metrics_json": str(mj), "metrics_dict": metrics,
                "predictions_csv": str(pc),
                "ci_csv": str(ci_path), "ci_rows": ci_rows}


def get_solver(random_state: int = 42, cv_folds: int = 5,
               external_label_csv: Optional[str] = None,
               external_label_col: str = "y_true",
               n_bootstrap: int = 200, ci_level: float = 0.95):
    return LogisticRegressionCVSolver(
        random_state=random_state, cv_folds=cv_folds,
        external_label_csv=external_label_csv,
        external_label_col=external_label_col,
        n_bootstrap=n_bootstrap, ci_level=ci_level,
    )


def selftest():
    """Linearly-separable synthetic data; LR should hit AUROC > 0.95.

    中文：fixture = 300 行 × 3 个数值特征（seed=2026），用线性 logits
    生成 y。

    生成式：logits = 1.5·f0 - 1.0·f1 + 0.5·f2; y = 1[logits > 0]
    → 数据线性可分，LR 应该几乎完美预测。

    通过判定：5 折 CV 的 AUROC > 0.95（线性可分场景的下界，
    实际通常 > 0.99）。
    """
    import tempfile
    rng = np.random.default_rng(2026)
    n = 300
    X = rng.normal(size=(n, 3))
    logits = 1.5 * X[:, 0] - 1.0 * X[:, 1] + 0.5 * X[:, 2]
    y = (logits > 0).astype(int)
    df = pd.DataFrame({"PatientID": [f"P{i}" for i in range(n)],
                       "f0": X[:, 0], "f1": X[:, 1], "f2": X[:, 2],
                       "target": y})
    diffs = []
    with tempfile.TemporaryDirectory() as tmp:
        out = get_solver(random_state=42, cv_folds=5).run(
            df=df,
            mapping=ColumnMapping({"id_col": "PatientID",
                                     "feature_columns": ["f0", "f1", "f2"],
                                     "target_col": "target"}),
            output_dir=Path(tmp),
        )
        if out["metrics_dict"]["auroc"] < 0.95:
            diffs.append(f"AUROC < 0.95 on linearly-separable fixture: "
                         f"{out['metrics_dict']['auroc']:.3f}")
    return {"ok": len(diffs) == 0,
            "summary": ("LR AUROC > 0.95 on linearly-separable fixture"
                        if not diffs else f"{len(diffs)} mismatch(es)"),
            "details": {"diffs": diffs,
                        "tested": ["logistic_regression_cv"]}}
