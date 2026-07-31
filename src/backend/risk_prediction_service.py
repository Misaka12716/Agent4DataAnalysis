# backend/risk_prediction_service.py
# 精神疾病风险预测 — 模型训练 + 预测 + 评估

import json
import os
import pickle
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    f1_score,
    roc_auc_score,
    roc_curve,
    confusion_matrix,
    precision_score,
    recall_score,
)
from sklearn.model_selection import train_test_split

from utils.mysql_utils import mysql_handler
from db.risk_schema import TABLE_RISK_MODELS, TABLE_PREDICTIONS

SUPPORTED_TASKS = ["relapse", "self_harm", "adverse_reaction"]
SUPPORTED_MODEL_TYPES = {
    "LogisticRegression": LogisticRegression,
    "RandomForest": RandomForestClassifier,
}

RISK_LEVELS = [
    (0.0, 0.25, "low"),
    (0.25, 0.50, "medium"),
    (0.50, 0.75, "high"),
    (0.75, 1.01, "critical"),
]
# 注意：以上四分位分层是工程惯例，不是文献验证的临床风险分层阈值。TRIPOD/PROBAST 均要求
# 预测模型报告校准曲线并在目标人群中重新标定分层阈值；本系统未完成该标定，
# risk_level 仅供研究/辅助分诊参考，禁止作为处置/转诊的唯一依据（另见 nice_self_harm_2022）。

MODEL_STORAGE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "..", "workspace", "models")


def _user_model_dir(owner_user_id: int) -> str:
    """每位用户的模型文件独立目录：workspace/models/user_{uid}/"""
    path = os.path.join(MODEL_STORAGE_DIR, f"user_{int(owner_user_id)}")
    os.makedirs(path, exist_ok=True)
    return path

# 批量预测从患者表拉取时仅这些列可用（与 patient_schema.ALLOWED_PATIENT_FIELDS 一致）
PATIENT_TABLE_FEATURES = {
    "patient_id", "age", "gender", "diagnosis",
    "admission_date", "discharge_date",
    "HAMD_total", "HAMA_total", "PHQ9_total",
    "disease_duration_years",
    "medication", "outcome", "relapse",
}


def _get_patient_table_columns() -> set:
    """读取患者表现有列名（MySQL SHOW COLUMNS / SQLite 兼容）。"""
    from db.patient_schema import TABLE_PATIENTS

    cols = mysql_handler.get_table_columns(TABLE_PATIENTS)
    if cols:
        return cols
    return set(PATIENT_TABLE_FEATURES)


def _split_features_for_patient_table(features: List[str]) -> Tuple[List[str], List[str]]:
    """将模型特征拆分为患者表可查询列与缺失列。"""
    table_cols = _get_patient_table_columns()
    available = [f for f in features if f in table_cols and f != "patient_id"]
    missing = [f for f in features if f not in table_cols]
    return available, missing


def _patient_row_for_model(row: Dict[str, Any], features: List[str]) -> Dict[str, Any]:
    """用患者表行填充模型特征；缺失列以 0 填补。"""
    data = dict(row)
    for feat in features:
        if feat not in data or data[feat] is None:
            data[feat] = 0
    return data


def _risk_methodology(task_type: str = "", model_type: str = "") -> dict:
    from backend.clinical_evidence import methodology

    extra = ["hanley_mcneil_1982"]
    if model_type == "RandomForest":
        extra.append("breiman_2001")
    if model_type == "LogisticRegression":
        extra.append("cox_1958")
    if task_type == "self_harm":
        extra.append("nice_self_harm_2022")
    return methodology(
        "risk_prediction",
        extra,
        caveat=(
            "风险预测仅用于临床辅助分析和研究分层；未完成外部验证、校准和净获益评估前，"
            "不得作为诊断、出院、转诊或危机处置的唯一依据。"
        ),
    )


def _ensure_tables() -> Tuple[bool, Optional[str]]:
    try:
        from backend.clinical_owner import migrate_owner_column, TABLES_WITH_OWNER_COLUMN

        for table, ddl_ref in [
            (TABLE_RISK_MODELS, "RISK_MODEL_TABLE_DDL"),
            (TABLE_PREDICTIONS, "PREDICTION_TABLE_DDL"),
        ]:
            if not mysql_handler._check_table_exists(table):
                from db.risk_schema import RISK_MODEL_TABLE_DDL, PREDICTION_TABLE_DDL
                ddl = RISK_MODEL_TABLE_DDL if table == TABLE_RISK_MODELS else PREDICTION_TABLE_DDL
                affected, err = mysql_handler.execute(ddl)
                if err:
                    return False, f"创建表 {table} 失败: {err}"
        for table in TABLES_WITH_OWNER_COLUMN[:2]:
            ok, err = migrate_owner_column(table)
            if not ok:
                return False, err
        return True, None
    except Exception as e:
        return False, str(e)


def _determine_risk_level(score: float) -> str:
    for low, high, level in RISK_LEVELS:
        if low <= score < high:
            return level
    return "critical"


def _get_feature_contributions(model, features: List[str], sample: np.ndarray) -> list:
    """获取特征贡献度（仅 RandomForest 支持 feature_importances_）。"""
    if hasattr(model, "feature_importances_"):
        importances = model.feature_importances_
        return [
            {"feature": f, "importance": round(float(v), 4)}
            for f, v in zip(features, importances)
        ]
    # LogisticRegression: 使用系数绝对值作为近似贡献
    if hasattr(model, "coef_"):
        coef = model.coef_[0] if len(model.coef_.shape) > 1 else model.coef_
        return [
            {"feature": f, "coef": round(float(v), 4)}
            for f, v in zip(features, coef)
        ]
    return []


def train_model(
    task_type: str,
    training_data: List[Dict],
    features: List[str],
    label: str,
    model_type: str = "RandomForest",
    model_params: Optional[Dict] = None,
    owner_user_id: Optional[int] = None,
) -> Tuple[Optional[dict], Optional[str]]:
    """
    训练风险预测模型。
    training_data: [{feature1: val, ..., label: val}, ...]
    return: {model_id, metrics, features}
    """
    ok, err = _ensure_tables()
    if not ok:
        return None, err
    if not owner_user_id or int(owner_user_id) <= 0:
        return None, "需要登录后才能训练个人风险模型"

    if task_type not in SUPPORTED_TASKS:
        return None, f"task_type 必须为: {SUPPORTED_TASKS}"
    if model_type not in SUPPORTED_MODEL_TYPES:
        return None, f"model_type 必须为: {list(SUPPORTED_MODEL_TYPES.keys())}"
    if len(training_data) < 20:
        return None, "训练数据至少需要 20 条"
    if not features or label not in training_data[0]:
        return None, "features 或 label 无效"

    unavailable = [f for f in features if f not in training_data[0]]
    if unavailable:
        return None, f"训练数据缺少特征列: {unavailable}"

    df = pd.DataFrame(training_data)
    X = df[features].fillna(0).values
    y = df[label].values

    # 检查类别平衡
    unique_labels = np.unique(y)
    if len(unique_labels) < 2:
        return None, "标签只有单一类别，无法训练"

    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y if len(unique_labels) == 2 else None)

    # 训练模型
    model_cls = SUPPORTED_MODEL_TYPES[model_type]
    params = model_params or {}
    if model_type == "RandomForest":
        params.setdefault("n_estimators", 100)
        params.setdefault("random_state", 42)
    elif model_type == "LogisticRegression":
        params.setdefault("max_iter", 1000)
        params.setdefault("random_state", 42)

    model = model_cls(**params)
    model.fit(X_train, y_train)

    # 评估
    y_pred = model.predict(X_test)
    metrics = {
        "accuracy": round(float(accuracy_score(y_test, y_pred)), 4),
        "precision": round(float(precision_score(y_test, y_pred, average="weighted", zero_division=0)), 4),
        "recall": round(float(recall_score(y_test, y_pred, average="weighted", zero_division=0)), 4),
        "f1": round(float(f1_score(y_test, y_pred, average="weighted", zero_division=0)), 4),
        "train_size": len(X_train),
        "test_size": len(X_test),
    }

    # AUC（仅二分类）
    if len(unique_labels) == 2:
        try:
            y_proba = model.predict_proba(X_test)[:, 1]
            metrics["auc"] = round(float(roc_auc_score(y_test, y_proba)), 4)
        except Exception:
            metrics["auc"] = None

    # 保存模型文件（按用户隔离目录）
    uid = int(owner_user_id)
    model_name = f"{task_type}_{model_type}_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}"
    file_name = f"{model_name}.pkl"
    file_path = os.path.join(_user_model_dir(uid), file_name)
    with open(file_path, "wb") as f:
        pickle.dump(model, f)

    # 入库
    insert_data = {
        "model_name": model_name,
        "task_type": task_type,
        "model_type": model_type,
        "features": json.dumps(features, ensure_ascii=False),
        "metrics": json.dumps(metrics, ensure_ascii=False),
        "params": json.dumps(params, ensure_ascii=False),
        "file_path": file_path,
        "owner_user_id": uid,
    }
    _, model_id, err = mysql_handler.insert(TABLE_RISK_MODELS, insert_data)
    if err:
        return None, f"保存模型记录失败: {err}"

    # 同步到资源管理模型库
    try:
        from backend.model_registry_service import register_model
        register_model(
            user_id=uid,
            model_name=model_name,
            file_path=file_path,
            metadata={
                "task_type": task_type,
                "model_type": model_type,
                "features": features,
                "metrics": metrics,
                "params": params,
                "framework": "sklearn",
            },
            source="clinical_risk",
            source_ref_id=int(model_id) if model_id is not None else None,
        )
    except Exception:
        pass  # 同步失败不影响训练主流程

    return {
        "model_id": model_id,
        "model_name": model_name,
        "metrics": metrics,
        "features": features,
        "file_path": file_path,
        "methodology": _risk_methodology(task_type, model_type),
        "validation_status": {
            "internal_split": "train_test_split(test_size=0.2, random_state=42)",
            "external_validation": False,
            "calibration_reported": False,
            "intended_use": "decision_support_only",
        },
    }, None


def _load_model(
    model_id: int,
    owner_user_id: Optional[int] = None,
) -> Tuple[Optional[Any], Optional[dict], Optional[str]]:
    """加载模型对象和元数据；仅允许访问当前用户拥有的模型。"""
    sql = f"SELECT * FROM {TABLE_RISK_MODELS} WHERE id = %s"
    params: List[Any] = [model_id]
    if owner_user_id:
        sql += " AND owner_user_id = %s"
        params.append(int(owner_user_id))
    rows, err = mysql_handler.query(sql, tuple(params))
    if err:
        return None, None, f"查询模型失败: {err}"
    if not rows:
        return None, None, "模型不存在或无权访问"

    meta = rows[0]
    file_path = meta.get("file_path", "")
    if not file_path or not os.path.exists(file_path):
        return None, meta, f"模型文件不存在: {file_path}"

    try:
        with open(file_path, "rb") as f:
            model = pickle.load(f)
        return model, meta, None
    except Exception as e:
        return None, meta, f"加载模型文件失败: {e}"


def predict_risk(
    model_id: int,
    patient_data: Dict[str, Any],
    owner_user_id: Optional[int] = None,
) -> Tuple[Optional[dict], Optional[str]]:
    """
    单例风险预测。
    return: {risk_score, risk_level, prediction_label, feature_contributions}
    """
    model, meta, err = _load_model(model_id, owner_user_id=owner_user_id)
    if err:
        # 演示模型无 pkl 时：基于量表启发式评分
        if meta and not (meta.get("file_path") or "").strip():
            hamd = float(patient_data.get("HAMD_total") or patient_data.get("hamd") or 0)
            phq9 = float(patient_data.get("PHQ9_total") or patient_data.get("phq9") or 0)
            score = min(0.95, max(0.05, (hamd / 52.0 * 0.5 + phq9 / 27.0 * 0.5)))
            level = _determine_risk_level(score)
            return {
                "risk_score": round(score, 4),
                "risk_level": level,
                "prediction_label": 1 if score >= 0.5 else 0,
                "feature_contributions": [
                    {"feature": "HAMD_total", "importance": round(hamd / 52.0, 4)},
                    {"feature": "PHQ9_total", "importance": round(phq9 / 27.0, 4)},
                ],
                "mode": "heuristic_demo",
                "methodology": _risk_methodology(str(meta.get("task_type") or ""), str(meta.get("model_type") or "")),
                "validation_status": {
                    "external_validation": False,
                    "calibration_reported": False,
                    "intended_use": "demo_only_not_for_clinical_disposition",
                },
            }, None
        return None, err

    features = json.loads(meta["features"]) if isinstance(meta["features"], str) else meta["features"]
    X = np.array([[patient_data.get(f, 0) for f in features]], dtype=float)

    proba = model.predict_proba(X)[0]
    # 取正类概率（类别 1 或最大概率）
    if len(proba) > 1:
        risk_score = round(float(proba[1]), 4)
        pred_label = int(model.classes_[1]) if len(model.classes_) > 1 else int(model.predict(X)[0])
    else:
        risk_score = round(float(proba[0]), 4)
        pred_label = int(model.predict(X)[0])

    risk_level = _determine_risk_level(risk_score)
    contributions = _get_feature_contributions(model, features, X[0])

    # 保存预测记录（按用户隔离）
    pred_row = {
        "model_id": model_id,
        "patient_id": patient_data.get("patient_id", "unknown"),
        "risk_score": risk_score,
        "risk_level": risk_level,
        "prediction_label": pred_label,
        "feature_contributions": json.dumps(contributions, ensure_ascii=False),
    }
    if owner_user_id:
        pred_row["owner_user_id"] = int(owner_user_id)
    _, _, save_err = mysql_handler.insert(TABLE_PREDICTIONS, pred_row)
    if save_err:
        pass  # 非致命

    return {
        "risk_score": risk_score,
        "risk_level": risk_level,
        "prediction_label": pred_label,
        "feature_contributions": contributions,
        "methodology": _risk_methodology(str(meta.get("task_type") or ""), str(meta.get("model_type") or "")),
        "validation_status": {
            "external_validation": False,
            "calibration_reported": False,
            "intended_use": "decision_support_only",
        },
    }, None


def batch_predict(
    model_id: int,
    cohort_patient_ids: List[str],
    owner_user_id: Optional[int] = None,
) -> Tuple[Optional[dict], Optional[str]]:
    """批量预测。从患者表拉取数据后逐条预测。"""
    from db.patient_schema import TABLE_PATIENTS
    from backend.patient_query_service import _migrate_patient_columns

    _migrate_patient_columns()
    model, meta, err = _load_model(model_id, owner_user_id=owner_user_id)
    if err:
        return None, err

    features = json.loads(meta["features"]) if isinstance(meta["features"], str) else meta["features"]
    if not cohort_patient_ids:
        return None, "cohort_patient_ids 不能为空"

    table_cols, missing_features = _split_features_for_patient_table(features)
    select_cols = ["patient_id"] + table_cols
    placeholders = ", ".join(["%s"] * len(cohort_patient_ids))
    params = list(cohort_patient_ids)
    owner_clause = ""
    if owner_user_id:
        owner_clause = " AND owner_user_id = %s"
        params.append(int(owner_user_id))
    sql = f"SELECT {', '.join(select_cols)} FROM {TABLE_PATIENTS} WHERE patient_id IN ({placeholders}){owner_clause}"
    rows, qerr = mysql_handler.query(sql, tuple(params))
    if qerr:
        return None, f"查询患者数据失败: {qerr}"

    results = []
    risk_counts = {"low": 0, "medium": 0, "high": 0, "critical": 0}
    for row in rows:
        pred, perr = predict_risk(
            model_id,
            _patient_row_for_model(row, features),
            owner_user_id=owner_user_id,
        )
        if perr:
            continue
        results.append({"patient_id": row["patient_id"], "prediction": pred})
        risk_counts[pred["risk_level"]] = risk_counts.get(pred["risk_level"], 0) + 1

    payload = {
        "predictions": results,
        "summary": {
            "total": len(results),
            "risk_distribution": risk_counts,
        },
        "methodology": _risk_methodology(str(meta.get("task_type") or ""), str(meta.get("model_type") or "")),
    }
    if missing_features:
        payload["feature_warnings"] = {
            "missing_in_patient_table": missing_features,
            "imputed_value": 0,
            "note": (
                "以下特征不在患者主表中，批量预测时已用 0 填补。"
                "若模型依赖这些特征，请先将数据导入患者表或重新训练仅使用主表字段的模型。"
            ),
        }
    return payload, None


def model_evaluation(
    model_id: int,
    test_data: Optional[List[Dict]] = None,
    owner_user_id: Optional[int] = None,
) -> Tuple[Optional[dict], Optional[str]]:
    """模型评估报告。如提供 test_data 则重新评估，否则返回已保存的 metrics。"""
    model, meta, err = _load_model(model_id, owner_user_id=owner_user_id)
    if err:
        return None, err

    stored_metrics = json.loads(meta["metrics"]) if isinstance(meta["metrics"], str) else meta["metrics"]

    if test_data and len(test_data) >= 10:
        features = json.loads(meta["features"]) if isinstance(meta["features"], str) else meta["features"]
        df = pd.DataFrame(test_data)
        X = df[features].fillna(0).values
        y_true = df["label"].values if "label" in df.columns else None
        y_pred = model.predict(X)
        y_proba = model.predict_proba(X)[:, 1] if hasattr(model, "predict_proba") else None

        # ROC 曲线数据
        roc_data = None
        if y_true is not None and y_proba is not None and len(np.unique(y_true)) == 2:
            fpr, tpr, thresholds = roc_curve(y_true, y_proba)
            roc_data = {
                "fpr": [round(float(v), 4) for v in fpr],
                "tpr": [round(float(v), 4) for v in tpr],
                "thresholds": [round(float(v), 4) for v in thresholds],
            }

        # 混淆矩阵
        cm = None
        if y_true is not None:
            cm = confusion_matrix(y_true, y_pred).tolist()

        return {
            "stored_metrics": stored_metrics,
            "roc_curve": roc_data,
            "confusion_matrix": cm,
            "methodology": _risk_methodology(str(meta.get("task_type") or ""), str(meta.get("model_type") or "")),
            "validation_status": {
                "external_validation": bool(test_data),
                "calibration_reported": False,
                "intended_use": "decision_support_only",
            },
        }, None

    return {
        "stored_metrics": stored_metrics,
        "methodology": _risk_methodology(str(meta.get("task_type") or ""), str(meta.get("model_type") or "")),
        "validation_status": {
            "external_validation": False,
            "calibration_reported": False,
            "intended_use": "decision_support_only",
        },
    }, None


def list_risk_models(
    task_type: Optional[str] = None,
    owner_user_id: Optional[int] = None,
) -> Tuple[Optional[list], Optional[str]]:
    """列出当前用户的风险预测模型。"""
    ok, err = _ensure_tables()
    if not ok:
        return None, err
    if not owner_user_id or int(owner_user_id) <= 0:
        return [], None

    uid = int(owner_user_id)
    if task_type:
        sql = (
            f"SELECT * FROM {TABLE_RISK_MODELS} "
            f"WHERE task_type = %s AND owner_user_id = %s ORDER BY id DESC"
        )
        rows, qerr = mysql_handler.query(sql, (task_type, uid))
    else:
        sql = f"SELECT * FROM {TABLE_RISK_MODELS} WHERE owner_user_id = %s ORDER BY id DESC"
        rows, qerr = mysql_handler.query(sql, (uid,))
    if qerr:
        return None, f"查询模型列表失败: {qerr}"
    return list(rows) if rows else [], None


def list_predictions(
    owner_user_id: Optional[int] = None,
    model_id: Optional[int] = None,
    limit: int = 100,
) -> Tuple[Optional[list], Optional[str]]:
    """列出当前用户的预测记录。"""
    ok, err = _ensure_tables()
    if not ok:
        return None, err
    if not owner_user_id or int(owner_user_id) <= 0:
        return [], None

    uid = int(owner_user_id)
    params: List[Any] = [uid]
    sql = f"SELECT * FROM {TABLE_PREDICTIONS} WHERE owner_user_id = %s"
    if model_id:
        sql += " AND model_id = %s"
        params.append(int(model_id))
    sql += " ORDER BY id DESC LIMIT %s"
    params.append(int(limit))
    rows, qerr = mysql_handler.query(sql, tuple(params))
    if qerr:
        return None, f"查询预测记录失败: {qerr}"
    return list(rows) if rows else [], None
