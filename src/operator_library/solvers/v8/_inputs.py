"""Shared messy-input coercion helpers for V8.1 solvers.

Designed so every operator in this folder can call
``coerce_numeric_friendly`` / ``coerce_event_binary`` /
``detect_column_kind`` and get a predictable, well-documented
conversion of real-world dirty data.

The point of this module is to **make the same dirty-input promise**
across all operators (IV2SLS, PC, LiNGAM, KM, ARIMA), so a user who
once cleaned ``'75%'`` for one operator can trust the next operator
to handle it the same way.

What we DO handle
-----------------
- numeric / Int64 / boolean dtype → passthrough as float
- strings:
    - whitespace, common NA tokens ("", "NA", "N/A", "nan", "null",
      "none", "-", "--", "?")  → NaN
    - boolean-like ("yes/no", "true/false", "Y/N", "alive/dead",
      "event/censored", "case/control", "positive/negative")
      → 1.0/0.0  (only if ``allow_boolean=True``)
    - percentages  "75%" / "75.5 %" / "-2.5%"  → 0.75 / 0.755 / -0.025
      (semantics: '%' means ÷100, per SQL convention)
    - currency-prefixed numbers  "$1,234.56" / "￥1,200" /
      "€1.5" → 1234.56 / 1200 / 1.5
    - thousands separators  "1,234,567" → 1234567
    - signed/scientific notation handled by ``float()`` after cleaning

What we DON'T handle (by design, to fail safely)
------------------------------------------------
- European decimal format ("1.234,56") — would conflict with US
  thousand-separator handling.  Users should convert upstream.
- Unit suffixes ("5km", "100ms") — too domain-specific.
- Currency codes after the number ("1234 USD") — ambiguous.
- Date strings — handled separately by each operator that wants dates
  (only ``survival_kaplan_meier`` and ``ts_arima_forecast`` currently).
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Regex / lookup tables.  Lower-cased lookups everywhere.
# ---------------------------------------------------------------------------
_NA_TOKENS = {
    "", "na", "n/a", "nan", "null", "none", "-", "--", "—", ".", "..",
    "?", "n.a.", "n.a", "missing", "<na>",
}
_BOOL_TRUE = {
    "true", "t", "yes", "y", "1", "alive", "positive", "case",
    "event", "deceased",  # survival event indicator
}
_BOOL_FALSE = {
    "false", "f", "no", "n", "0", "dead", "negative", "control",
    "censored", "alive_no_event",
    # Note: 'dead'/'alive' map to 1/0 in event semantics — but a
    # survival study may use either convention.  Caller (KM solver)
    # documents this explicitly.
}
# Adjusted: in survival convention 'dead' = event = 1, 'alive' = 0.
# Move 'dead' to TRUE / 'alive' to FALSE.
_BOOL_TRUE.add("dead")
_BOOL_FALSE.discard("dead")
_BOOL_FALSE.add("alive")
_BOOL_TRUE.discard("alive")

# Currency symbols we silently strip.  Excludes plain letters (USD, EUR)
# because removing them mid-string would change "USD$5" → "5" which is
# fine, but "1234 USD" → "1234 " which `float()` rejects — exactly the
# fail-safe behavior we want.
_CURRENCY_CHARS = "$￥¥£€₹￡₩₪﹩"
_CURRENCY_RE = re.compile(f"[{re.escape(_CURRENCY_CHARS)}]")
_THOUSANDS_RE = re.compile(r"(?<=\d),(?=\d{3}(\D|$))")
_PERCENT_RE = re.compile(r"^\s*([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*%\s*$")


def _coerce_one(x: Any, allow_boolean: bool = False) -> float:
    if x is None:
        return float("nan")
    if isinstance(x, float):
        return x  # already float (possibly NaN)
    if isinstance(x, (np.integer, np.floating)):
        return float(x)
    if isinstance(x, (bool, np.bool_)):
        return 1.0 if bool(x) else 0.0
    if isinstance(x, int):
        return float(x)
    # Everything else: try as string.
    t = str(x).strip()
    if t == "":
        return float("nan")
    tl = t.lower()
    if tl in _NA_TOKENS:
        return float("nan")
    if allow_boolean:
        if tl in _BOOL_TRUE:
            return 1.0
        if tl in _BOOL_FALSE:
            return 0.0
    m = _PERCENT_RE.match(t)
    if m:
        try:
            return float(m.group(1)) / 100.0
        except Exception:
            return float("nan")
    # Strip currency + thousands separators.
    cleaned = _CURRENCY_RE.sub("", t)
    cleaned = _THOUSANDS_RE.sub("", cleaned)
    cleaned = cleaned.replace(" ", "")
    try:
        return float(cleaned)
    except Exception:
        return float("nan")


def coerce_numeric_friendly(s: pd.Series,
                             allow_boolean: bool = False
                             ) -> pd.Series:
    """Coerce a Series to float, handling messy real-world strings.

    Returns a new float64 Series.  Unparseable values become NaN.
    """
    # Fast path: numeric (non-bool) dtype passes through unchanged.
    if (pd.api.types.is_numeric_dtype(s)
            and not pd.api.types.is_bool_dtype(s)):
        return s.astype(float)
    # Boolean dtype → 0/1.
    if pd.api.types.is_bool_dtype(s):
        return s.astype("Int64").astype(float)
    # Datetime → numeric (epoch seconds).  KM/ARIMA handle this in
    # their own input layer; here we just refuse to silently convert
    # because the units would be ambiguous (seconds vs days).
    if pd.api.types.is_datetime64_any_dtype(s):
        raise TypeError("coerce_numeric_friendly: datetime input — "
                          "callers must convert dates to days/seconds "
                          "before this helper.")
    # Element-wise coercion for object / mixed.
    return s.map(lambda v: _coerce_one(v, allow_boolean=allow_boolean)
                  ).astype(float)


def coerce_event_binary(s: pd.Series) -> pd.Series:
    """Coerce an event indicator to {0, 1} float.

    Accepts: numeric 0/1, booleans, and the boolean-like strings in
    ``_BOOL_TRUE`` / ``_BOOL_FALSE`` (yes/no/alive/dead/etc.).  Other
    values → NaN.  Raises ValueError if, after coercion, any non-NaN
    value is not in {0, 1}.
    """
    out = coerce_numeric_friendly(s, allow_boolean=True)
    bad = out.dropna()
    bad = bad[~bad.round().astype(int).isin([0, 1])]
    if len(bad):
        raise ValueError(
            f"coerce_event_binary: event column has non-binary values "
            f"after coercion (e.g. {bad.unique()[:5].tolist()}); "
            "expected 0/1 or boolean-like (yes/no, alive/dead, "
            "T/F, etc.)."
        )
    return out.round().astype(float)


def detect_column_kind(s: pd.Series) -> Dict[str, Any]:
    """Lightweight type sniff for diagnostic reporting.

    Returns a dict with:
      - original_dtype:  str
      - n_unique:        int (after coercion)
      - is_binary:       bool   (2 unique values in {0, 1})
      - is_discrete_low_cardinality:  bool (n_unique <= 5)
      - looks_categorical: bool  (object dtype with >2 unique non-numeric)
      - had_percent / had_currency / had_thousands: bool

    Designed to be cheap (samples up to 200 elements) so operators can
    log diagnostics without slowing down on million-row inputs.
    """
    info: Dict[str, Any] = {
        "original_dtype": str(s.dtype),
        "had_percent":    False,
        "had_currency":   False,
        "had_thousands":  False,
        "looks_categorical": False,
    }
    sample = s.dropna().head(200)
    if s.dtype == object and len(sample) > 0:
        strs = sample.astype(str)
        # Use Series.apply with the compiled patterns to dodge the
        # pandas FutureWarning about `str.contains(<compiled regex>)`.
        if strs.apply(lambda x: bool(_PERCENT_RE.search(str(x)))).any():
            info["had_percent"] = True
        if strs.apply(lambda x: bool(_CURRENCY_RE.search(str(x)))).any():
            info["had_currency"] = True
        if strs.apply(lambda x: bool(_THOUSANDS_RE.search(str(x)))).any():
            info["had_thousands"] = True
        # categorical: object dtype where >50% of sample is not parseable
        # as a number.
        n_num_ok = sum(1 for v in strs
                        if not np.isnan(_coerce_one(v, allow_boolean=False)))
        if n_num_ok < 0.5 * len(strs):
            info["looks_categorical"] = True

    # Coerced n_unique
    try:
        coerced = coerce_numeric_friendly(s, allow_boolean=True)
        coerced_nn = coerced.dropna()
        uniq = coerced_nn.unique()
        info["n_unique"] = int(len(uniq))
        info["is_binary"] = bool(
            len(uniq) <= 2 and set(np.round(uniq).astype(int)).issubset({0, 1})
        )
        info["is_discrete_low_cardinality"] = bool(len(uniq) <= 5)
    except Exception:
        info["n_unique"] = -1
        info["is_binary"] = False
        info["is_discrete_low_cardinality"] = False
    return info
