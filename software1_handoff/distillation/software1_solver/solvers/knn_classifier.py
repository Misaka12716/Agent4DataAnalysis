"""KNN classifier solver (F06 / Q14).

GridSearchCV over K ∈ {1..15} odd, 5-fold stratified CV; refits with
best K on the full training set; reports test_accuracy on a held-out
80/20 split.  Fixed seed.

中文说明
========
KNN 多分类 + K 网格搜索 + 80/20 留出评估。

流程：
  1. 80/20 stratified split → 训练 / 测试
  2. 训练集上 5 折 stratified CV，K ∈ {1, 3, 5, 7, 9, 11, 13, 15}
     - 全部用奇数：避免 2-class 投票打平
  3. best_k 在训练集 refit，预测 hold-out 测试集得 ``test_accuracy``

注意：和 LR / 树 / SVM 的 OOF 路线 **不一样**！
- KNN 在小样本上对 K 选择极敏感，cross_val_predict 出 OOF 概率不太
  公平（因为 best_k 是在全量上选的）；这里改用 hold-out 测试集评估
- 如果想做 OOF 风格，需要改成 nested CV，代价更高

Pipeline：``StandardScaler -> KNeighborsClassifier``
（KNN 也对尺度敏感，必须 scale）

输入约定
========
- ``id_col`` / ``feature_columns`` 必填
- ``target_col`` optional：description 写 NUMERIC_TARGET 是为了让
  rule mapper 在列名像 "target/y/label" 时优先匹配
- 静态参数：random_state / cv_folds / k_grid / test_size

输出
====
- ``metrics.json``    {best_k, cv_accuracy, test_accuracy, n_train, n_test}
- ``predictions.csv`` 仅 **测试集** 的 [id, y_pred]
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score
from sklearn.model_selection import GridSearchCV, StratifiedKFold, train_test_split
from sklearn.neighbors import KNeighborsClassifier
from sklearn.pipeline import Pipeline as SKPipeline
from sklearn.preprocessing import StandardScaler

from ..contract import ColumnMapping, Role, RoleSpec, SolverContract


# Contract 说明：
#   - target_col 写 NUMERIC_TARGET 而不是 BINARY_TARGET，是因为 KNN
#     天然多分类；rule mapper 在列名像 target/y/label 时才会匹配
#   - static_params.k_grid 默认全奇数：避免 2-class 投票打平
#   - static_params.test_size：留出测试集占比，默认 0.2 是经典 80/20
CONTRACT = SolverContract(
    name="knn_k_selection",
    capability="F06_supervised_classification",
    description=(
        "KNN classifier with K selected by 5-fold CV in {1..15}, "
        "evaluated on an 80/20 stratified split.  Output "
        "predictions.csv (test set) + metrics.json (best_k, "
        "cv_accuracy, test_accuracy)."),
    roles={
        "id_col":          RoleSpec(Role.ID, "patient identifier"),
        "feature_columns": RoleSpec(Role.NUMERIC_LIST,
                                     "numeric feature columns"),
        "target_col":      RoleSpec(Role.NUMERIC_TARGET,
                                     "multi-class target column "
                                     "(only matched when col name "
                                     "looks like target/y/label)",
                                     optional=True),
    },
    static_params={
        "random_state": 42,
        "cv_folds": 5,
        "k_grid": [1, 3, 5, 7, 9, 11, 13, 15],
        "test_size": 0.2,
        "external_label_csv": None,
    },
    output_files={
        "metrics_json":     "metrics.json",
        "predictions_csv":  "predictions.csv",
    },
)


class KnnKSelectionSolver:
    contract = CONTRACT

    def __init__(self, random_state: int = 42, cv_folds: int = 5,
                 k_grid: Optional[List[int]] = None,
                 test_size: float = 0.2,
                 external_label_csv: Optional[str] = None,
                 external_label_col: str = "y_true"):
        """中文：

        :param random_state: 控制 train_test_split + CV split，可复现
        :param cv_folds:     CV 折数，默认 5
        :param k_grid:       K 候选值；全奇数避免投票打平。默认覆盖
                             1..15，对中等样本（200..2000）够用；样本
                             更大可以加到 21、25、31
        :param test_size:    hold-out 测试集占比，默认 0.2
        """
        self.random_state = random_state
        self.cv_folds = cv_folds
        self.k_grid = k_grid or [1, 3, 5, 7, 9, 11, 13, 15]
        self.test_size = test_size
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
        ids = df_xy[id_col].values

        # 80/20 stratified split：保留各类比例不变，避免类不平衡时
        # 测试集出现 0 个少数类样本
        X_tr, X_te, y_tr, y_te, id_tr, id_te = train_test_split(
            X, y, ids, test_size=self.test_size,
            random_state=self.random_state, stratify=y,
        )

        # KNN 用欧氏距离，所以 scaling 必不可少（与 SVM 同理）
        pipe = SKPipeline([
            ("scaler", StandardScaler()),
            ("knn", KNeighborsClassifier()),
        ])
        param_grid = {"knn__n_neighbors": self.k_grid}
        skf = StratifiedKFold(n_splits=self.cv_folds, shuffle=True,
                              random_state=self.random_state)
        # scoring='accuracy'：KNN 多分类场景下默认指标；二分类想要
        # AUROC 可以让上层换 scoring='roc_auc'
        gs = GridSearchCV(pipe, param_grid, cv=skf, scoring="accuracy",
                          n_jobs=1, refit=True)
        gs.fit(X_tr, y_tr)

        best_k = int(gs.best_params_["knn__n_neighbors"])
        # cv_accuracy = best_score_ 是训练集 5 折 CV 的均值（**含一定
        # 选择偏倚**，因为 K 是在这上面选的）；test_accuracy 才是真正
        # 的泛化估计
        cv_accuracy = float(gs.best_score_)
        y_pred = gs.predict(X_te)
        test_accuracy = float(accuracy_score(y_te, y_pred))

        metrics = {
            "best_k": best_k,
            "cv_accuracy": cv_accuracy,
            "test_accuracy": test_accuracy,
            "n_train": int(len(y_tr)),
            "n_test": int(len(y_te)),
        }
        mj = Path(output_dir) / CONTRACT.output_files["metrics_json"]
        mj.write_text(__import__("json").dumps(metrics, ensure_ascii=False,
                                                  indent=2, default=str),
                      encoding="utf-8")

        pred_df = pd.DataFrame({
            id_col:    id_te,
            "y_pred":  y_pred.astype(int),
        })
        pc = Path(output_dir) / CONTRACT.output_files["predictions_csv"]
        pred_df.to_csv(pc, index=False)

        return {"metrics_json": str(mj),
                "metrics_dict": metrics,
                "predictions_csv": str(pc)}


def get_solver(random_state: int = 42, cv_folds: int = 5,
               external_label_csv: Optional[str] = None,
               external_label_col: str = "y_true"):
    return KnnKSelectionSolver(
        random_state=random_state, cv_folds=cv_folds,
        external_label_csv=external_label_csv,
        external_label_col=external_label_col,
    )


def selftest():
    """3-class iris-style fixture; CV accuracy should be > 0.85.

    中文：fixture = sklearn make_classification 生成的 3 分类 300 行
    × 4 特征（class_sep=2.0，相对好分），seed=42。

    通过判定：5 折 CV 的 best K 下 cv_accuracy > 0.85（KNN 在
    "三个分得开的高斯团"上的下界）。
    """
    import tempfile
    from sklearn.datasets import make_classification
    X, y = make_classification(n_samples=300, n_features=4, n_informative=4,
                                n_redundant=0, n_repeated=0,
                                n_classes=3, n_clusters_per_class=1,
                                class_sep=2.0,
                                random_state=42)
    df = pd.DataFrame({"PatientID": [f"P{i}" for i in range(300)],
                       "f0": X[:, 0], "f1": X[:, 1],
                       "f2": X[:, 2], "f3": X[:, 3],
                       "target": y})
    diffs = []
    with tempfile.TemporaryDirectory() as tmp:
        out = get_solver(random_state=42, cv_folds=5).run(
            df=df,
            mapping=ColumnMapping({"id_col": "PatientID",
                                     "feature_columns": ["f0", "f1", "f2", "f3"],
                                     "target_col": "target"}),
            output_dir=Path(tmp),
        )
        if out["metrics_dict"]["cv_accuracy"] < 0.85:
            diffs.append(f"KNN CV acc < 0.85 on 3-class fixture: "
                         f"{out['metrics_dict']['cv_accuracy']:.3f}")
    return {"ok": len(diffs) == 0,
            "summary": ("KNN CV accuracy > 0.85 on 3-class synthetic"
                        if not diffs else f"{len(diffs)} mismatch(es)"),
            "details": {"diffs": diffs,
                        "tested": ["knn_k_selection"]}}
