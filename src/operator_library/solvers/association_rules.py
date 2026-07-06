"""Association-rule mining solver (F12 / Q21 / Q27).

Backed by ``mlxtend.frequent_patterns``.  Mines frequent itemsets with
FP-Growth and extracts {antecedent → consequent} rules above min support
and min confidence thresholds.  Fully deterministic.

Input convention: a transactions DataFrame with two columns
  - ``items_col``   : ';'-separated items per row (e.g. drugs taken)
  - ``targets_col`` : ';'-separated outcomes per row (e.g. adverse events)
The solver builds a one-hot ``transactions`` DataFrame and mines rules
{drug} -> {adverse_event}.

中文说明
========
关联规则挖掘（典型用例：合并用药 → 不良反应）。
算法：``mlxtend.frequent_patterns.fpgrowth`` + ``association_rules``。

输入约定
========
DataFrame 必须有 2 个文本列：
- ``items_col``  ：";"-分隔的 antecedent items（例如同时服用的药物）
- ``targets_col``：";"-分隔的 consequent 候选（例如观察到的不良事件）

每行视为一笔 transaction，items_col ∪ targets_col 一起做 one-hot
后送进 FP-Growth；最后只保留**单元素 consequent ∈ targets 全集**
的规则（避免出现 {药} → {另一种药} 这种无意义跨域规则）。

输出
====
- ``rules_csv`` = ``association_rules.csv``：
  [antecedent, consequent, support, confidence, lift]
  - support    P(A ∪ C)   该规则出现的频率
  - confidence P(C | A)   given A 的条件概率
  - lift       P(C|A)/P(C) >1 = 正相关；=1 = 独立
- 按 antecedent + consequent 排序，方便人类查阅
- ``n_rules``：规则总数

静态参数
========
- ``min_support``    默认 0.05（5% 起步；数据稀疏可降到 0.01）
- ``min_confidence`` 默认 0.30（30%；探索性可放宽，临床决策应收紧）
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import pandas as pd
from mlxtend.frequent_patterns import association_rules, fpgrowth
from mlxtend.preprocessing import TransactionEncoder

from ..contract import ColumnMapping, Role, RoleSpec, SolverContract


# Contract 说明：
#   - items_col / targets_col 都是 TEXT 类型，必填
#   - static_params:
#       min_support   FP-Growth 的频繁项集阈值
#       min_confidence association_rules 的规则阈值
#     两者都默认偏宽松，适合"先看到东西再调"的探索流程
CONTRACT = SolverContract(
    name="association_rules",
    capability="F12_association_comorbidity_pattern",
    description=(
        "Mine frequent {antecedent}->{consequent} rules from a "
        "transactions DataFrame using FP-Growth + association_rules. "
        "Output association_rules.csv + summary_dict with per-rule "
        "confidence."
    ),
    roles={
        "items_col":   RoleSpec(Role.TEXT,
                                "';'-separated antecedent items per row"),
        "targets_col": RoleSpec(Role.TEXT,
                                "';'-separated consequent items per row"),
    },
    static_params={
        "min_support": 0.05,
        "min_confidence": 0.3,
    },
    output_files={"rules_csv": "association_rules.csv"},
    output_kind={"rules_csv": "s"},
)


class AssociationRulesSolver:
    contract = CONTRACT

    def __init__(self, min_support: float = 0.05,
                 min_confidence: float = 0.3):
        """中文：

        :param min_support:    频繁项集阈值。默认 0.05 = 5% 的 transaction
                               必须含此项才会被考虑。稀疏数据（事件率
                               <2%）建议调到 0.01。
        :param min_confidence: 规则置信度阈值。默认 0.3 = P(C|A) ≥ 30%。
                               临床决策建议收紧到 0.7+；探索性挖矿可
                               放宽到 0.1。
        """
        self.min_support = min_support
        self.min_confidence = min_confidence

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
            output_dir: Path) -> Dict[str, Any]:
        items_col   = mapping["items_col"]
        targets_col = mapping["targets_col"]

        # 构造每行的 transaction = items ∪ targets
        # - 用 ";" 切分 + strip 容忍空白
        # - dict.fromkeys 在保留首次顺序的前提下去重，防止 "drug_X;drug_X"
        #   这种重复条目把 support 算偏
        transactions: List[List[str]] = []
        for _, row in df.iterrows():
            items_raw   = row.get(items_col)
            targets_raw = row.get(targets_col)
            tokens: List[str] = []
            if isinstance(items_raw, str) and items_raw.strip():
                tokens += [t.strip() for t in items_raw.split(";") if t.strip()]
            if isinstance(targets_raw, str) and targets_raw.strip():
                tokens += [t.strip() for t in targets_raw.split(";") if t.strip()]
            transactions.append(list(dict.fromkeys(tokens)))

        # mlxtend TransactionEncoder 把 list[list[str]] 转 boolean 矩阵
        te = TransactionEncoder()
        oh = te.fit(transactions).transform(transactions)
        tdf = pd.DataFrame(oh, columns=te.columns_)

        # known-target items only become valid consequents
        target_universe = set()
        for tr in transactions:
            for tok in tr:
                target_universe.add(tok)
        # we'll filter rules where consequent ⊆ targets_col tokens
        target_universe = set()
        for raw in df[targets_col].dropna().astype(str):
            for tok in raw.split(";"):
                tok = tok.strip()
                if tok:
                    target_universe.add(tok)

        # FP-Growth 找所有 support ≥ min_support 的频繁项集；
        # association_rules 再生成所有 confidence ≥ min_threshold 的规则
        freq = fpgrowth(tdf, min_support=self.min_support,
                        use_colnames=True)
        rules = association_rules(freq, metric="confidence",
                                   min_threshold=self.min_confidence)

        # 过滤：只保留 **单元素 consequent** 且该元素属于
        # targets_col 全集 → 排除 "drug_A → drug_B" 这种跨域噪声
        # （我们关心的是"用药 → 不良事件"方向）
        def _ok(consequents):
            if len(consequents) != 1:
                return False
            return next(iter(consequents)) in target_universe

        rules = rules[rules["consequents"].apply(_ok)].copy()

        rules["antecedents_str"] = rules["antecedents"].apply(
            lambda s: "+".join(sorted(s)))
        rules["consequents_str"] = rules["consequents"].apply(
            lambda s: next(iter(s)))
        out = rules[[
            "antecedents_str", "consequents_str", "support",
            "confidence", "lift",
        ]].rename(columns={
            "antecedents_str": "antecedent",
            "consequents_str": "consequent",
        }).sort_values(["antecedent", "consequent"]).reset_index(drop=True)

        path = Path(output_dir) / CONTRACT.output_files["rules_csv"]
        out.to_csv(path, index=False)

        return {
            "rules_csv": str(path),
            "rules_df":  out,
            "n_rules":   int(len(out)),
        }


def get_solver(min_support: float = 0.05, min_confidence: float = 0.3):
    return AssociationRulesSolver(min_support=min_support,
                                   min_confidence=min_confidence)


def selftest():
    """Hand-built transactions where rule {drug_X} -> {event_E} is
    deterministic with confidence = 1.0 (every drug_X transaction
    contains event_E).

    中文：手搓 10 笔 transaction：
      - drug_X 出现 6 次（行 0,1,3,4,7,9），**全部** 伴随 event_E
        → 期望规则 {drug_X} → {event_E} 的 confidence = 6/6 = 1.0
      - drug_Y / drug_Z 出现但与 event 关系弱，不会构成强规则

    通过判定：在过滤后的规则表里能找到 {drug_X} → {event_E}，且其
    confidence 恰好等于 1.0（误差 < 1e-9）。
    """
    import tempfile
    df = pd.DataFrame({
        "RecordID": [f"R{i}" for i in range(10)],
        "drugs": ["drug_X", "drug_X;drug_Y", "drug_Y", "drug_X",
                  "drug_X;drug_Z", "drug_Z", "drug_Y", "drug_X",
                  "drug_Z", "drug_X;drug_Y;drug_Z"],
        "events": ["event_E", "event_E", "", "event_E",
                   "event_E", "", "", "event_E",
                   "", "event_E"],
    })
    diffs = []
    with tempfile.TemporaryDirectory() as tmp:
        out = get_solver(min_support=0.1, min_confidence=0.5).run(
            df=df,
            mapping=ColumnMapping({"items_col":   "drugs",
                                     "targets_col": "events"}),
            output_dir=Path(tmp),
        )
        rules = out["rules_df"]
        match = rules[(rules["antecedent"] == "drug_X")
                      & (rules["consequent"] == "event_E")]
        if match.empty:
            diffs.append("rule {drug_X} -> {event_E} not found")
        else:
            conf = float(match["confidence"].iloc[0])
            if abs(conf - 1.0) > 1e-9:
                diffs.append(f"rule {{drug_X}} -> {{event_E}} confidence "
                             f"expected 1.0, got {conf}")
    return {"ok": len(diffs) == 0,
            "summary": ("rule {drug_X} -> {event_E} recovered with "
                        "confidence = 1.0 on hand-built transactions"
                        if not diffs else f"{len(diffs)} mismatch(es)"),
            "details": {"diffs": diffs,
                        "tested": ["association_rules"]}}
