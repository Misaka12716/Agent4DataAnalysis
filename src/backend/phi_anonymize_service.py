# backend/phi_anonymize_service.py — 2.1.3 敏感信息匿名化

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import os
import re
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Tuple

MAX_TEXT_LENGTH = 200_000
MAX_DATASET_ROWS = 10_000
MAX_NESTING_DEPTH = 8
VALID_MODES = {"replace", "annotate", "redact"}

MEDICAL_WHITELIST = {
    "精神分裂症",
    "抑郁症",
    "双相障碍",
    "焦虑",
    "幻听",
    "妄想",
    "奥氮平",
    "利培酮",
    "喹硫平",
    "氟西汀",
    "舍曲林",
    "PANSS",
    "HAMD",
    "HAMA",
    "PHQ9",
    "GAD7",
}

PHI_PATTERNS: List[Tuple[str, re.Pattern[str], str]] = [
    (
        "ID_CARD",
        re.compile(
            r"(?<!\d)[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])"
            r"(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)"
        ),
        "[身份证]",
    ),
    (
        "PHONE",
        re.compile(r"(?<!\d)(?:\+?86[-\s]?)?1[3-9](?:\d{9}|\d[-\s]?\d{4}[-\s]?\d{4})(?!\d)"),
        "[手机号]",
    ),
    ("EMAIL", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[邮箱]"),
    ("MRN", re.compile(r"(?:病案号|住院号|门诊号|MRN)[:：\s]*[A-Za-z0-9_-]{4,}", re.I), "[病案号]"),
    (
        "PATIENT_ID_INLINE",
        re.compile(r"(?:患者编号|患者ID|patient[_\s]?id)[:：\s]*[A-Za-z0-9_-]{3,}", re.I),
        "[患者编号]",
    ),
    (
        "DATE_DOB",
        re.compile(r"(?:出生日期|生日|DOB)[:：\s]*\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}日?", re.I),
        "[出生日期]",
    ),
    (
        "ADDRESS",
        re.compile(r"(?:住址|地址|居住地)[:：\s]*[\u4e00-\u9fffA-Za-z0-9-]{2,32}(?:省|市|区|县|镇|村|路|街|巷|号|栋|单元|室)[\u4e00-\u9fffA-Za-z0-9-]{0,24}"),
        "[地址]",
    ),
    ("IP", re.compile(r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)"), "[IP]"),
]

_FULLWIDTH_DIGIT_MAP = str.maketrans("０１２３４５６７８９", "0123456789")

STRUCT_FIELD_RULES = {
    "patient_id": "hash",
    "patient_key": "hash",
    "medical_record_number": "hash",
    "mrn": "hash",
    "name": "name",
    "patient_name": "name",
    "phone": "phone",
    "mobile": "phone",
    "id_card": "id_card",
    "national_id": "id_card",
    "address": "redact",
    "email": "email",
    "contact": "mask",
    "guardian": "name",
    "date_of_birth": "date",
    "dob": "date",
    "birthday": "date",
}
TEXT_FIELDS = {"content", "note", "notes", "text", "description", "medication", "report_text", "conclusion"}


def _pseudonym_secret(secret: Optional[str] = None) -> bytes:
    value = secret if secret is not None else os.getenv("DQ213_PSEUDONYM_SECRET", "")
    if not value and secret is None:
        secret_file = os.getenv("DQ213_PSEUDONYM_SECRET_FILE", "").strip()
        if secret_file:
            path = Path(secret_file).expanduser().resolve(strict=True)
            if not path.is_file():
                raise RuntimeError("DQ213_PSEUDONYM_SECRET_FILE 必须指向普通文件")
            value = path.read_text(encoding="utf-8").strip()
    if len(value.encode("utf-8")) < 16:
        raise RuntimeError("DQ213 脱敏密钥必须配置且至少 16 字节")
    return value.encode("utf-8")


def _hash_id(value: str, secret: Optional[str] = None) -> str:
    digest = hmac.new(_pseudonym_secret(secret), str(value).encode("utf-8"), hashlib.sha256).hexdigest()[:16]
    return f"PID_{digest}"


def _mask_keep_tail(value: Any, keep: int = 2) -> str:
    text = str(value)
    if len(text) <= keep:
        return "*" * len(text)
    return "*" * (len(text) - keep) + text[-keep:]


def _mask_name(value: Any) -> str:
    text = str(value).strip()
    if not text:
        return ""
    if len(text) == 1:
        return "*"
    return text[0] + "*" * (len(text) - 1)


def _mask_phone(value: Any) -> str:
    text = re.sub(r"\s+", "", str(value))
    if len(text) < 7:
        return "*" * len(text)
    return text[:3] + "****" + text[-4:]


def _mask_email(value: Any) -> str:
    text = str(value)
    if "@" not in text:
        return _mask_keep_tail(text, 0)
    local, domain = text.split("@", 1)
    masked_local = (local[:1] if local else "") + "***"
    return f"{masked_local}@{domain}"


def _mask_date(value: Any) -> str:
    match = re.search(r"(19|20)\d{2}", str(value))
    return f"{match.group(0)}-**-**" if match else "[日期]"


def _normalize_text_for_phi(text: Any) -> str:
    raw = str(text or "")
    if len(raw) > MAX_TEXT_LENGTH:
        raise ValueError(f"文本超过上限 {MAX_TEXT_LENGTH}")
    return raw.translate(_FULLWIDTH_DIGIT_MAP)


def _valid_match(entity_type: str, fragment: str) -> bool:
    if entity_type == "PHONE" and "." in fragment:
        return False
    if entity_type == "IP":
        try:
            ipaddress.ip_address(fragment)
        except ValueError:
            return False
    return not any(word in fragment for word in MEDICAL_WHITELIST)


def detect_phi_in_text(text: str, *, include_values: bool = False) -> Dict[str, Any]:
    normalized = _normalize_text_for_phi(text)
    spans: List[Dict[str, Any]] = []
    for entity_type, pattern, _tag in PHI_PATTERNS:
        for match in pattern.finditer(normalized):
            fragment = match.group(0)
            if not _valid_match(entity_type, fragment):
                continue
            item: Dict[str, Any] = {
                "entity_type": entity_type,
                "start": match.start(),
                "end": match.end(),
                "score": 0.95,
                "source": "rule",
            }
            if include_values:
                item["text"] = fragment
            spans.append(item)

    try:
        import spacy  # type: ignore

        nlp = None
        for model in ("zh_core_web_sm", "en_core_web_sm"):
            try:
                nlp = spacy.load(model)
                break
            except Exception:
                continue
        if nlp is not None:
            doc = nlp(normalized[:5000])
            for entity in doc.ents:
                if entity.label_ not in ("PERSON", "PER", "NR"):
                    continue
                if any(word in entity.text for word in MEDICAL_WHITELIST):
                    continue
                item = {
                    "entity_type": "PERSON",
                    "start": entity.start_char,
                    "end": entity.end_char,
                    "score": 0.8,
                    "source": "spacy",
                }
                if include_values:
                    item["text"] = entity.text
                spans.append(item)
    except Exception:
        pass

    for match in re.finditer(
        r"(?:患者|病人|姓名[:：]?)([赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张][\u4e00-\u9fff]{1,2})",
        normalized,
    ):
        item = {
            "entity_type": "PERSON_HEURISTIC",
            "start": match.start(1),
            "end": match.end(1),
            "score": 0.65,
            "source": "heuristic",
        }
        if include_values:
            item["text"] = match.group(1)
        spans.append(item)

    spans.sort(key=lambda item: (item["start"], -(item["end"] - item["start"]), -item["score"]))
    merged: List[Dict[str, Any]] = []
    last_end = -1
    for item in spans:
        if item["start"] < last_end:
            continue
        merged.append(item)
        last_end = item["end"]

    by_type: Dict[str, int] = {}
    for item in merged:
        entity_type = str(item["entity_type"])
        by_type[entity_type] = by_type.get(entity_type, 0) + 1
    return {
        "ok": True,
        "n_entities": len(merged),
        "by_type": by_type,
        "entities": merged,
        "text_len": len(normalized),
    }


def anonymize_text(text: str, mode: str = "replace") -> Dict[str, Any]:
    if mode not in VALID_MODES:
        raise ValueError(f"mode 必须是 {sorted(VALID_MODES)}")
    normalized = _normalize_text_for_phi(text)
    detection = detect_phi_in_text(normalized, include_values=False)
    output = normalized
    tag_map = {name: tag for name, _pattern, tag in PHI_PATTERNS}
    tag_map.update({"PERSON": "[姓名]", "PERSON_HEURISTIC": "[姓名]"})
    for entity in sorted(detection["entities"], key=lambda item: item["start"], reverse=True):
        start, end = int(entity["start"]), int(entity["end"])
        entity_type = str(entity["entity_type"])
        if mode == "annotate":
            replacement = f"⟦{entity_type}:{output[start:end]}⟧"
        elif mode == "redact":
            replacement = "█" * max(1, end - start)
        else:
            replacement = tag_map.get(entity_type, f"[{entity_type}]")
        output = output[:start] + replacement + output[end:]
    return {
        "ok": True,
        "mode": mode,
        "original_len": len(str(text or "")),
        "anonymized": output,
        "detection": detection,
        "checked_at": datetime.now().isoformat(timespec="seconds"),
    }


def _apply_structured_rule(value: Any, rule: str, secret: Optional[str]) -> Any:
    if rule == "hash":
        return _hash_id(str(value), secret=secret)
    if rule == "name":
        return _mask_name(value)
    if rule == "phone":
        return _mask_phone(value)
    if rule == "email":
        return _mask_email(value)
    if rule == "date":
        return _mask_date(value)
    if rule in ("id_card", "redact"):
        return "*" * len(str(value))
    return _mask_keep_tail(value)


def _anonymize_value(
    value: Any,
    *,
    field_name: str,
    secret: Optional[str],
    depth: int,
    operations: List[Dict[str, Any]],
) -> Any:
    if depth > MAX_NESTING_DEPTH:
        raise ValueError("结构化数据嵌套过深")
    key = field_name.lower()
    rule = STRUCT_FIELD_RULES.get(key)
    if value is None:
        return None
    if rule:
        operations.append({"field": field_name, "rule": rule})
        return _apply_structured_rule(value, rule, secret)
    if isinstance(value, Mapping):
        return {
            str(child_key): _anonymize_value(
                child_value,
                field_name=str(child_key),
                secret=secret,
                depth=depth + 1,
                operations=operations,
            )
            for child_key, child_value in value.items()
        }
    if isinstance(value, list):
        return [
            _anonymize_value(
                child,
                field_name=field_name,
                secret=secret,
                depth=depth + 1,
                operations=operations,
            )
            for child in value
        ]
    if isinstance(value, str) and key in TEXT_FIELDS and len(value) >= 4:
        result = anonymize_text(value, mode="replace")
        if result["detection"]["n_entities"]:
            operations.append({"field": field_name, "rule": "text_phi", "n": result["detection"]["n_entities"]})
            return result["anonymized"]
    return value


def anonymize_structured_row(row: Mapping[str, Any], secret: Optional[str] = None) -> Dict[str, Any]:
    if not isinstance(row, Mapping):
        raise ValueError("结构化记录必须是对象")
    operations: List[Dict[str, Any]] = []
    output = {
        str(key): _anonymize_value(
            value,
            field_name=str(key),
            secret=secret,
            depth=0,
            operations=operations,
        )
        for key, value in row.items()
    }
    return {"row": output, "applied": operations}


def anonymize_dataset(rows: List[Mapping[str, Any]], secret: Optional[str] = None) -> Dict[str, Any]:
    if not isinstance(rows, list):
        raise ValueError("rows 必须是对象数组")
    if len(rows) > MAX_DATASET_ROWS:
        raise ValueError(f"rows 超过上限 {MAX_DATASET_ROWS}")
    _pseudonym_secret(secret)
    output_rows = []
    operation_count = 0
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise ValueError(f"rows[{index}] 必须是对象")
        result = anonymize_structured_row(row, secret=secret)
        output_rows.append(result["row"])
        operation_count += len(result["applied"])
    return {
        "ok": True,
        "n_rows": len(output_rows),
        "n_field_ops": operation_count,
        "rows": output_rows,
        "checked_at": datetime.now().isoformat(timespec="seconds"),
    }


def demo_anonymize() -> Dict[str, Any]:
    sample = (
        "患者张伟，身份证110101199001011234，手机13812345678，"
        "住址北京市海淀区某某路1号。诊断精神分裂症，奥氮平10mg。"
        "病案号：ZY20260001。联系邮箱 demo@hospital.org。"
    )
    return anonymize_text(sample, mode="replace")
