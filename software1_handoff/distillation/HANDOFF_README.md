# Software 1 算子框架 — 交付包 README

> 打包时间: 2026-05-12
> 适用对象: 学长（严彦东）
> 包内容: 32 个数据分析算子 + 算子调用框架（人手动 / LLM 自动）+ 验证证据 + demo 脚本
>
> 这份 README 回答 4 个问题：
> 1. **算子在哪里？** 怎么直接 import 调用单个算子？（§2）
> 2. **人手动调多个算子的框架在哪里？** 怎么跑 Flask UI demo？（§3）
> 3. **LLM 自动调算子的框架在哪里？** 怎么跑端到端 demo？（§4）
> 4. **环境怎么搭？** 怎么验证装得对？（§1, §5）

---

## 0. 30 秒上手

```bash
# 1) 装环境（建议 venv 隔离）
python -m venv .venv && source .venv/bin/activate    # Linux/macOS
# 或: .\.venv\Scripts\activate                       # Windows
pip install -r handoff_requirements.txt

# 2) 配 LLM key（仅 LLM agent demo 需要，单算子和 pipeline 不用）
copy .env.example .env       # Windows
# 或: cp .env.example .env   # Linux/macOS
# 编辑 .env，填入 DashScope/OpenAI 兼容的 API_KEY 和 BASE_URL

# 3) 验证算子全部可用（不需要联网）
python -m distillation.scripts.run_software1_solver_tests
# 期待: passed=33+, fail=0

# 4) 跑人手动调用 4 步 pipeline 的 demo
python -m distillation.scripts.run_software1_pipeline_demo
# 期待: 两条 pipeline (PANSS + 化验 EDA) 都打印 overall_ok=True

# 5) 跑 LLM 端到端 demo（需要 LLM key）
python -m distillation.software1_agent run \
  --task "请先检查每列缺失，然后给出 Pearson 相关矩阵" \
  --csv  benchmark/Software1_Bench/F13_outlier_reference_range_detection/selfcon_reference_range_audit/inputs/lab_panel.csv \
  --out  _agent_runs

# 6) 生信端到端 demo（需要 LLM key）
python -m distillation.scripts.bio_agent_demo_geo2r_aligned
# 期待: Spearman ρ ≈ 1.0, top-200 Jaccard = 1.0 vs GEO2R
```

---

## 1. 环境与依赖

### 1.1 系统要求
- Python 3.10 - 3.11 (3.10 测试最充分)
- 操作系统: Linux / macOS / Windows 都通过测试
- 联网: 仅 LLM agent 部分需要（调用 LLM API）；算子层与 pipeline 层完全本地

### 1.2 依赖清单

见 `handoff_requirements.txt`。核心依赖（按用途分组）：

```
# 算子层（必装）
pandas>=2.0          # 数据结构
numpy>=1.24          # 数值
scipy>=1.10          # 统计 / 假设检验
scikit-learn>=1.3    # ML / KNN / SVM / LR / RF
statsmodels>=0.14    # multipletests / 多重校正
lifelines>=0.27      # Cox 回归 / KM 生存分析
mlxtend>=0.22        # 关联规则 (FP-Growth)

# Pipeline UI 层（人调用 demo 需要）
flask>=3.0           # Web UI
python-dotenv>=1.0   # .env 配置
requests>=2.31       # LLM HTTP

# LLM Agent 层（自动调用需要）
# 仅用 OpenAI 兼容 HTTP，无需 openai SDK，依赖 requests 即可

# 可选（启用后对应算子才能跑）
xgboost>=2.0         # xgboost 算子
lightgbm>=4.0        # lightgbm 算子
jieba                # 中文 text_features
```

### 1.3 LLM 配置 (`.env`)

仅在跑 LLM agent demo 时需要。复制 `.env.example` 为 `.env`，填入兼容 OpenAI Chat 接口的 endpoint。

`distillation/software1_pipeline_demo_app/llm_client.py` 实际读取的环境变量（按优先级，第一个非空生效）：

| 用途 | 接受的变量名 |
|---|---|
| API key | `ANTHROPIC_API_KEY` 或 `OPENAI_API_KEY` |
| Base URL | `ANTHROPIC_API_BASE_URL` 或 `OPENAI_API_BASE` 或 `OPENAI_BASE_URL` |
| Model | `ANTHROPIC_MODEL` 或 `LLM_MODEL`（默认 `claude-opus-4-7`） |

`.env` 推荐写法：

```env
# 阿里云 DashScope 示例（已验证 qwen3-8b 可用）
ANTHROPIC_API_KEY=sk-xxxxxx
ANTHROPIC_API_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
ANTHROPIC_MODEL=qwen3-8b

# 或：纯 OpenAI 兼容写法
# OPENAI_API_KEY=sk-xxxxxx
# OPENAI_API_BASE=https://api.openai.com/v1
# LLM_MODEL=gpt-4o-mini
```

**重要 — Qwen3 注意**: 客户端已为 `qwen3` / `qwen-3` 字样的模型自动加 `enable_thinking=False`，否则 DashScope 非流式调用会返回 HTTP 400。其他模型（gpt-4o-mini / claude / 本地 vLLM）只要 OpenAI 兼容即可，无需改代码。

**网关注**: 名字虽写 `ANTHROPIC_*`，但 `llm_client.py` 走的是 OpenAI-style `/chat/completions`（不是 Anthropic Messages API）。命名只是历史延续，可对接任何兼容 OpenAI Chat 的 endpoint。

---

## 2. 算子库（Solver Library）

### 2.1 算子在哪里

```
distillation/software1_solver/
├── contract.py              # SolverContract 抽象（Role/RoleSpec）
├── profiler.py              # DataFrame → LLM 友好的 schema 摘要
├── mapper.py                # rule-based / LLM 列映射
├── runner.py                # 单算子单任务执行
├── pipeline.py              # 多算子链式执行
├── selftest.py              # 出厂自检框架
├── comparator.py            # 与 GT csv/json 对比
└── solvers/                 # ←—— 32 个算子全在这里
    ├── data_governance.py        # missing_summary, fillna_median, outlier_iqr_flag
    ├── descriptive_stats.py      # describe_full, distribution_histogram
    ├── correlation.py            # pearson / spearman / kendall
    ├── normality_test.py         # 正态性检验
    ├── multiple_correction.py    # Bonferroni / BH-FDR
    ├── hypothesis_tests.py       # welch_t / mann_whitney / chi2 / anova / kruskal
    ├── logistic_regression.py
    ├── tree_models.py            # random_forest / hist_gbdt / xgboost / lightgbm
    ├── svm_classifier.py
    ├── knn_classifier.py
    ├── cox_regression.py
    ├── association_rules.py      # FP-Growth
    ├── reference_range_flag.py   # 参考区间打标
    ├── panss_factor_score.py     # PANSS 因子分
    ├── panss_trajectory_responder.py
    ├── time_series_features.py
    ├── propensity_score_matching.py
    ├── text_features.py          # TF-IDF
    ├── metadata_parser.py
    ├── data_quality_check.py     # consistency_check
    └── bio/                      # ← 生信专用
        ├── soft_parser.py            # GEO SOFT/GDS 解析
        ├── probe_to_gene.py          # 探针→基因聚合
        ├── limma_deg.py              # limma 差异表达（Smyth EB）
        ├── probe_deg_collapse.py     # GEO2R 风格 min(adj_p) 收敛
        ├── pca_decomposition.py      # 样本×基因 PCA
        ├── hierarchical_cluster.py   # 样本聚类
        └── pathway_enrichment.py     # Fisher / 超几何（自带 MSigDB Hallmark）
```

完整 32 个算子的 id → 中文描述见 `distillation/software1_pipeline_demo_app/registry.py` 里的 `_solvers()` 函数。

### 2.2 直接调用单个算子（不需要任何框架）

每个 solver 模块都暴露 `get_solver(...)` 工厂函数，返回一个**可直接调用**的对象：

```python
# 例 1: 计算 Pearson 相关 + 配对长表
import pandas as pd
from distillation.software1_solver.solvers import correlation
from distillation.software1_solver.contract import ColumnMapping

df = pd.read_csv("benchmark/Software1_Bench/F13_outlier_reference_range_detection/"
                 "selfcon_reference_range_audit/inputs/lab_panel.csv")
solver = correlation.get_pearson_solver()
mapping = ColumnMapping(roles={"numeric_columns": [
    "WBC_10e9_per_L", "Hemoglobin_g_per_L", "Platelet_10e9_per_L",
]})
out = solver.run(df, mapping, output_dir="_demo_out")
# out 是一个 dict：{'matrix_csv': ..., 'pairs_csv': ...}
```

```python
# 例 2: 跑 Cox 回归
from distillation.software1_solver.solvers import cox_regression
solver = cox_regression.get_solver()
mapping = ColumnMapping(roles={
    "id_col": "PatientID",
    "time_col": "time_days",
    "event_col": "event_readmitted",
    "covariates": ["Age", "is_male", "panss_baseline"],
})
out = solver.run(df, mapping, output_dir="_demo_out")
# out: {'coefficients_csv': ..., 'metrics_json': ..., 'metrics_dict': {...}}
```

### 2.3 算子契约（SolverContract）

每个算子都声明它需要哪些「角色 (Role)」的列，而**不是**写死列名。这是 LLM agent 能跨任意输入数据集的基础：

```python
# distillation/software1_solver/contract.py
class Role(str, Enum):
    ID = "ID"
    NUMERIC_TARGET = "NUMERIC_TARGET"
    BINARY_TARGET  = "BINARY_TARGET"
    DATETIME = "DATETIME"
    NUMERIC_LIST = "NUMERIC_LIST"
    GROUP = "GROUP"
    PARAMS = "PARAMS"
    # ...

# 每个 solver.contract 列出它要什么 role：
solver.contract.roles  # → list[RoleSpec]
```

`mapper.py` 负责把"数据集实际列名"绑到这些 role 上，可以走规则也可以走 LLM。

---

## 3. 人手动调多算子的框架（Pipeline + Flask UI）

### 3.1 框架在哪里

```
distillation/software1_pipeline_demo_app/
├── app.py                  # Flask 入口（Web UI）
├── __main__.py             # `python -m ...` 启动入口
├── registry.py             # 32 算子 id → 工厂表（必看）
├── runner.py               # 单步执行（含 LLM/规则映射）
├── run_spec.py             # JSON spec → Pipeline 对象
├── mapping_engine.py       # 三段式列映射（user/LLM/rule）
├── solver_overlays.py      # 暴露算子构造参数为 PARAMS role 的 wrapper
├── llm_client.py           # OpenAI 兼容 HTTP 客户端
├── ui_catalog.py           # UI 用的算子 catalog 渲染
├── templates/              # HTML
└── static/                 # CSS + JS（含拖拽 pipeline builder）
```

### 3.2 用法 1: Python 直接拼 Pipeline

最直接的"人调用"方式，不开 Web UI：

```python
from distillation.software1_solver import Pipeline, PipelineStep
from distillation.software1_solver.solvers import (
    panss_factor_score, normality_test, multiple_correction,
)

pipe = Pipeline([
    PipelineStep(
        name="step1_score_panss",
        solver=panss_factor_score.get_solver(),
        mapping_override={
            "id_col": "PatientID",
            "time_col": "VisitWeek",
            "positive_items": [f"P{i}" for i in range(1, 8)],
            "negative_items": [f"N{i}" for i in range(1, 8)],
            "general_items":  [f"G{i}" for i in range(1, 17)],
        },
    ),
    PipelineStep(
        name="step2_normality",
        solver=normality_test.get_solver(0.05),
        from_step=0,                  # 用上一步的输出做输入
        from_csv_key="scored_csv",
        mapping_override={"test_columns": ["Positive_score", "Negative_score",
                                            "General_score", "Total_score"]},
    ),
    PipelineStep(
        name="step3_correction",
        solver=multiple_correction.get_solver(0.05),
        from_step=1,
        mapping_override={"test_id_col": "column", "p_value_col": "shapiro_p"},
    ),
])

import pandas as pd
df = pd.read_csv("benchmark/Software1_Bench/.../panss_items.csv")
result = pipe.run(df, output_dir="_pipeline_out")
print(result.overall_ok, [s.name for s in result.steps])
```

完整可跑 demo: `distillation/scripts/run_software1_pipeline_demo.py`，跑出来两条 pipeline (PANSS workflow + 化验 EDA)。

### 3.3 用法 2: Flask UI（拖拽建管线）

```bash
python -m distillation.software1_pipeline_demo_app
# 浏览器访问: http://127.0.0.1:8765
```

UI 里可以：
1. 上传 csv
2. 拖拽算子摆 pipeline
3. 每个算子可在表单里手填参数 / 留空让 LLM 自动填
4. 点"运行" → 看每步输出表格 + 下载 csv

底层就是把 UI 的 form 翻译成跟 §3.2 一样的 PipelineStep。

### 3.4 用法 3: JSON spec → Pipeline

适合脚本化批量跑：

```python
spec = {
    "rationale": "PANSS workflow",
    "steps": [
        {"solver": "panss_factor_score", "from": "initial",
         "mapping": {"id_col": "PatientID", "time_col": "VisitWeek",
                     "positive_items": ["P1","P2","P3","P4","P5","P6","P7"],
                     "negative_items": ["N1","N2","N3","N4","N5","N6","N7"],
                     "general_items":  ["G1","G2","G3","G4","G5","G6","G7","G8",
                                        "G9","G10","G11","G12","G13","G14","G15","G16"]}},
        {"solver": "normality_test", "from": "previous",
         "mapping": {"test_columns": ["Positive_score","Negative_score",
                                       "General_score","Total_score"]}},
        {"solver": "multiple_correction", "from": "previous",
         "mapping": {"test_id_col": "column", "p_value_col": "shapiro_p"}},
    ],
}

from distillation.software1_pipeline_demo_app.run_spec import build_pipeline_from_spec
pipe = build_pipeline_from_spec(spec)
result = pipe.run(df, output_dir="_pipeline_out")
```

---

## 4. LLM 自动调算子的框架（Software1 Agent）

### 4.1 框架在哪里

```
distillation/software1_agent/
├── __main__.py     # CLI 入口 (python -m distillation.software1_agent run ...)
├── agent.py        # solve_task() 主入口：plan → execute → 落盘
├── planner.py      # PLANNER_SYSTEM prompt + 调 LLM 出 JSON spec
├── catalog.py      # 把 32 算子渲染成给 LLM 看的 markdown
└── _smoke_e2e.py   # 烟雾测试
```

### 4.2 工作流（重要）

```
任务自然语言 + 输入 csv
        │
        ▼
┌──────────────────┐
│ profiler.py      │  ← DataFrame → 列名/dtype/缺失/sample 摘要
└────────┬─────────┘
         │
         ▼
┌────────────────────────────┐
│ catalog.py                 │  ← 32 算子的 id+用途+contract 渲染成 markdown
└────────┬───────────────────┘
         │
         ▼
┌───────────────────────────────────┐
│ planner.py: LLM 调用              │  ← system prompt 8 条 rule
│ → 出 JSON spec (rationale + steps) │     (mapping fidelity / presentation fidelity 等)
└────────┬──────────────────────────┘
         │
         ▼
┌────────────────────────────────────┐
│ runner.py: 逐步执行                │
│ for each step:                    │
│   mapping_engine 解析列 (用户/LLM/规则)
│   make_solver(...).run(df, mapping)
│   把主产物 csv 当下一步输入        │
└────────┬───────────────────────────┘
         │
         ▼
落盘 manifest.json + 每步产物 csv
        ↓
返回 SolveResult (含 plan + steps 列表 + ok flag)
```

**Tool use 即插即用**: catalog.py 每次调用前实时渲染 → 加新算子注册到 `registry.py` 后，下一次 plan 立即可用，**无需任何重训**。这是学长说的"即插即用"的当前实现。

### 4.3 用法 1: CLI

```bash
python -m distillation.software1_agent run \
  --task "请先检查每列缺失，然后用中位数补齐缺失，最后给出 Pearson 相关矩阵和最相关的几对" \
  --csv  benchmark/Software1_Bench/F13_outlier_reference_range_detection/selfcon_reference_range_audit/inputs/lab_panel.csv \
  --out  _agent_runs
```

输出会落到 `_agent_runs/<run_id>/`：
- `plan.json` — LLM 出的 JSON spec（含 rationale）
- `manifest.json` — 每步执行的完整记录（mapping 来源、产物路径、状态）
- `pipeline_output/<step_idx>_<solver>/` — 每步的产物文件夹

### 4.4 用法 2: Python API

```python
from pathlib import Path
from distillation.software1_agent import solve_task

res = solve_task(
    task="正态性检验+多重比较校正",
    csv_path=Path("data.csv"),
    output_dir=Path("_agent_runs"),
    use_llm_mapping=True,        # False 则只用规则映射
    step_overrides=[             # 可选：硬钉某些 mapping
        {},                      # step 0 不动
        {"test_id_col": "column", "p_value_col": "shapiro_p"},  # step 1
    ],
)
print(res.ok, res.run_dir, res.plan.rationale)
```

`step_overrides` 是为了对付小模型偶尔漏 mapping 的情况，宿主脚本可以"钉死"关键参数。

### 4.5 端到端 demo（生信，已验证）

```bash
# 一般生信流程（GDS6016 ASD 模型，En2 KO vs WT，n=6）
python -m distillation.scripts.bio_agent_demo

# GEO2R 对齐流程（probe→DEG→收敛到 gene→富集，与 NCBI GEO2R 标准对齐）
python -m distillation.scripts.bio_agent_demo_geo2r_aligned
```

后者会跑完后自动跟 GEO2R 官方差异基因表（在 `benchmark/.../references/GSE51612.top.table.tsv`）做对比，输出：
- Spearman ρ on `-log10(adj_p)` ≈ **1.000**
- Top-200 Jaccard = **1.000**

—— 即"LLM 自动规划的 pipeline" 与"GEO2R 标准做法"在前 200 差异基因上**完全一致**。

---

## 5. 验证证据（"算子算得对"的证据在哪）

完整的多层验证策略见 `distillation/VALIDATION_GUIDE.md`，简版：

### L1 算子级（出厂自检）

```bash
# 跑全部算子的 selftest（不需要联网）
python -m distillation.scripts.run_software1_solver_tests

# 输出会写到 _selftest/selftest_<时间戳>.json
# 用法：每个 solver 模块自带一个 selftest()，
#       用手搓的最小 fixture 跑一遍，对比解析解或独立库的输出
```

L1 期望全过 (passed=33+, fail=0)。这是"算子内部代码对不对"的证据。

### L2 数据集级（GDS6016 全算子审计）

```bash
# 把全部 32 个算子在 GDS6016 真实生信数据上各跑一遍
python -m distillation.scripts.audit_gds6016_all_operators
```

输出 `benchmark/Software1_Bench/real_medical_data/_all_ops/<时间戳>/`:
- `report.md` — 32 算子结果对照表
- `manifest.json` — 每算子的 input/output/状态
- 各算子产物 csv

这是"算子在真实数据上能不能跑"的证据。

### L3 外部金标对比（生信 GEO2R）

```bash
python -m distillation.scripts.audit_gds6016_geo2r_aligned
```

把我们的 limma + 收敛 vs GEO2R 官方 TSV 比 Spearman ρ + Jaccard。
当前 Spearman ρ = 0.99999879，top-200 Jaccard = 1.000。
这是"和黄金标准方法的差异"的证据。

---

## 6. 核心 demo 脚本一览

| 脚本 | 用途 | 联网? |
|---|---|---|
| `scripts/run_software1_solver_tests.py` | 跑所有算子的 selftest | 否 |
| `scripts/_save_selftest.py` | 保存 selftest 结果到 json | 否 |
| `scripts/run_software1_pipeline_demo.py` | 人调 4 步 pipeline (PANSS + 化验) | 否 |
| `scripts/audit_gds6016.py` | 6 个生信算子在 GDS6016 上跑 | 否 |
| `scripts/audit_gds6016_geo2r_aligned.py` | 与 GEO2R 对齐的生信审计 | 否 |
| `scripts/audit_gds6016_all_operators.py` | 32 算子全在 GDS6016 上跑 | 否 |
| `scripts/_run_bio_selftests.py` | 生信算子专项 selftest | 否 |
| `scripts/_ping_llm.py` | LLM 连通性测试 | 是 |
| `scripts/bio_agent_demo.py` | 标准生信 LLM agent demo | 是 |
| `scripts/bio_agent_demo_geo2r_aligned.py` | GEO2R 对齐 LLM agent demo | 是 |
| `scripts/run_software1_pipeline_demo.py` | 多 pipeline 串联手动 demo | 否 |

---

## 7. 已验证的 LLM agent run（参考输出）

`distillation/software1_agent/_runs/` 下已经包含 6 个跑通的 LLM agent run：

| run_id | 任务 | 步数 | 状态 |
|---|---|---|---|
| `20260510T081556_71e5db30` | 缺失 + 中位数填补 + Pearson | 3 | ok |
| `20260510T081634_ced21891` | PANSS 因子分 + 总分 | 1 | ok |
| `20260510T081641_34aad551` | 参考区间打标 | 1 | ok |
| `20260510T081656_36beb62a` | 正态性 + BH-FDR | 2 | ok |
| `20260510T081702_df7252e8` | Cox 再入院风险 | 1 | ok |
| `20260510T081759_1f6388ba` | 描述统计 + 直方图 | 2 | ok |

每个 run 目录下有 `plan.json`（LLM 出的规划）+ `manifest.json`（执行流水）+ 各步产物。
学长可直接打开 `manifest.json` 看 agent 的完整决策链。

---

## 8. 想看更多

- `distillation/VALIDATION_GUIDE.md` — 多层验证策略 + 每个算子的可信证据
- `distillation/TRAJECTORY_DISTILLATION_PLAN.md` — 后续轨迹蒸馏与建模 plan v5（含超图/双轨迹/即插即用 tool use 等设计）

---

## 9. 已知限制 / 后续计划

1. **xgboost / lightgbm** 是可选依赖，未安装时对应算子会在调用时抛清晰报错；其他 30 个算子不受影响
2. **出图算子**: 当前没有，正在规划"GEO 在线爬图 + vega-lite 模板"两条路（不本地写绘图代码），见 plan v5 §5.5
3. **轨迹蒸馏**: 6 个 LLM agent run 是 v4 数据，下一步是按 plan v5 升级 trajectory schema（加 reasoning 字段 + verdict 三元组 + mode label），预计 1-2 周

---

## 10. 联系 / 反馈

如果跑不通：

1. 先跑 `python -m distillation.scripts.run_software1_solver_tests`，看是不是单算子层有问题
2. 再跑 `python -m distillation.scripts.run_software1_pipeline_demo`，看 pipeline 拼装层
3. 最后跑 `python -m distillation.scripts._ping_llm`，看 LLM 配置
4. 都过了再跑端到端 agent demo

把卡住的那一步的报错完整发给我即可。
