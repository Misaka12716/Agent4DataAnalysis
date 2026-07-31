"""SVM-RBF classifier solver (F06 / Q13).

sklearn.svm.SVC(kernel='rbf', probability=True) + GridSearchCV over
C × gamma + 5-fold stratified CV.  Fixed seed.

中文说明
========
RBF 核 SVM + 网格搜索 + 5 折分层 CV。

Pipeline：``StandardScaler -> SVC(kernel='rbf', probability=True)``
（SVM 对特征尺度极敏感，**必须 scale**，否则大尺度特征主导 RBF
距离，C / gamma 全部失灵）

GridSearch 网格（默认）：
  - C     ∈ {0.1, 1, 10}    控制软间隔；越大越逼近硬间隔（更易过拟合）
  - gamma ∈ {'scale', 0.1, 1.0}
            'scale' = 1 / (n_features · X.var())；新版 sklearn 默认
            数值越大 → 决策边界越复杂、覆盖半径越小
  - 评分 metric = ``roc_auc``
  - 总共 3·3 = 9 个组合 × 5 折 = 45 次 fit；小数据上很快

输入约定
========
- ``id_col`` / ``feature_columns`` 必填
- ``target_col`` optional（同 LR），缺则用 ``external_label_csv``
- 静态参数：random_state / cv_folds / C_grid / gamma_grid

输出
====
- ``metrics.json``    {auroc, accuracy, best_params, n, n_pos}
- ``predictions.csv`` [id, y_pred, y_pred_proba]（OOF 概率，
  best_params 重新 cross_val_predict 出的，无信息泄漏）
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, roc_auc_score
from sklearn.model_selection import GridSearchCV, StratifiedKFold, cross_val_predict
from sklearn.pipeline import Pipeline as SKPipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVC

from ..contract import ColumnMapping, Role, RoleSpec, SolverContract


# Contract 说明：与 LR / 树模型 contract 同构
#   - feature_columns 这里 description 写 "the 8 numeric feature
#     columns" 是 wine 数据集的历史遗留；solver 实际不限列数
#   - C_grid / gamma_grid 可以由调用方在 static_params 里覆盖：
#     例如想跑更细的网格 C=[0.01, 0.1, 1, 10, 100]，直接传过来即可
CONTRACT = SolverContract(
    name="svm_rbf_classifier",
    capability="F06_supervised_classification",
    description=(
        "RBF-kernel SVM + GridSearchCV over C × gamma with 5-fold "
        "stratified CV.  Feature columns may be raw numeric predictors OR a "
        "molecular fingerprint / descriptor matrix produced upstream by "
        "morgan_fingerprint / molecular_descriptors.  Output: predictions.csv "
        "+ metrics.json (auroc, accuracy, best_params)."),
    roles={
        "id_col":         RoleSpec(Role.ID, "row identifier"),
        "feature_columns": RoleSpec(Role.NUMERIC_LIST,
                                      "numeric feature columns"),
        "target_col": RoleSpec(
            Role.BINARY_TARGET, "0/1 target (omit → external_label_csv)",
            optional=True),
    },
    static_params={
        "random_state": 42,
        "cv_folds": 5,
        "C_grid": [0.1, 1, 10],
        "gamma_grid": ["scale", 0.1, 1.0],
        "external_label_csv": None,
    },
    output_files={
        "metrics_json":     "metrics.json",
        "predictions_csv":  "predictions.csv",
    },
    output_kind={"metrics_json": "s", "predictions_csv": "t"},
)


class SvmRbfClassifierSolver:
    contract = CONTRACT

    def __init__(self, random_state: int = 42, cv_folds: int = 5,
                 C_grid: Optional[List[float]] = None,
                 gamma_grid: Optional[List[Any]] = None,
                 external_label_csv: Optional[str] = None,
                 external_label_col: str = "y_true"):
        """中文：

        :param random_state: 控制 CV split + SVC 内部随机性，可复现
        :param cv_folds:     CV 折数，默认 5
        :param C_grid:       软间隔代价；默认 [0.1, 1, 10]，跨 2 个量级
                             基本能锁定区间；想精调可以传 [0.5, 1, 5]
        :param gamma_grid:   RBF 核宽度；默认 ['scale', 0.1, 1.0]
                             - 'scale' = 1/(n_features · X.var())
                             - 数值越大决策面越"细"，越易过拟合小样本
        """
        self.random_state = random_state
        self.cv_folds = cv_folds
        self.C_grid = C_grid or [0.1, 1, 10]
        self.gamma_grid = gamma_grid or ["scale", 0.1, 1.0]
        self.external_label_csv = external_label_csv
        self.external_label_col = external_label_col

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
            output_dir: Path) -> Dict[str, Any]:
        id_col = mapping["id_col"]
        feats = mapping["feature_columns"]

        target_col = mapping.get("target_col")
        if target_col and target_col in df.columns:
            df_xy = df
            y = df_xy[target_col].astype(int).to_numpy()
        elif self.external_label_csv:
            label_df = pd.read_csv(self.external_label_csv)
            df_xy = df.merge(label_df, on=id_col, how="inner")
            y = df_xy[self.external_label_col].astype(int).to_numpy()
        else:
            raise ValueError("No target column resolved.")

        X = df_xy[feats].astype(float).to_numpy()

        # SVM 的 RBF 核对特征尺度极其敏感，scaler 不能省
        # probability=True 让 SVC 用 Platt scaling 出概率（多花一倍时间，
        # 但 AUROC 才能算）
        pipe = SKPipeline([
            ("scaler", StandardScaler()),
            ("svc", SVC(kernel="rbf", probability=True,
                         random_state=self.random_state)),
        ])
        param_grid = {
            "svc__C":     self.C_grid,
            "svc__gamma": self.gamma_grid,
        }
        skf = StratifiedKFold(n_splits=self.cv_folds, shuffle=True,
                              random_state=self.random_state)
        # GridSearchCV 用同一个 skf 选 best_params；scoring='roc_auc' →
        # 与最终评估指标一致，避免选出"acc 高但 AUC 一般"的参数
        gs = GridSearchCV(pipe, param_grid, cv=skf, scoring="roc_auc",
                          n_jobs=1, refit=True)
        gs.fit(X, y)

        best = gs.best_estimator_
        # 注意：best_estimator_ 是用全量数据 refit 的 → 不能直接用它
        # predict_proba 拿评估指标（会过拟合）。这里再做一次
        # cross_val_predict（用同一份 best pipeline / 同一个 skf）拿 OOF
        # 概率，metrics 才是无偏的
        proba = cross_val_predict(best, X, y, cv=skf, method="predict_proba")[:, 1]
        pred = (proba >= 0.5).astype(int)

        auroc = float(roc_auc_score(y, proba))
        acc = float(accuracy_score(y, pred))
        metrics = {
            "auroc": auroc,
            "accuracy": acc,
            "best_params": {k.replace("svc__", ""): v
                            for k, v in gs.best_params_.items()},
            "n": int(len(y)),
            "n_pos": int(y.sum()),
        }
        mj = Path(output_dir) / CONTRACT.output_files["metrics_json"]
        mj.write_text(__import__("json").dumps(metrics, ensure_ascii=False,
                                                  indent=2, default=str),
                      encoding="utf-8")

        pred_df = pd.DataFrame({
            id_col: df_xy[id_col].values,
            "y_pred": pred,
            "y_pred_proba": proba,
        })
        pc = Path(output_dir) / CONTRACT.output_files["predictions_csv"]
        pred_df.to_csv(pc, index=False)

        return {"metrics_json": str(mj),
                "metrics_dict": metrics,
                "predictions_csv": str(pc)}


def get_solver(random_state: int = 42,
               cv_folds: int = 5,
               external_label_csv: Optional[str] = None,
               external_label_col: str = "y_true"):
    return SvmRbfClassifierSolver(
        random_state=random_state,
        cv_folds=cv_folds,
        external_label_csv=external_label_csv,
        external_label_col=external_label_col,
    )


def selftest():
    """Two non-linear concentric clusters; RBF SVM should hit AUROC > 0.9.

    中文：fixture = 同心圆（seed=2026）
      - inner: 半径 ∈ [0, 1]，标签 0
      - outer: 半径 ∈ [2, 3]，标签 1
    线性分类器（LR）在这上面只能做到 ~0.5；RBF SVM 应该 ≥ 0.9。

    通过判定：5 折 CV 的 AUROC > 0.90（同心圆是 RBF SVM 的"教科书
    展示题"，跑不到 0.9 说明 pipeline 出 bug 了）。
    """
    import tempfile
    rng = np.random.default_rng(2026)
    n = 200
    r = rng.uniform(0, 1, n)
    theta = rng.uniform(0, 2 * np.pi, n)
    inner = np.column_stack([r * np.cos(theta), r * np.sin(theta)])
    r2 = rng.uniform(2, 3, n)
    outer = np.column_stack([r2 * np.cos(theta), r2 * np.sin(theta)])
    X = np.vstack([inner, outer])
    y = np.array([0] * n + [1] * n)
    df = pd.DataFrame({
        "PatientID": [f"P{i}" for i in range(2 * n)],
        "feature_0": X[:, 0],
        "feature_1": X[:, 1],
        "target":    y,
    })
    diffs = []
    with tempfile.TemporaryDirectory() as tmp:
        out = get_solver(random_state=42, cv_folds=5).run(
            df=df,
            mapping=ColumnMapping({"id_col": "PatientID",
                                     "feature_columns": ["feature_0", "feature_1"],
                                     "target_col": "target"}),
            output_dir=Path(tmp),
        )
        if out["metrics_dict"]["auroc"] < 0.90:
            diffs.append(f"SVM RBF AUROC < 0.9 on concentric circles: "
                         f"{out['metrics_dict']['auroc']:.3f}")
    return {"ok": len(diffs) == 0,
            "summary": ("RBF SVM AUROC > 0.9 on non-linear "
                        "concentric-circle fixture" if not diffs
                        else f"{len(diffs)} mismatch(es)"),
            "details": {"diffs": diffs,
                        "tested": ["svm_rbf_classifier"]}}
