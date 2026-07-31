"""Lightweight data-quality contracts for tabular operator confidence.

The contract is deliberately label-free: it never reads gold answers or RADAR
recovery metadata.  It inspects the input table and the produced answer to
estimate whether an agreement signal is likely to be trustworthy.
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Iterable, Protocol

import pandas as pd


_MISSING = {"", "nan", "none", "null", "na", "n/a", "missing", "unknown"}
_SENTINELS = {-9999, -999, 9999, 99999, 999999, -1e9, 1e9}
_NONNEG_HINTS = (
    "count", "case", "cases", "age", "rank", "price", "sales", "total",
    "number", "num", "amount", "bmi", "rating", "score", "speed",
    "bedroom", "population", "patients", "gold", "winner", "winners",
)


@dataclass
class ContractReport:
    contract_score: float
    table_health_score: float
    answer_health_score: float
    missing_cell_rate: float
    numeric_parse_failure_rate: float
    sentinel_cell_rate: float
    nonnegative_violation_rate: float
    outlier_cell_rate: float
    suspicious_zero_answer: bool
    flags: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PrimitiveReport:
    """Diagnostic result for one typed contract primitive.

    The report is label-free: it describes whether the input table obeyed a
    declared type/value/relation contract, not whether the benchmark answer
    matched gold.
    """

    name: str
    passed: bool
    score: float
    n_checked: int
    n_failed: int
    failure_rate: float
    flags: list[str] = field(default_factory=list)
    repair_actions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TypedPrimitive(Protocol):
    name: str

    def evaluate(self, df: pd.DataFrame) -> PrimitiveReport:
        ...


@dataclass
class DataQualityContract:
    """Composable, label-free data-quality contract.

    A task adapter should declare a small set of primitives, run them on the
    table, and use their masks/reports to decide whether the typed path is
    compliant.  This is the method-level object we can report as Contract
    Compliance Rate, separate from final answer accuracy.
    """

    name: str
    primitives: list[TypedPrimitive]

    def evaluate(self, df: pd.DataFrame) -> dict[str, Any]:
        reports = [p.evaluate(df) for p in self.primitives]
        if reports:
            score = 1.0
            for r in reports:
                score *= r.score
            passed = all(r.passed for r in reports)
        else:
            score = 1.0
            passed = True
        flags: list[str] = []
        repairs: list[str] = []
        for r in reports:
            flags.extend(r.flags)
            repairs.extend(r.repair_actions)
        return {
            "name": self.name,
            "passed": passed,
            "score": round(max(0.0, min(1.0, score)), 6),
            "flags": flags,
            "repair_actions": repairs,
            "primitive_reports": [r.to_dict() for r in reports],
        }


def first_number(x: Any) -> float | None:
    if x is None:
        return None
    if isinstance(x, dict) and "answer" in x:
        return first_number(x["answer"])
    if isinstance(x, (int, float)) and not isinstance(x, bool):
        return float(x) if math.isfinite(float(x)) else None
    s = str(x).strip().replace(",", "")
    if not s or s.lower() in _MISSING:
        return None
    m = re.search(r"[-+]?\d+(?:\.\d+)?(?:e[-+]?\d+)?", s, re.IGNORECASE)
    if not m:
        return None
    try:
        val = float(m.group(0))
        return val if math.isfinite(val) else None
    except ValueError:
        return None


def normalize_text(x: Any) -> str:
    return re.sub(r"\s+", " ", str(x).strip().lower())


def normalized_text_series(s: pd.Series) -> pd.Series:
    return s.map(normalize_text)


def parse_numeric_series(s: pd.Series) -> pd.Series:
    return s.map(first_number)


def missing_mask(s: pd.Series) -> pd.Series:
    return s.astype(str).str.strip().str.lower().isin(_MISSING) | s.isna()


def range_valid_mask(values: pd.Series, low: float | None = None, high: float | None = None) -> pd.Series:
    mask = values.notna()
    if low is not None:
        mask &= values >= low
    if high is not None:
        mask &= values <= high
    return mask


def iqr_outlier_mask(values: pd.Series, k: float = 3.0) -> pd.Series:
    clean = values.dropna().astype(float)
    out = pd.Series(False, index=values.index)
    if len(clean) < 8:
        return out
    q1, q3 = clean.quantile(0.25), clean.quantile(0.75)
    iqr = q3 - q1
    if iqr <= 0:
        return out
    lo, hi = q1 - k * iqr, q3 + k * iqr
    return values.notna() & ((values < lo) | (values > hi))


@dataclass
class NumericRangePrimitive:
    name: str
    column: str
    low: float | None = None
    high: float | None = None
    repair_action: str = "drop_out_of_range_rows"
    required: bool = True

    def evaluate(self, df: pd.DataFrame) -> PrimitiveReport:
        if self.column not in df.columns:
            flag = f"{self.name}:missing_column:{self.column}"
            return PrimitiveReport(self.name, False, 0.05, 0, 0, 1.0, [flag], [])
        values = parse_numeric_series(df[self.column])
        checked = int(values.notna().sum())
        valid = range_valid_mask(values, self.low, self.high)
        failed = int((values.notna() & ~valid).sum())
        if self.required:
            failed += int(values.isna().sum())
            checked = int(len(values))
        rate = failed / max(1, checked)
        flags = [f"{self.name}:range_violation"] if failed else []
        repairs = [self.repair_action] if failed else []
        return PrimitiveReport(
            self.name,
            failed == 0,
            round(max(0.05, 1.0 - min(1.0, 2.0 * rate)), 6),
            checked,
            failed,
            round(rate, 6),
            flags,
            repairs,
        )


@dataclass
class FormulaPrimitive:
    name: str
    output_column: str
    input_columns: tuple[str, ...]
    formula: Callable[[pd.DataFrame], pd.Series]
    tolerance: float = 1e-6
    repair_action: str = "drop_formula_mismatch_rows"

    def evaluate(self, df: pd.DataFrame) -> PrimitiveReport:
        missing = [c for c in (self.output_column, *self.input_columns) if c not in df.columns]
        if missing:
            flag = f"{self.name}:missing_columns:{','.join(missing)}"
            return PrimitiveReport(self.name, False, 0.05, 0, 0, 1.0, [flag], [])
        reported = parse_numeric_series(df[self.output_column])
        expected = self.formula(df)
        comparable = reported.notna() & expected.notna()
        mismatch = comparable & ((reported - expected).abs() > self.tolerance)
        checked = int(comparable.sum())
        failed = int(mismatch.sum())
        rate = failed / max(1, checked)
        flags = [f"{self.name}:formula_mismatch"] if failed else []
        repairs = [self.repair_action] if failed else []
        return PrimitiveReport(
            self.name,
            failed == 0,
            round(max(0.05, 1.0 - min(1.0, 2.5 * rate)), 6),
            checked,
            failed,
            round(rate, 6),
            flags,
            repairs,
        )


@dataclass
class CategorySetPrimitive:
    name: str
    column: str
    allowed_values: set[str]
    normalizer: Callable[[Any], str] = normalize_text
    repair_action: str = "drop_invalid_category_rows"

    def evaluate(self, df: pd.DataFrame) -> PrimitiveReport:
        if self.column not in df.columns:
            flag = f"{self.name}:missing_column:{self.column}"
            return PrimitiveReport(self.name, False, 0.05, 0, 0, 1.0, [flag], [])
        vals = df[self.column].map(self.normalizer)
        present = ~missing_mask(df[self.column])
        invalid = present & ~vals.isin(self.allowed_values)
        checked = int(present.sum())
        failed = int(invalid.sum())
        rate = failed / max(1, checked)
        flags = [f"{self.name}:invalid_categories"] if failed else []
        repairs = [self.repair_action] if failed else []
        return PrimitiveReport(
            self.name,
            failed == 0,
            round(max(0.05, 1.0 - min(1.0, 2.0 * rate)), 6),
            checked,
            failed,
            round(rate, 6),
            flags,
            repairs,
        )


def _looks_numeric_series(s: pd.Series) -> bool:
    sample = s.dropna().astype(str).str.strip()
    sample = sample[~sample.str.lower().isin(_MISSING)]
    if sample.empty:
        return False
    parsed = sample.map(first_number)
    return parsed.notna().mean() >= 0.65


def _is_nonnegative_column(col: str) -> bool:
    c = col.lower()
    return any(h in c for h in _NONNEG_HINTS)


def _bounded_rate(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def evaluate_contract(df: pd.DataFrame, answer: Any = None) -> ContractReport:
    total_cells = max(1, int(df.shape[0] * df.shape[1]))
    flags: list[str] = []

    text_df = df.astype(str)
    missing_mask = text_df.apply(lambda col: col.str.strip().str.lower().isin(_MISSING))
    missing_cell_rate = float(missing_mask.to_numpy().sum() / total_cells)

    numeric_cols: list[str] = []
    numeric_parse_fail = 0
    numeric_total = 0
    sentinel_count = 0
    nonnegative_violations = 0
    outlier_count = 0
    outlier_total = 0

    for col in df.columns:
        s = df[col]
        if not _looks_numeric_series(s):
            continue
        numeric_cols.append(str(col))
        raw = s.astype(str).str.strip()
        non_missing = raw[~raw.str.lower().isin(_MISSING)]
        vals = non_missing.map(first_number)
        numeric_total += int(len(non_missing))
        numeric_parse_fail += int(vals.isna().sum())
        clean_vals = vals.dropna().astype(float)
        if clean_vals.empty:
            continue
        sentinel_count += int(clean_vals.map(lambda v: int(v) in _SENTINELS or abs(v) >= 1e9).sum())
        if _is_nonnegative_column(str(col)):
            nonnegative_violations += int((clean_vals < 0).sum())
        if len(clean_vals) >= 8:
            q1, q3 = clean_vals.quantile(0.25), clean_vals.quantile(0.75)
            iqr = q3 - q1
            if iqr > 0:
                lo, hi = q1 - 3.0 * iqr, q3 + 3.0 * iqr
                outlier_count += int(((clean_vals < lo) | (clean_vals > hi)).sum())
                outlier_total += int(len(clean_vals))

    numeric_parse_failure_rate = numeric_parse_fail / max(1, numeric_total)
    sentinel_cell_rate = sentinel_count / max(1, numeric_total)
    nonnegative_violation_rate = nonnegative_violations / max(1, numeric_total)
    outlier_cell_rate = outlier_count / max(1, outlier_total)

    if missing_cell_rate > 0.01:
        flags.append("missing_cells")
    if numeric_parse_failure_rate > 0.05:
        flags.append("numeric_parse_failures")
    if sentinel_cell_rate > 0:
        flags.append("sentinel_values")
    if nonnegative_violation_rate > 0:
        flags.append("nonnegative_violations")
    if outlier_cell_rate > 0.03:
        flags.append("outlier_values")

    table_penalty = (
        2.0 * min(0.20, missing_cell_rate)
        + 2.5 * min(0.20, numeric_parse_failure_rate)
        + 6.0 * min(0.10, sentinel_cell_rate)
        + 6.0 * min(0.10, nonnegative_violation_rate)
        + 2.0 * min(0.20, outlier_cell_rate)
    )
    table_health_score = _bounded_rate(1.0 - table_penalty)

    ans_num = first_number(answer)
    suspicious_zero_answer = False
    if ans_num is not None and abs(ans_num) < 1e-12 and numeric_cols:
        # A zero answer can be valid, but it is suspicious when the table has
        # substantial non-zero numeric evidence.  This catches common failure
        # modes where code selected no rows and returned 0.
        nonzero_seen = False
        for col in numeric_cols[:20]:
            vals = df[col].astype(str).map(first_number).dropna().astype(float)
            if len(vals) and (vals.abs() > 1e-12).mean() > 0.25:
                nonzero_seen = True
                break
        suspicious_zero_answer = nonzero_seen
    answer_health_score = 0.55 if suspicious_zero_answer else 1.0
    if suspicious_zero_answer:
        flags.append("suspicious_zero_answer")

    contract_score = _bounded_rate(table_health_score * answer_health_score)
    return ContractReport(
        contract_score=round(contract_score, 6),
        table_health_score=round(table_health_score, 6),
        answer_health_score=round(answer_health_score, 6),
        missing_cell_rate=round(missing_cell_rate, 6),
        numeric_parse_failure_rate=round(numeric_parse_failure_rate, 6),
        sentinel_cell_rate=round(sentinel_cell_rate, 6),
        nonnegative_violation_rate=round(nonnegative_violation_rate, 6),
        outlier_cell_rate=round(outlier_cell_rate, 6),
        suspicious_zero_answer=suspicious_zero_answer,
        flags=flags,
    )
