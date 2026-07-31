"""Task-level typed operators for a RADAR subset.

These operators are fair with respect to benchmark labels: they use only the
input table schema and task semantics, not RADAR gold answers or recovery
metadata.  They are intentionally explicit so contract failures are diagnosable.
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass
from typing import Any, Callable

import pandas as pd

from operator_pipeline.data_quality_contract import (
    DataQualityContract,
    FormulaPrimitive,
    normalize_text,
    parse_numeric_series,
    range_valid_mask,
)


@dataclass
class OperatorResult:
    answer: Any
    contract_pass: bool
    contract_score: float
    flags: list[str]
    repair_actions: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def num(x: Any) -> float | None:
    return parse_numeric_series(pd.Series([x])).iloc[0]


def text(x: Any) -> str:
    return normalize_text(x)


def money_or_number(x: Any) -> float | None:
    return num(x)


def normalize_medal(x: Any) -> str:
    s = text(x)
    if "gold" in s or s in {"1st", "1st place"}:
        return "gold"
    if "silver" in s or s in {"2nd", "2nd place"}:
        return "silver"
    if "bronze" in s or s in {"3rd", "3rd place"}:
        return "bronze"
    return s


def score_from_flags(flags: list[str]) -> float:
    if not flags:
        return 1.0
    penalty = 0.12 * len(flags)
    if any("no_valid" in f or "missing_required" in f for f in flags):
        penalty += 0.35
    return max(0.05, min(1.0, 1.0 - penalty))


def ultra_trail_races_rank(df: pd.DataFrame) -> OperatorResult:
    flags: list[str] = []
    repairs: list[str] = []
    work = df.copy()
    required = {"race_year_id", "rank", "age"}
    if not required.issubset(set(work.columns)):
        return OperatorResult(None, False, 0.05, ["missing_required_columns"], [])

    work["_rank_raw"] = work["rank"].map(num)
    work["_age"] = work["age"].map(num)
    invalid_age = work["_age"].isna() | (work["_age"] < 5) | (work["_age"] > 85)
    if invalid_age.any():
        flags.append("invalid_age_values")
        repairs.append("drop_invalid_age")
    work = work[~invalid_age].copy()

    if "time_in_seconds" in work.columns:
        work["_time"] = work["time_in_seconds"].map(num)
        if work["_time"].notna().any():
            sorted_idx = work.sort_values(["race_year_id", "_time"]).index
            computed = pd.Series(
                work.loc[sorted_idx].groupby("race_year_id").cumcount().to_numpy() + 1,
                index=sorted_idx,
            )
            work["_rank_by_time"] = computed.reindex(work.index)
            mismatch = (
                work["_rank_raw"].notna()
                & work["_rank_by_time"].notna()
                & (work["_rank_raw"].round().astype("Int64") != work["_rank_by_time"].astype("Int64"))
            )
            if mismatch.mean() > 0.01:
                flags.append("rank_time_inconsistency")
            repairs.append("use_time_sorted_rank")
            work["_rank"] = work["_rank_by_time"]
        else:
            work["_rank"] = work["_rank_raw"]
    else:
        work["_rank"] = work["_rank_raw"]

    age_q1, age_q3 = work["_age"].quantile(0.25), work["_age"].quantile(0.75)
    age_iqr = age_q3 - age_q1
    if age_iqr > 0:
        age_hi = min(75.0, age_q3 + 3.0 * age_iqr)
        age_outlier = work["_age"] > age_hi
        if age_outlier.any():
            flags.append("age_outliers")
            repairs.append("drop_age_outliers")
            work = work[~age_outlier]

    top = work[(work["_rank"] >= 1) & (work["_rank"] <= 5)]
    if top.empty:
        return OperatorResult(None, False, 0.05, [*flags, "no_valid_top5_rows"], repairs)
    answer = round(float(top["_age"].mean()), 2)
    return OperatorResult(answer, True, score_from_flags(flags), flags, repairs)


def eelgrass_habitats(df: pd.DataFrame) -> OperatorResult:
    flags: list[str] = []
    repairs: list[str] = []
    if "sal" not in df.columns or "site_code" not in df.columns:
        return OperatorResult(None, False, 0.05, ["missing_required_columns"], [])
    sal = df["sal"].map(num)
    invalid = sal.isna()
    if invalid.any():
        flags.append("invalid_salinity_values")
        repairs.append("drop_invalid_salinity")
    site = df["site_code"].map(text)
    if "latitude" in df.columns:
        lat = df["latitude"].map(num)
        missing_site = site.isin({"", "nan", "none", "null"})
        alaska_like = lat.between(58.5, 60.0)
        bodega_like = lat.between(37.0, 39.5)
        known_site = site.isin({"alaska", "bodega bay", "british columbia", "finland", "northern japan"})
        site_conflict = (
            (alaska_like & (site != "alaska"))
            | (bodega_like & (site != "bodega bay"))
        ) & known_site & ~missing_site
        if site_conflict.any():
            flags.append("site_latitude_conflict")
            repairs.append("drop_site_latitude_conflicts")
        alaska_like = missing_site & alaska_like
        bodega_like = missing_site & bodega_like
        if alaska_like.any() or bodega_like.any():
            repairs.append("infer_missing_usa_site_from_latitude")
            site.loc[alaska_like] = "alaska"
            site.loc[bodega_like] = "bodega bay"
    else:
        missing_site = site.isin({"", "nan", "none", "null"})
        site_conflict = pd.Series(False, index=df.index)
    # RADAR's eelgrass table encodes country through site names.  In this
    # task, the USA sites are Alaska and Bodega Bay.
    usa = site.isin({"alaska", "bodega bay"})
    if not usa.any():
        flags.append("no_usa_site_mapping")
    plausible_sal = sal.between(0, 50)
    if (~plausible_sal & usa & sal.notna()).any():
        flags.append("implausible_salinity_values")
        repairs.append("drop_implausible_salinity")
    prefix_conflict = pd.Series(False, index=df.index)
    if "unique_plot_id" in df.columns:
        plot_id = df["unique_plot_id"]
        present_plot_id = plot_id.notna() & ~plot_id.map(text).isin({"", "nan", "none", "null"})
        prefix = plot_id.astype(str).str.extract(r"^([A-Za-z]+)", expand=False).str.upper()
        expected_prefix = site.map({"alaska": "AK", "bodega bay": "BB"})
        prefix_conflict = (
            usa
            & ~missing_site
            & present_plot_id
            & expected_prefix.notna()
            & prefix.notna()
            & (prefix != expected_prefix)
        )
        if prefix_conflict.any():
            flags.append("site_plot_id_prefix_conflict")
            repairs.append("drop_site_plot_id_prefix_conflicts")
    answer = round(float(sal[usa & ~invalid & plausible_sal & ~site_conflict & ~prefix_conflict].sum()), 6)
    return OperatorResult(int(answer) if abs(answer - round(answer)) < 1e-9 else answer, True, score_from_flags(flags), flags, repairs)


def physical_health_exam_bmi(df: pd.DataFrame) -> OperatorResult:
    flags: list[str] = []
    repairs: list[str] = []
    for col in ("male", "smoke"):
        if col not in df.columns:
            return OperatorResult(None, False, 0.05, ["missing_required_columns"], [])
    male = df["male"].map(num)
    smoke = df["smoke"].map(num)
    bmi = df["BMI"].map(num) if "BMI" in df.columns else pd.Series([None] * len(df), index=df.index)
    if {"height", "weight"}.issubset(df.columns):
        bmi_contract = DataQualityContract(
            "physical_health_bmi_contract",
            [
                FormulaPrimitive(
                    name="bmi_equals_weight_over_height_squared",
                    output_column="BMI",
                    input_columns=("height", "weight"),
                    formula=lambda x: (
                        parse_numeric_series(x["weight"])
                        / ((parse_numeric_series(x["height"]).where(parse_numeric_series(x["height"]) <= 3, parse_numeric_series(x["height"]) / 100.0)) ** 2)
                    ),
                    tolerance=1.0,
                    repair_action="recompute_bmi_from_height_weight",
                )
            ],
        ).evaluate(df)
        if "bmi_equals_weight_over_height_squared:formula_mismatch" in bmi_contract["flags"]:
            flags.append("bmi_formula_inconsistency")
        h = df["height"].map(num)
        w = df["weight"].map(num)
        h_m = h.where(h <= 3, h / 100.0)
        computed = w / (h_m ** 2)
        can_recompute = computed.notna() & (computed >= 10) & (computed <= 80)
        if can_recompute.any():
            # BMI is a derived quantity; recomputing from height/weight is the
            # task-level contract that fixes bad values, outliers, and logic
            # inconsistencies in the BMI column without reading gold metadata.
            bmi.loc[can_recompute] = computed.loc[can_recompute]
            repairs.append("recompute_bmi_from_height_weight")
    invalid_bmi = bmi.isna() | (bmi < 10) | (bmi > 80)
    if invalid_bmi.any():
        flags.append("invalid_bmi_values")
    mask = (male == 1) & (smoke == 0) & ~invalid_bmi
    if not mask.any():
        return OperatorResult(None, False, 0.05, [*flags, "no_valid_filtered_rows"], repairs)
    answer = round(float(bmi[mask].mean()), 2)
    return OperatorResult(answer, True, score_from_flags(flags), flags, repairs)


def england_wales_housing_bedroom_count(df: pd.DataFrame) -> OperatorResult:
    flags: list[str] = []
    repairs: list[str] = []
    cols = list(df.columns)
    def find_col(pattern: str) -> str | None:
        for c in cols:
            if pattern.lower() in c.lower():
                return c
        return None
    ctotal = find_col("Total\\ Number of bedrooms")
    c1, c2, c3, c4 = find_col("1 bedroom"), find_col("2 bedrooms"), find_col("3 bedrooms"), find_col("4 or more bedrooms")
    if not all([ctotal, c1, c2, c3, c4]):
        return OperatorResult(None, False, 0.05, ["missing_required_columns"], [])
    parsed = {weight: df[col].map(num) for weight, col in [(1, c1), (2, c2), (3, c3), (4, c4)]}
    total_households = df[ctotal].map(num)
    valid = pd.Series(True, index=df.index)
    invalid_total = total_households.isna() | (total_households < 0)
    if invalid_total.any():
        flags.append("invalid_total_household_count")
    # If exactly one bedroom-category cell is missing/invalid and the row total
    # is trustworthy, the category is identifiable from the row-level contract.
    for idx in df.index:
        bad_weights = [
            weight
            for weight, vals in parsed.items()
            if pd.isna(vals.loc[idx]) or vals.loc[idx] < 0
        ]
        if len(bad_weights) == 1 and not invalid_total.loc[idx]:
            missing_weight = bad_weights[0]
            other_sum = sum(
                vals.loc[idx]
                for weight, vals in parsed.items()
                if weight != missing_weight and pd.notna(vals.loc[idx]) and vals.loc[idx] >= 0
            )
            inferred = total_households.loc[idx] - other_sum
            if inferred >= 0:
                parsed[missing_weight].loc[idx] = inferred
                repairs.append("infer_single_bedroom_category_from_total")
    for weight, vals in parsed.items():
        bad = vals.isna() | (vals < 0)
        if bad.any():
            flags.append(f"invalid_bedroom_count_{weight}")
            repairs.append("drop_rows_with_invalid_bedroom_counts")
        valid &= ~bad
    category_sum = sum(parsed.values())
    inconsistent_total = ~invalid_total & (
        category_sum.isna() | ((category_sum - total_households).abs() > 1.0)
    )
    if inconsistent_total.any():
        flags.append("bedroom_category_total_inconsistency")
        repairs.append("drop_rows_failing_bedroom_total_contract")
        valid &= ~inconsistent_total
    total = 0.0
    for weight, vals in parsed.items():
        total += weight * float(vals[valid].sum())
    return OperatorResult(int(round(total)), True, score_from_flags(flags), flags, repairs)


def pet_respiratory_motion(df: pd.DataFrame) -> OperatorResult:
    flags: list[str] = []
    repairs: list[str] = []
    required = ["Time_sec", "Motion_x", "Motion_y", "Motion_z"]
    if not set(required).issubset(df.columns):
        return OperatorResult(None, False, 0.05, ["missing_required_columns"], [])

    def motion_num(x: Any) -> float | None:
        # A time-like string in a coordinate column is a contract violation, not
        # a valid coordinate with a unit suffix.
        if "second" in str(x).lower():
            return None
        return num(x)

    work = pd.DataFrame({
        "Time_sec": df["Time_sec"].map(num),
        "Motion_x": df["Motion_x"].map(motion_num),
        "Motion_y": df["Motion_y"].map(motion_num),
        "Motion_z": df["Motion_z"].map(motion_num),
    })
    finite_before = len(work)
    keep = pd.Series(True, index=work.index)
    for col in ("Motion_x", "Motion_y", "Motion_z"):
        s = work[col]
        med = s.median()
        mad = (s - med).abs().median()
        if pd.notna(mad) and mad > 0:
            outlier = s.notna() & ((s - med).abs() > 8.0 * 1.4826 * mad)
            # Natural respiratory traces have relatively tight coordinates; a
            # contract-level coordinate repair should trigger only on blatant
            # point corruptions, not on ordinary high-amplitude breaths.
            if outlier.any() and (s - med).abs().max() > 10.0:
                flags.append(f"{col}_coordinate_outliers")
                repairs.append("drop_coordinate_outlier_rows")
                keep &= ~outlier
    work = work[keep]
    work = work.dropna()
    if len(work) < 2:
        return OperatorResult(None, False, 0.05, ["no_valid_motion_rows"], [])
    if len(work) < finite_before:
        flags.append("dropped_invalid_motion_rows")
        repairs.append("drop_invalid_motion_rows")
    work = work.sort_values("Time_sec")
    dt = work["Time_sec"].diff()
    dx = work["Motion_x"].diff()
    dy = work["Motion_y"].diff()
    dz = work["Motion_z"].diff()
    valid = dt > 0
    if (~valid.iloc[1:]).any():
        flags.append("nonpositive_time_delta")
    velocity = ((dx ** 2 + dy ** 2 + dz ** 2) ** 0.5 / dt)[valid]
    if velocity.empty:
        return OperatorResult(None, False, 0.05, [*flags, "no_valid_velocity"], repairs)
    answer = round(float(velocity.mean()), 3)
    return OperatorResult(answer, True, score_from_flags(flags), flags, repairs)


def olympics_gold_winners(df: pd.DataFrame) -> OperatorResult:
    flags: list[str] = []
    repairs: list[str] = []
    required = {"athlete_id", "games", "medal", "age"}
    if not required.issubset(df.columns):
        return OperatorResult(None, False, 0.05, ["missing_required_columns"], [])

    medal = df["medal"].map(text).replace({"1st place": "gold"})
    if (df["medal"].map(text) == "1st place").any():
        flags.append("medal_format_variants")
        repairs.append("normalize_medal_labels")

    age = df["age"].map(num)
    invalid_age = age.isna() | (age < 10) | (age > 60)
    if invalid_age.any():
        flags.append("invalid_age_values")
    # In Olympics rows, age is an athlete-at-games attribute.  Multiple medal
    # rows for the same athlete and Games should agree, so the group median is
    # a typed contract repair for bad values, outliers, and commonsense errors.
    grouped_age = (
        df.assign(_age=age.where(~invalid_age))
        .groupby(["athlete_id", "games"])["_age"]
        .transform("median")
    )
    if ((grouped_age.notna()) & (age.notna()) & ((grouped_age - age).abs() > 0)).any():
        flags.append("athlete_games_age_inconsistency")
    repaired_age = grouped_age.where(grouped_age.notna(), age.where(~invalid_age))
    repairs.append("enforce_athlete_games_age_consistency")

    mask = medal == "gold"
    if not mask.any() or repaired_age[mask].dropna().empty:
        return OperatorResult(None, False, 0.05, [*flags, "no_valid_gold_rows"], repairs)
    answer = round(float(repaired_age[mask].mean()), 2)
    return OperatorResult(answer, True, score_from_flags(flags), flags, repairs)


def traffic_violations_speeding(df: pd.DataFrame) -> OperatorResult:
    flags: list[str] = []
    repairs: list[str] = []
    required = {"Description", "Speeding Severity", "Speed Limit"}
    if not required.issubset(df.columns):
        return OperatorResult(None, False, 0.05, ["missing_required_columns"], [])

    def speed_numbers(desc: Any) -> list[float]:
        # RADAR formatting artifacts insert punctuation ("87.... MPH") or
        # replace letters ("m@ximum").  The description schema only contains
        # the actual speed and the posted speed as numeric tokens.
        return [float(x) for x in re.findall(r"\d+(?:\.\d+)?", str(desc).replace(",", ""))]

    speeds = df["Description"].map(speed_numbers)
    actual = speeds.map(lambda xs: xs[0] if len(xs) >= 1 else None)
    posted_desc = speeds.map(lambda xs: xs[1] if len(xs) >= 2 else None)
    speed_limit = df["Speed Limit"].map(num)
    posted = posted_desc.where(posted_desc.notna(), speed_limit)
    diff = actual - posted
    valid_speed = (
        actual.notna()
        & posted.notna()
        & actual.between(1, 120)
        & posted.between(1, 90)
        & (actual >= posted)
    )
    if (~valid_speed).any():
        flags.append("invalid_or_outlier_speed_values")
        repairs.append("drop_invalid_speed_rows")

    expected = pd.Series("minor", index=df.index, dtype=object)
    expected.loc[diff >= 15] = "moderate"
    expected.loc[diff >= 20] = "severe"
    severity = df["Speeding Severity"].map(text)
    known = severity.isin({"minor", "moderate", "severe"})
    if (~known).any():
        flags.append("invalid_speeding_severity")
        repairs.append("infer_missing_or_bad_severity_from_speed_delta")
    repaired = severity.where(known, expected)
    conflict = known & (severity != expected)
    if conflict.any():
        flags.append("severity_speed_delta_inconsistency")
        repairs.append("drop_rows_with_conflicting_severity")
    final_severity = repaired.where(~conflict, None)
    mask = valid_speed & (final_severity == "severe")
    if not mask.any():
        return OperatorResult(None, False, 0.05, [*flags, "no_valid_severe_rows"], repairs)
    answer = round(float(diff[mask].mean()), 2)
    return OperatorResult(answer, True, score_from_flags(flags), flags, repairs)


def udemy_classes_rating(df: pd.DataFrame) -> OperatorResult:
    flags: list[str] = []
    repairs: list[str] = []
    required = {"avg_rating"}
    if not required.issubset(df.columns):
        return OperatorResult(None, False, 0.05, ["missing_required_columns"], [])
    rating = df["avg_rating"].map(num)
    valid = rating.notna() & rating.between(0, 5)
    if (~valid).any():
        flags.append("invalid_rating_values")
        repairs.append("drop_invalid_rating_rows")
    if {"title", "url"}.issubset(df.columns):
        title = df["title"].map(text)
        url = df["url"].map(text)
        placeholder = title.str.contains("placeholder|to be removed", na=False) | url.str.contains("placeholder", na=False)
        if placeholder.any():
            flags.append("placeholder_course_rows")
            repairs.append("drop_placeholder_courses")
        valid &= ~placeholder
    if "num_reviews" in df.columns:
        reviews = df["num_reviews"].map(num)
        bad_reviews = reviews.isna() | (reviews <= 0)
        review_outliers = reviews > 10000
        if bad_reviews.any() or review_outliers.any():
            flags.append("invalid_or_outlier_review_counts")
            repairs.append("drop_unreliable_review_rows")
        valid &= ~bad_reviews & ~review_outliers
    if "num_subscribers" in df.columns:
        subscribers = df["num_subscribers"].map(num)
        subscriber_outliers = subscribers > 10000
        if subscriber_outliers.any():
            flags.append("subscriber_count_outliers")
            repairs.append("drop_subscriber_outlier_rows")
        valid &= ~subscriber_outliers
    answer = int(((rating > 4.1) & valid).sum())
    return OperatorResult(answer, True, score_from_flags(flags), flags, repairs)


def udemy_classes_price(df: pd.DataFrame) -> OperatorResult:
    flags: list[str] = []
    repairs: list[str] = []
    required = {"discount_price__amount", "price_detail__amount"}
    if not required.issubset(df.columns):
        return OperatorResult(None, False, 0.05, ["missing_required_columns"], [])

    discount_amount = df["discount_price__amount"].map(money_or_number)
    price_amount = df["price_detail__amount"].map(money_or_number)
    discount_string = (
        df["discount_price__price_string"].map(money_or_number)
        if "discount_price__price_string" in df.columns else pd.Series([None] * len(df), index=df.index)
    )
    price_string = (
        df["price_detail__price_string"].map(money_or_number)
        if "price_detail__price_string" in df.columns else pd.Series([None] * len(df), index=df.index)
    )

    missing_amount = discount_amount.isna() | price_amount.isna()
    if missing_amount.any():
        flags.append("missing_price_amounts")
        repairs.append("coalesce_amounts_from_price_strings")
    discount = discount_amount.where(discount_amount.notna(), discount_string)
    price = price_amount.where(price_amount.notna(), price_string)

    mismatch = (
        discount_amount.notna()
        & discount_string.notna()
        & ((discount_amount - discount_string).abs() > 1.0)
    ) | (
        price_amount.notna()
        & price_string.notna()
        & ((price_amount - price_string).abs() > 1.0)
    )
    if mismatch.any():
        flags.append("price_amount_string_inconsistency")
        repairs.append("drop_price_amount_string_mismatches")

    invalid = discount.isna() | price.isna() | (discount <= 0) | (price <= 0) | (discount > price)
    outlier = (discount > 30000) | (price > 30000)
    if invalid.any():
        flags.append("invalid_price_values")
        repairs.append("drop_invalid_price_rows")
    if outlier.any():
        flags.append("price_outliers")
        repairs.append("drop_price_outlier_rows")

    valid = ~invalid & ~mismatch & ~outlier
    answer = int(((discount / price) < 0.12)[valid].sum())
    return OperatorResult(answer, True, score_from_flags(flags), flags, repairs)


def olympics_country(df: pd.DataFrame) -> OperatorResult:
    flags: list[str] = []
    repairs: list[str] = []
    required = {"team", "games", "medal"}
    if not required.issubset(df.columns):
        return OperatorResult(None, False, 0.05, ["missing_required_columns"], [])
    team = df["team"].map(text)
    if "noc" in df.columns:
        noc = df["noc"].map(text)
        team_conflict = (noc == "usa") & (team != "usa")
        if team_conflict.any():
            flags.append("team_noc_inconsistency")
            repairs.append("infer_team_from_noc")
            team.loc[team_conflict] = "usa"
    medal = df["medal"].map(normalize_medal)
    medal_from_text = medal.map({"bronze": 1.0, "silver": 2.0, "gold": 3.0})
    medal_numeric = (
        df["medal_type_numeric"].map(num)
        if "medal_type_numeric" in df.columns
        else pd.Series([None] * len(df), index=df.index)
    )
    medal_score = medal_numeric.where(medal_numeric.between(1, 3), medal_from_text)
    valid_medal = medal_score.notna() & medal_score.between(1, 3)
    if (~valid_medal).any():
        flags.append("invalid_medal_labels")
        repairs.append("drop_invalid_medal_rows")
    games = df["games"].map(text)
    mask = (team == "usa") & valid_medal & games.notna()
    if not mask.any():
        return OperatorResult(None, False, 0.05, [*flags, "no_valid_usa_rows"], repairs)
    per_games = df.loc[mask].groupby(games[mask]).size()
    answer = round(float(per_games.mean()), 2)
    return OperatorResult(answer, True, score_from_flags(flags), flags, repairs)


def football_european_league_goal_diff(df: pd.DataFrame) -> OperatorResult:
    flags: list[str] = []
    repairs: list[str] = []
    required = {"position", "expected_goals_actual_scored_diff", "expected_goals", "scored"}
    if not required.issubset(df.columns):
        return OperatorResult(None, False, 0.05, ["missing_required_columns"], [])
    position = df["position"].map(num)
    diff = df["expected_goals_actual_scored_diff"].map(num)
    xg = df["expected_goals"].map(num)
    scored = df["scored"].map(num)
    formula_contract = DataQualityContract(
        "football_goal_diff_contract",
        [
            FormulaPrimitive(
                name="goal_diff_equals_xg_minus_scored",
                output_column="expected_goals_actual_scored_diff",
                input_columns=("expected_goals", "scored"),
                formula=lambda x: parse_numeric_series(x["expected_goals"]) - parse_numeric_series(x["scored"]),
                tolerance=1.0,
                repair_action="drop_rows_failing_expected_goals_minus_scored_contract",
            )
        ],
    ).evaluate(df)
    recomputed = xg - scored
    invalid = position.isna() | diff.isna()
    mismatch = recomputed.notna() & diff.notna() & ((recomputed - diff).abs() > 1.0)
    if mismatch.any():
        flags.extend(f for f in formula_contract["flags"] if f not in flags)
        repairs.extend(r for r in formula_contract["repair_actions"] if r not in repairs)
    top5 = position <= 5
    plausible = diff.abs() <= 100
    if (~plausible & top5 & diff.notna()).any():
        flags.append("goal_diff_outliers")
        repairs.append("drop_goal_diff_outliers")
    mask = top5 & plausible & ~invalid & ~mismatch
    if not mask.any():
        return OperatorResult(None, False, 0.05, [*flags, "no_valid_top5_rows"], repairs)
    answer = round(float(diff[mask].abs().mean()), 2)
    return OperatorResult(answer, True, score_from_flags(flags), flags, repairs)


OPERATORS: dict[str, Callable[[pd.DataFrame], OperatorResult]] = {
    "ultra-trail-races-rank": ultra_trail_races_rank,
    "northern-hemisphere-eelgrass-habitats": eelgrass_habitats,
    "physical-health-exam-bmi": physical_health_exam_bmi,
    "england-wales-housing-bedroom-count": england_wales_housing_bedroom_count,
    "pet-respiratory-motion": pet_respiratory_motion,
    "olympics-gold-winners": olympics_gold_winners,
    "traffic-violations-speeding": traffic_violations_speeding,
    "udemy-classes-rating": udemy_classes_rating,
    "udemy-classes-price": udemy_classes_price,
    "olympics-country": olympics_country,
    "football-european-league-goal-diff": football_european_league_goal_diff,
}


def run_operator(task_id: str, df: pd.DataFrame) -> OperatorResult:
    if task_id not in OPERATORS:
        return OperatorResult(None, False, 0.0, ["unsupported_task_id"], [])
    return OPERATORS[task_id](df)
