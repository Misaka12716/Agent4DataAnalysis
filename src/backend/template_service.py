# backend/template_service.py
# 模板管理业务逻辑：CRUD + 版本管理 + 批量导入

import datetime
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from utils.mysql_utils import mysql_handler
from db.template_schema import TEMPLATE_COLUMNS, TABLE_TEMPLATES, VALID_DISEASE_TYPES, TemplateRow


def _ensure_table() -> Tuple[bool, Optional[str]]:
    """确保模板表存在，不存在则创建。"""
    try:
        if not mysql_handler._check_table_exists(TABLE_TEMPLATES):
            from db.template_schema import TEMPLATE_TABLE_DDL
            affected, err = mysql_handler.execute(TEMPLATE_TABLE_DDL)
            if err:
                return False, f"创建模板表失败: {err}"
        return True, None
    except Exception as e:
        return False, str(e)


def _validate_disease_type(disease_type: str) -> bool:
    return disease_type in VALID_DISEASE_TYPES


def _validate_version(version: str) -> bool:
    """验证语义版本格式 MAJOR.MINOR.PATCH"""
    return bool(re.match(r"^\d+\.\d+\.\d+$", version))


def _increment_version(current: str) -> str:
    """递增 PATCH 版本号，如 1.0.0 -> 1.0.1"""
    parts = current.split(".")
    if len(parts) == 3:
        return f"{parts[0]}.{parts[1]}.{int(parts[2]) + 1}"
    return "1.0.1"


def _row_to_response(row: Dict[str, Any]) -> dict:
    """将 DB 行转为 API 响应格式，自动解析 JSON 字段。

    MySQL 驱动会把 TIMESTAMP 列返回为 datetime 对象（SQLite 兜底时是字符串），
    这里统一转成 ISO 字符串，避免 JSONResponse 序列化报 500。
    """
    for key in ("scales", "analysis_steps", "report_structure", "version_history"):
        if key in row and isinstance(row[key], str):
            try:
                row[key] = json.loads(row[key])
            except (json.JSONDecodeError, TypeError):
                pass
    for key in ("created_at", "updated_at"):
        if key in row and isinstance(row[key], (datetime.datetime, datetime.date)):
            row[key] = row[key].isoformat()
    return row


class TemplateService:
    """精神疾病分析模板管理服务"""

    @staticmethod
    def create_template(data: dict) -> Tuple[Optional[dict], Optional[str]]:
        """创建模板。返回 (response_dict, error)"""
        # 校验必填字段
        if not data.get("template_name"):
            return None, "template_name 不能为空"
        if not data.get("disease_type"):
            return None, "disease_type 不能为空"
        if not _validate_disease_type(data["disease_type"]):
            return None, f"disease_type 必须为: {', '.join(VALID_DISEASE_TYPES)}"
        if not data.get("scales") or len(data["scales"]) == 0:
            return None, "scales 至少包含 1 个量表"
        if not data.get("analysis_steps") or len(data["analysis_steps"]) == 0:
            return None, "analysis_steps 不能为空"
        if not data.get("report_structure") or len(data["report_structure"]) == 0:
            return None, "report_structure 不能为空"
        version = data.get("version", "1.0.0")
        if not _validate_version(version):
            return None, f"version 格式无效: {version}，应为 MAJOR.MINOR.PATCH"

        ok, err = _ensure_table()
        if not ok:
            return None, err

        # 检查名称唯一性
        exist_rows, qerr = mysql_handler.query(
            f"SELECT id FROM {TABLE_TEMPLATES} WHERE template_name = %s",
            (data["template_name"],)
        )
        if qerr:
            return None, f"查询模板失败: {qerr}"
        if exist_rows:
            return None, f"模板名称已存在: {data['template_name']}"

        insert_data = {
            "template_name": data["template_name"],
            "disease_type": data["disease_type"],
            "scales": json.dumps(data["scales"], ensure_ascii=False),
            "analysis_steps": json.dumps(data["analysis_steps"], ensure_ascii=False),
            "report_structure": json.dumps(data["report_structure"], ensure_ascii=False),
            "version": version,
            "version_history": None,
        }
        _, last_id, err = mysql_handler.insert(TABLE_TEMPLATES, insert_data)
        if err:
            return None, f"插入模板失败: {err}"

        rows, qerr = mysql_handler.query(
            f"SELECT * FROM {TABLE_TEMPLATES} WHERE id = %s", (last_id,)
        )
        if qerr or not rows:
            return None, f"查询新模板失败: {qerr}"
        return _row_to_response(rows[0]), None

    @staticmethod
    def list_templates(disease_type: Optional[str] = None) -> Tuple[Optional[list], Optional[str]]:
        """列出模板，可按 disease_type 过滤。"""
        ok, err = _ensure_table()
        if not ok:
            return None, err
        if disease_type:
            if not _validate_disease_type(disease_type):
                return None, f"disease_type 必须为: {', '.join(VALID_DISEASE_TYPES)}"
            sql = f"SELECT * FROM {TABLE_TEMPLATES} WHERE disease_type = %s ORDER BY id"
            rows, qerr = mysql_handler.query(sql, (disease_type,))
        else:
            sql = f"SELECT * FROM {TABLE_TEMPLATES} ORDER BY id"
            rows, qerr = mysql_handler.query(sql)
        if qerr:
            return None, f"查询模板列表失败: {qerr}"
        return [_row_to_response(r) for r in rows], None

    @staticmethod
    def get_template(template_id: int) -> Tuple[Optional[dict], Optional[str]]:
        """获取单个模板详情。"""
        ok, err = _ensure_table()
        if not ok:
            return None, err
        rows, qerr = mysql_handler.query(
            f"SELECT * FROM {TABLE_TEMPLATES} WHERE id = %s", (template_id,)
        )
        if qerr:
            return None, f"查询模板失败: {qerr}"
        if not rows:
            return None, "模板不存在"
        return _row_to_response(rows[0]), None

    @staticmethod
    def update_template(template_id: int, data: dict) -> Tuple[Optional[dict], Optional[str]]:
        """更新模板，自动递增版本号并保存历史。"""
        ok, err = _ensure_table()
        if not ok:
            return None, err

        # 获取当前模板
        current, err = TemplateService.get_template(template_id)
        if err:
            return None, err
        if not current:
            return None, "模板不存在"

        # 校验 disease_type
        if data.get("disease_type") and not _validate_disease_type(data["disease_type"]):
            return None, f"disease_type 必须为: {', '.join(VALID_DISEASE_TYPES)}"

        # 校验 scales
        if "scales" in data and (not data["scales"] or len(data["scales"]) == 0):
            return None, "scales 至少包含 1 个量表"

        # 校验 analysis_steps
        if "analysis_steps" in data and (not data["analysis_steps"] or len(data["analysis_steps"]) == 0):
            return None, "analysis_steps 不能为空"

        # 构建更新字段
        updates = []
        params = []
        for field in ["template_name", "disease_type", "scales", "analysis_steps", "report_structure"]:
            if field in data and data[field] is not None:
                val = data[field]
                if field in ("scales", "analysis_steps", "report_structure"):
                    val = json.dumps(val, ensure_ascii=False)
                updates.append(f"{field} = %s")
                params.append(val)

        if not updates:
            # 无更新字段，返回当前
            return current, None

        # 版本管理：保存旧版本到 version_history
        old_version = current.get("version", "1.0.0")
        new_version = _increment_version(old_version)
        history = list(current.get("version_history") or [])
        history.append({
            "version": old_version,
            "scales": current.get("scales"),
            "analysis_steps": current.get("analysis_steps"),
            "report_structure": current.get("report_structure"),
        })
        updates.append("version = %s")
        params.append(new_version)
        updates.append("version_history = %s")
        params.append(json.dumps(history, ensure_ascii=False))

        params.append(template_id)
        sql = f"UPDATE {TABLE_TEMPLATES} SET {', '.join(updates)} WHERE id = %s"
        _, err = mysql_handler.execute(sql, tuple(params))
        if err:
            return None, f"更新模板失败: {err}"

        return TemplateService.get_template(template_id)

    @staticmethod
    def delete_template(template_id: int) -> Tuple[bool, Optional[str]]:
        """删除模板。"""
        ok, err = _ensure_table()
        if not ok:
            return False, err
        _, err = mysql_handler.execute(
            f"DELETE FROM {TABLE_TEMPLATES} WHERE id = %s", (template_id,)
        )
        if err:
            return False, f"删除模板失败: {err}"
        return True, None

    @staticmethod
    def import_templates(templates_dir: str) -> Tuple[dict, Optional[str]]:
        """从目录批量导入 JSON 模板文件。返回 {imported, skipped, details}。"""
        result = {"imported": 0, "skipped": 0, "details": []}
        if not os.path.isdir(templates_dir):
            return result, f"模板目录不存在: {templates_dir}"

        ok, err = _ensure_table()
        if not ok:
            return result, err

        for fname in sorted(os.listdir(templates_dir)):
            if not fname.endswith(".json"):
                continue
            fpath = os.path.join(templates_dir, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as e:
                result["details"].append(f"{fname}: 读取失败 ({e})")
                result["skipped"] += 1
                continue

            resp, err = TemplateService.create_template(data)
            if err:
                result["details"].append(f"{fname}: {err}")
                result["skipped"] += 1
            else:
                result["details"].append(f"{fname}: 已导入 (id={resp['id']})")
                result["imported"] += 1

        return result, None
