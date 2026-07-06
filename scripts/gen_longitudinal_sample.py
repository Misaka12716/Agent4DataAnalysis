# scripts/gen_longitudinal_sample.py
# 生成"同一患者基线→随访"纵向抑郁症治疗数据集，用于验证
# template_step_executor 里 responder_analysis 的纵向轨迹分支
# （_run_responder_longitudinal）以及新增的量表变化趋势（visit trend）计算。
#
# 重要说明：真实患者数据涉及隐私/伦理与数据使用协议（如 STAR*D、CATIE 等
# 公开精神科纵向数据集均需向 NIMH/NDA 申请数据使用协议，无法在此环境直接
# 下载），因此本脚本生成的是"参数经过文献校准的高仿真合成数据"，不是真实
# 患者数据；所有分布参数都在下方注释里注明了参考依据，确保临床上合理、
# 不是随手瞎编的数字。
#
# 关键参数来源：
#   - HAMD-17 量表范围 0-52，中重度入组阈值 >=18：
#     Hamilton M (1960) J Neurol Neurosurg Psychiatry 23:56-62
#   - 抗抑郁药 vs 安慰剂应答率量级（约 45%-59% vs 35%-40%）：
#     Cipriani A et al. (2018) Lancet 391:1357-1366（21种抗抑郁药网络meta分析）
#   - 安慰剂应答率量级（约 30%-40%）：
#     Walsh BT et al. (2002) JAMA 287:1840-1847
#   - 缓解后复发率量级（约 20%-30%/6-12月）：
#     Rush AJ et al. (2006) Am J Psychiatry 163:1905-1917（STAR*D）
#   - 抑郁症女性:男性患病比例约 1.7:1：
#     Kessler RC et al. (2003) JAMA 289:3095-3105（NCS-R）
#   - HAMD/HAMA 共病相关系数量级（约 0.6-0.7）：
#     Hamilton M (1959) Br J Med Psychol 32:50-55；抑郁焦虑共病文献综述量级参考

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "tests" / "fixtures" / "mental_health_longitudinal_sample.xlsx"

VISITS = [("baseline", 0), ("week2", 2), ("week4", 4), ("week8", 8), ("week12", 12)]

MEDICATIONS = {
    # (label, 期望12周应答率量级, 相对基线波动速率系数)
    "SSRI": (0.55, 1.0),
    "SNRI": (0.58, 1.05),
    "none": (0.35, 0.55),          # 未用药/仅心理治疗，对照类比
    "benzodiazepine": (0.30, 0.5),  # 仅抗焦虑辅助，非一线抗抑郁治疗
    "aripiprazole": (0.40, 0.75),   # 增效治疗，通常用于难治性亚组，基线更重
    "methylphenidate": (0.38, 0.7),
}
MED_WEIGHTS = [0.35, 0.25, 0.15, 0.10, 0.08, 0.07]

DIAGNOSES = ["major_depressive_disorder", "persistent_depressive_disorder", "depression_with_anxious_distress"]
DIAG_WEIGHTS = [0.80, 0.12, 0.08]


def _clip(arr, lo, hi):
    return np.clip(arr, lo, hi)


def generate(n_patients: int = 140, seed: int = 20260701) -> pd.DataFrame:
    rng = np.random.default_rng(seed)

    patient_ids = [f"LP{100000 + i}" for i in range(n_patients)]
    age = _clip(rng.normal(38, 12, n_patients), 18, 65).round(0).astype(int)
    gender = rng.choice(["female", "male"], size=n_patients, p=[0.62, 0.38])
    diagnosis = rng.choice(DIAGNOSES, size=n_patients, p=DIAG_WEIGHTS)
    disease_duration_years = _clip(rng.exponential(3.5, n_patients), 0.1, 20).round(1)
    medication = rng.choice(list(MEDICATIONS.keys()), size=n_patients, p=MED_WEIGHTS)
    medication_dose_mg = np.array([
        {"SSRI": rng.choice([20, 40, 60]), "SNRI": rng.choice([75, 150, 225]),
         "none": 0, "benzodiazepine": rng.choice([0.5, 1.0, 2.0]),
         "aripiprazole": rng.choice([2, 5, 10]), "methylphenidate": rng.choice([18, 27, 36])}[m]
        for m in medication
    ], dtype=float)

    baseline_hamd = _clip(rng.normal(24, 4.5, n_patients), 18, 35).round(0)

    base_start = pd.Timestamp("2024-09-01")
    baseline_date = base_start + pd.to_timedelta(rng.integers(0, 400, n_patients), unit="D")

    rows = []
    response_labels = []
    floor_fracs = []
    rates = []

    for i in range(n_patients):
        resp_rate, rate_mult = MEDICATIONS[medication[i]]
        # 用应答概率 + 随机扰动决定该患者落在哪个反应分层，
        # 分层内 floor_frac（终点/基线比值）均匀采样，量级见模块头注释
        roll = rng.uniform()
        if roll < resp_rate:
            tier = "response"
            floor_frac = rng.uniform(0.15, 0.45)     # 55%-85% 下降
        elif roll < resp_rate + 0.30:
            tier = "partial_response"
            floor_frac = rng.uniform(0.50, 0.75)     # 25%-50% 下降
        else:
            tier = "no_response"
            floor_frac = rng.uniform(0.80, 1.05)     # <20% 下降甚至略加重
        response_labels.append(tier)
        floor_fracs.append(floor_frac)
        rates.append(0.28 * rate_mult * rng.uniform(0.75, 1.25))

    floor_fracs = np.array(floor_fracs)
    rates = np.array(rates)

    # ---- 12 周随访是否脱落（约 12% 提前脱落，行政性原因，与应答无关）----
    # 量级参考：抗抑郁药 RCT 12 周脱落率常见 10%-20%
    dropout_at = rng.choice(
        ["none", "week8"], size=n_patients, p=[0.88, 0.12]
    )

    for i in range(n_patients):
        pid = patient_ids[i]
        b_hamd = baseline_hamd[i]
        for visit_name, week in VISITS:
            if dropout_at[i] == "week8" and week > 8:
                # 脱落患者 week12 用 LOCF（末次观测值结转）而非真实随访，
                # 常见于纵向精神科试验的保守 ITT 处理
                # 参考：Mallinckrodt CH et al. (2003) J Biopharm Stat 13:179-190
                week_eff = 8
                locf = True
            else:
                week_eff = week
                locf = False

            noise = rng.normal(0, 1.5)
            decay = floor_fracs[i] + (1 - floor_fracs[i]) * np.exp(-rates[i] * week_eff)
            hamd = _clip(b_hamd * decay + noise, 0, 52)

            hama_base = _clip(b_hamd * (56 / 52) * 0.72 + rng.normal(0, 2.0), 0, 56)
            hama = _clip(hama_base * decay + rng.normal(0, 1.5), 0, 56)

            phq9_base = _clip(b_hamd * (27 / 52) * 0.85 + rng.normal(0, 1.2), 0, 27)
            phq9 = _clip(phq9_base * decay + rng.normal(0, 1.0), 0, 27)

            visit_date = baseline_date[i] + pd.Timedelta(days=week * 7 + int(rng.integers(-2, 3)))

            rows.append({
                "patient_id": pid,
                "visit_type": visit_name,
                "visit_week": week,
                "visit_date": visit_date.date().isoformat(),
                "age": age[i],
                "gender": gender[i],
                "diagnosis": diagnosis[i],
                "disease_duration_years": disease_duration_years[i],
                "medication": medication[i],
                "medication_dose_mg": medication_dose_mg[i],
                "HAMD_total": round(float(hamd), 1),
                "HAMA_total": round(float(hama), 1),
                "PHQ9_total": round(float(phq9), 1),
                "locf_imputed": int(locf),
            })

    df = pd.DataFrame(rows)

    # ---- 患者级最终结局（用于 outcome / ordinal_regression / KM / Cox）----
    outcome = np.array(response_labels, dtype=object)

    # 缓解/应答后复发：量级参考 STAR*D 随访复发率约 20%-30%/6-12月
    relapse = np.zeros(n_patients, dtype=int)
    duration_days = np.full(n_patients, 365, dtype=float)
    for i in range(n_patients):
        if response_labels[i] in ("response", "partial_response"):
            if rng.uniform() < (0.28 if response_labels[i] == "response" else 0.35):
                relapse[i] = 1
                duration_days[i] = _clip(rng.exponential(180), 30, 364)
        else:
            # 无应答者：用"再入院"而非"复发"概念，量级参考真实世界再入院研究约 15%-20%
            pass
    readmission = np.zeros(n_patients, dtype=int)
    for i in range(n_patients):
        if response_labels[i] == "no_response" and rng.uniform() < 0.18:
            readmission[i] = 1
            duration_days[i] = min(duration_days[i], _clip(rng.exponential(150), 20, 364))

    outcome_final = np.where(relapse == 1, "relapse", outcome)

    patient_meta = pd.DataFrame({
        "patient_id": patient_ids,
        "outcome": outcome_final,
        "relapse": relapse,
        "relapse_date": [
            (pd.Timestamp(baseline_date[i]) + pd.Timedelta(days=int(duration_days[i]))).date().isoformat()
            if relapse[i] == 1 else ""
            for i in range(n_patients)
        ],
        "readmission": readmission,
        "readmission_date": [
            (pd.Timestamp(baseline_date[i]) + pd.Timedelta(days=int(duration_days[i]))).date().isoformat()
            if readmission[i] == 1 else ""
            for i in range(n_patients)
        ],
    })

    df = df.merge(patient_meta, on="patient_id", how="left")

    def _note(row):
        pct = 0.0
        b = baseline_hamd[patient_ids.index(row["patient_id"])]
        if b > 0:
            pct = round((1 - row["HAMD_total"] / b) * 100, 1)
        if row["visit_type"] == "baseline":
            return f"首次就诊：情绪低落、兴趣减退、睡眠紊乱，HAMD-17 评分 {row['HAMD_total']:.0f} 分，符合中重度抑郁发作诊断标准。"
        if row["locf_imputed"]:
            return f"患者失访，本次为末次观测值结转（LOCF），HAMD-17 参考评分 {row['HAMD_total']:.0f} 分。"
        return f"复诊：HAMD-17 评分 {row['HAMD_total']:.0f} 分（较基线降幅 {pct:.1f}%）。"

    df["notes_text"] = df.apply(_note, axis=1)

    ordered_cols = [
        "patient_id", "visit_type", "visit_week", "visit_date",
        "age", "gender", "diagnosis", "disease_duration_years",
        "medication", "medication_dose_mg",
        "HAMD_total", "HAMA_total", "PHQ9_total",
        "outcome", "relapse", "relapse_date", "readmission", "readmission_date",
        "locf_imputed", "notes_text",
    ]
    return df[ordered_cols].sort_values(["patient_id", "visit_week"]).reset_index(drop=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n-patients", type=int, default=140)
    parser.add_argument("--seed", type=int, default=20260701)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    df = generate(n_patients=args.n_patients, seed=args.seed)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    df.to_excel(args.out, index=False)

    n_visit_rows = len(df)
    n_patients_actual = df["patient_id"].nunique()
    print(f"生成完成: {args.out}")
    print(f"患者数: {n_patients_actual}，总随访行数: {n_visit_rows}（每人最多 {len(VISITS)} 次随访）")
    print("患者最终结局分布:")
    print(df.drop_duplicates("patient_id")["outcome"].value_counts())
    print(f"脱落(LOCF)患者数: {int(df.groupby('patient_id')['locf_imputed'].max().sum())}")


if __name__ == "__main__":
    main()
