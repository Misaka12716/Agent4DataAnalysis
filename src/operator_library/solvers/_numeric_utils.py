"""Shared "try to coerce object column to numeric" helper.

Some real-world CSVs ship numeric columns as strings with thousand
separators (``"37,410"``), percent signs (``"15.17%"``), currency
symbols (``"$1.50"`` / ``"£2,341"``), or stray whitespace.  pandas
``read_csv`` keeps them as object dtype, so any solver that defends
with ``pd.api.types.is_numeric_dtype`` rejects them — even though the
content IS numeric.

This helper does ONE thing: best-effort string-stripping +
``pd.to_numeric(errors='coerce')``.  It returns the coerced series
plus a flag indicating whether the column was successfully coerced
(≥ ``min_rate`` of the originally-non-NaN cells parse as numeric).

中文：把 ``"37,410"`` / ``"15.17%"`` / ``"$1.50"`` 这种被 pandas 当
object 读进来但实际是数字的列，统一清洗成 float64 Series。≥70%
单元格成功 coerce 才算清洗成功。
"""
from __future__ import annotations

import re
from typing import Tuple

import pandas as pd


# Strip common non-digit noise.  We deliberately do NOT strip ``.`` or
# ``-`` (decimal point / negative sign) or ``e``/``E`` (scientific
# notation).  We strip everything else that commonly clutters numeric
# columns: thousand-separator comma, whitespace, % sign, common
# currency symbols ($, £, ¥, €), and the literal word "USD" tail.
_STRIP_SYMBOLS_RE = re.compile(r"[\s,\$£¥€%]+|\b(?:USD|usd)\b")


def coerce_to_numeric(
    s: pd.Series,
    min_rate: float = 0.7,
) -> Tuple[pd.Series, bool, float]:
    """Best-effort numeric coercion for an object-dtype Series.

    Returns ``(numeric_series, was_coerced, coerce_rate)`` where:

    - ``was_coerced=False, coerce_rate=1.0`` when the input is already
      numeric (no change).
    - ``was_coerced=True``  when ≥ ``min_rate`` of the non-NaN input
      cells parse as numeric after stripping noise symbols.  The
      returned series is the cleaned float series (still has NaN for
      cells that failed to parse).
    - ``was_coerced=False, coerce_rate<min_rate`` when too few cells
      could be parsed.  The cleaned (still partial) series is returned
      so the caller can decide whether to use it or raise.

    中文：原列已是数值 → 直通返回；否则 strip 千分位逗号、百分号、
    货币符号、空白等后再 ``pd.to_numeric(errors='coerce')``。返回
    （清洗后的 series, 是否成功, 成功率）。
    """
    if pd.api.types.is_numeric_dtype(s):
        return s, False, 1.0
    # Count cells that started out non-null (NaN cells don't count
    # against the success rate).
    nonnull_mask = s.notna()
    n_total = int(nonnull_mask.sum())
    if n_total == 0:
        # Nothing to coerce; let caller decide.
        return s, False, 0.0
    s_str = s.astype(str).str.strip()
    s_clean = s_str.str.replace(_STRIP_SYMBOLS_RE, "", regex=True)
    out = pd.to_numeric(s_clean, errors="coerce")
    n_valid = int((out.notna() & nonnull_mask).sum())
    rate = n_valid / n_total
    return out, (rate >= min_rate), rate


__all__ = ["coerce_to_numeric"]
