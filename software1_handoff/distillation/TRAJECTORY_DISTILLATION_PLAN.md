# Trajectory Distillation Plan (v5)

> 起草: 2026-05-11
> 状态: **草案 v5，等审核**
> v5 相对 v4 的改动:
>
> 1. **重新定位**: 这份 plan 的存在意义是支撑学长论文的三点贡献, 不再以"数据集交付"为主线
> 2. **新增 §11 轨迹建模方法** — jsonl 只是存储, 建模层做超图/粗细粒度/双轨迹/能量空间
> 3. **新增 §12 tool use 即插即用机制** — 学长指明的"核心中的核心", 必须想清楚才能动手
> 4. **新增 §13 准确率与证据** — 不做量化 bench, trajectory 本身就是证据
> 5. **聚焦精神科** — seed 任务库重新分配（15 精神科 + 10 生信 + 5 通用）
> 6. **砍 vega-lite 模板** (出图先只爬 GEO, 过段时间再扩展)
> 7. **砍 S4 SFT 实验** (bench 没定先不上学生模型)
> 8. **明确不上重 RL** — 套用现成 RL 技术没意义, 要么 zero-shot/ICL 即插即用, 要么轻量 DPO

---

## 0. 这份 plan 的存在意义 — 对齐学长的三点贡献

学长 2026-05-11 对话中明确指出：

> "三点贡献: 1.算子库的构建 2.轨迹蒸馏的学习, 这个轨迹需要建模  3.实验效果, 轨迹的学习是怎么学习, 这个方法要根据建模来"
>
> "tool use 是核心中的核心"
> "RL 的问题也很大, 新增了 tool 怎么办?"
> "理想状态是即插即用的 tools 调用机制"
> "如果上重的 RL, 我觉得就没啥意义了"
> "这块我们聚焦精神科"
> "轨迹最好不只是 csv" — 还要其他形式: 离散空间 / 能量空间 / 关键节点图化 / 超图 / 粗细粒度 / 双轨迹
> "benchmark 不是我们工作核心, 就是一个附带的"

对应到这份 plan:


| 学长贡献               | 在 plan 中的位置                                                                      |
| ------------------ | -------------------------------------------------------------------------------- |
| 1. 算子库构建           | **已基本完成**（32 算子 + selftest + audit + GDS6016 demo）, 仅余 §9 朴素贝叶斯小修和 §5.5 GEO 取图算子 |
| 2. **轨迹蒸馏 + 轨迹建模** | §3 数据集组成 + §4 schema + **§11 建模方法（新章节，核心）**                                      |
| 3. **轨迹学习方法**      | §12 tool use 即插即用机制 + §6 S4_alt（轻量学习方案）                                          |
| 准确率（用户视角）          | §13 — 不做量化 bench, trajectory 就是证据                                                |
| 工作领域聚焦             | §3.5 seed 任务分布 — 倾向精神科                                                           |


## 1. 核心理念 — 三个不要松动的设计前提

### 1.1 轨迹要"会想"，不只是"会跑"

每条 trajectory 必须能回答 5 个 reasoning 问题：


| Q                       | 字段                                             | 用途                  |
| ----------------------- | ---------------------------------------------- | ------------------- |
| Q1 拿到任务后 agent 怎么想的     | `plan.thinking`                                | 顶层规划信号              |
| Q2 为什么选这个 solver 而不是另一个 | `step.intent` + `step.alternatives_considered` | tool selection 信号   |
| Q3 这一步期望看到什么            | `step.expected`                                | "预期-实际" 差距 = 模式转移信号 |
| Q4 看到结果后怎么解读            | `step.analysis`                                | 数据→知识 转换            |
| Q5 为什么这就够了 / 还要下一步      | `step.next_action_reasoning`                   | 控制流信号               |


**reasoning 字段就是模式（mode）的载体**。学长 paper 要学的"分析模式"靠它落地。

### 1.2 工具调用必须即插即用（学长定义的"核心中的核心"）

任何能力扩展（新加算子）必须**无需重训 / 无需改 prompt 模板**：

- 现状: planner 调用前先读 `catalog.py` 渲染成 markdown 注入 prompt → **天然 zero-shot**, 加新算子立即可用 ✓
- 风险: 一旦上 SFT/RL 学生模型 → 学生模型被锁在训练时的 catalog → **即插即用性破坏** ✗
- 解决方向: 详见 §12

### 1.3 轨迹不只是 jsonl, 要建模

学长原话: "轨迹最好不只是 csv, 还有其他的, 例如 离散空间 / 能量空间 / 关键节点图化 / 超图 / 粗细粒度 / 双轨迹"

- **存储层** (jsonl): 详尽 raw 数据，源信号 — §4 schema
- **建模层** (derived views): 从 jsonl 派生 — §11 多种建模方法

数据生产和建模分离, 同一份 raw 可派生多种建模视图。

## 2. 现状能力画像（数据集要覆盖什么）

不变，沿用 v4。已知短板：呈现忠实度、错误恢复、L3 模糊任务、agent reasoning 采集。

## 3. 数据集组成

```
Trajectory Dataset
├─ D1 正常成功轨迹       ~75 条
│   30 seed × 难度均值 2.5 × 成功率 ~85%
│   含完整 reasoning + execution + verdict
│
├─ D2 自然失败 + 修复轨迹  ~25 条
│   主要来自 L3 模糊任务 + 脏数据 fixture
│   agent 失败 → 看 error → 重规划 1 次
│   不论第二轮成功失败都保留
│
└─ D3 难度对照轨迹         与 D1 共用切片
    同一 seed 在 L1/L2/L3 下的 reasoning 差异本身就是 mode 信号
```

### 3.5 Seed 任务分布 — 聚焦精神科

30 个 seed 按学长意见重新分配:


| 类别        | 数量     | 例                                                                                |
| --------- | ------ | -------------------------------------------------------------------------------- |
| **精神科临床** | **15** | PANSS 因子分、再入院 Cox、复发风险 KM、用药与结局关联、量表纵向变化、HAMD/HAMA 计算、CGI 评估、首发vs复发对比、共病分析、依从性影响 |
| 精神疾病生信    | 10     | GDS6016 (En2 KO ASD 模型) 已有, 加 GDS 上其他 brain/CNS 数据集: 抑郁、精分、自闭、阿尔兹海默 等            |
| 通用统计/ML   | 5      | 缺失/异常、相关、PCA、随机森林分类、KM 通用                                                        |


数据 fixture:

- **clean**: 现成
- **dirty_v1**: 列名加 trailing space / 大小写变体 / 中文备注列混入
- **dirty_v2**: 加 5-15% 缺失 / 1-2 个异常单位

dirty 主要作为 D2 自然失败的来源, 不每个 seed 都跑。

## 4. Trajectory Schema (v5)

字段全集（**reasoning 字段是核心**, verdict 简化为 3 元组, 砍 hash）:

```jsonc
{
  "trajectory_id": "T01_L1_20260511T1500",
  "seed_task_id": "T01",
  "difficulty": "L1|L2|L3",
  "data_fixture": "clean|dirty_v1|dirty_v2",
  "task_nl": "...",
  "domain": "psychiatry_clinical|psychiatry_bio|generic",

  "dataset_profile": {
    "n_rows": 200, "n_cols": 9,
    "columns": [{"name": "...", "dtype": "...", "n_missing": 0}, ...],
    "anomalies_observed": ["..."]
  },

  // ↓ 顶层 reasoning
  "plan": {
    "thinking": "用户问的是相关性, 但数据有缺失 ...",
    "alternatives_considered": ["..."],
    "rationale": "...",
    "steps": [...],
    "limitations": ["pairs returned unsorted; no sort/topk operator"]
  },

  "steps": [
    {
      "step_idx": 0,
      "solver": "missing_summary",
      "mapping": {...},
      "mapping_source": "rule_based|llm|manual|mixed",

      // ↓ step 级 reasoning（核心）
      "intent": "了解每列缺失多少, 决定后续填补策略",
      "expected": "缺失率 <10%, 集中在 1-2 列",
      "alternatives_considered": ["跳过直接填补（拒绝原因: ...）"],

      "outputs": {...},

      "observation": "Glucose 列缺失 14 行(7%), 其他列无缺失",
      "analysis": "符合预期, median imputation 足够",
      "next_action_reasoning": "进入填补步骤",

      "verdict": {
        "selftest_status": "passed",       // 查 _selftest.json 不重跑
        "invariant_check": {"passed": true, "checks": ["n_rows preserved", "id unique"]},
        "presentation_check": {"passed": true, "unmet_requirements": []}
      },

      "mode_label": "explore",
      "duration_ms": 87
    },
    ...
  ],

  "final": {
    "verdict_passed": true,
    "presentation_satisfied": false,
    "limitations_echoed": [...],
    "agent_summary": "完成相关性分析。最相关的几对(按 |r|): ... agent 在 final summary 里手工列出 top 3。"
  },

  "repair_history": [],
  "meta": {
    "teacher_model": "qwen3-8b",
    "verdict_protocol_version": "v1.0",
    "created_at": "2026-05-11T15:00:00+08:00"
  }
}
```

## 5. 默认决策（v5 已根据反馈收敛）


| #   | 问题              | v5 默认（已确认）                                               | 备注              |
| --- | --------------- | -------------------------------------------------------- | --------------- |
| D1  | 教师模型            | qwen3-8b + selftest 查表 + 不变量                             | —               |
| D2  | reasoning 字段怎么来 | **每步单独 probe 一次 LLM** ✓                                  | 用户已拍板           |
| D3  | verdict 协议      | 3 元组（selftest_status + invariant + presentation）         | —               |
| D4  | 失败样本来源          | 自然失败（L3 模糊任务 + 脏数据 fixture）                              | —               |
| D5  | mode 标签集        | 6 类: plan / explore / execute / verify / repair / report | —               |
| D6  | seed 规模         | 30, 偏精神科 15 + 生信 10 + 通用 5                               | —               |
| D7  | 出图              | **仅 GEO 在线爬, vega 模板留待后续** ✓                             | 用户已拍板           |
| D8  | 是否做 S4 SFT      | **先不做** ✓                                                | bench 没定先不上学生模型 |
| D9  | 轨迹建模            | §11 多种 derived view, 列出后挑 2-3 种实现                        | **新增, 待挑**      |
| D10 | tool use 即插即用   | 维持现状 zero-shot from catalog, 学习方案见 §12                   | **新增, 关键**      |


### 5.5 出图算子（v5 收窄: 仅 GEO 在线爬, 4 个）


| 算子                              | 输入                    | 输出                          |
| ------------------------------- | --------------------- | --------------------------- |
| `geo_value_distribution_figure` | GDS/GSE ID            | image_url, local_cache_path |
| `geo_pca_figure`                | GDS/GSE ID, group_col | image_url, local_cache_path |
| `geo_hclust_figure`             | GDS/GSE ID            | image_url, local_cache_path |
| `geo_volcano_figure`            | GDS/GSE ID, contrast  | image_url, local_cache_path |


实现: 拼 GEO 标准 URL → urllib 下载 PNG → 本地缓存 → 返回路径。
**没有任何绘图代码**, 全是 GEO 服务器现成的图。0.3 天工作量。

vega-lite 模板 / 临床图 → **v5 砍**, 留待后续迭代。

## 6. 六阶段任务分解（v5）


| 阶段                                         | 内容                                     | v5 估时    |
| ------------------------------------------ | -------------------------------------- | -------- |
| **S0** schema + 协议                         | 同 v4, dataclass + invariants + mode 规则 | 0.5d     |
| **S1** 不变量库 + selftest 查表                  | 不写独立重算 verifier                        | 0.5d     |
| **S2** seed 任务库                            | 30 seed × 3 难度, 偏精神科                   | 1d       |
| **S2.5** 出图算子                              | **仅 GEO 爬 4 个**                        | 0.3d     |
| **S3** trajectory runner + reasoning probe | 每步独立 probe reasoning                   | 2.5d     |
| **S5 (新增)** **轨迹建模 derived views**         | 见 §11, 选 2-3 种实现                       | **1-2d** |
| Phase X                                    | 朴素贝叶斯                                  | 0.5h     |
| **合计**                                     |                                        | **6-8d** |


S4 (SFT) **v5 砍**。

## 7. 风险与对策（v5 修订）


| 风险                       | 对策                                       |
| ------------------------ | ---------------------------------------- |
| reasoning 字段过于冗长重复       | 结构化 5 问, JSON 输出, 字数上限                   |
| 教师 qwen3-8b reasoning 飘  | 抽 30 条人工核对; 同任务跑两遍低/中温对照                 |
| GEO 网址变 / 限流             | 本地缓存; 失败时降级为"无图但有数据"                     |
| L3 全失败采不到正样本             | L1/L2 保底正样本; L3 失败本身就是好数据                |
| 脏数据让算子全崩                 | 渐进 (dirty_v1 列名变体, dirty_v2 才加缺失/异常)     |
| mode 标签 LLM 飘            | 规则 95%, LLM 仅在歧义时调, 6 选 1                |
| 轨迹建模选错方向                 | §11 列多种, 跟学长 align 后只实现 2-3 种            |
| **加新算子时旧 trajectory 失效** | catalog 字段进 trajectory.meta, 区分版本; 见 §12 |


## 8. 明确不做（v5 边界）

- 不做 matplotlib/seaborn 本地绘图
- 不做 vega-lite 模板（v5 砍，过段再做）
- 不做 multi-agent 编排
- 不做 PDF / 多轮对话
- 不发 benchmark 论文（学长明确"太烧 api"，详见 §13）
- 不做主动错误注入（用自然失败）
- 不做 32 个 verifier 独立重算
- **不上重 RL**（学长明确"套用现有技术没意义"）
- 不做 S4 SFT 学生模型（v5 砍，bench 没定先不上）
- 不接 GPT-4 / Claude API 作教师

## 9. 朴素贝叶斯小修

不变. `naive_bayes.py` + selftest, 凑齐 ≥10 ML 算法, 30 分钟。

---

## §11. 轨迹建模方法（v5 新增章节, **核心**）

> "轨迹最好不只是 csv. 还有其他的, 例如: 离散空间, 能量空间, 轨迹的关键节点图化, 或者你直接学我构建超图, 粗细粒度轨迹, 双轨迹等等"
> — 严彦东 2026-05-11

jsonl 是 raw 数据, 建模层从 jsonl 派生多种**derived view**, 每一种对应一种学习方法假设:

### 11.1 候选建模方法（共 6 种）


| 编号  | 建模方法              | 数据表示                                                                                              | 学习方法假设                                                | 实现难度 |
| --- | ----------------- | ------------------------------------------------------------------------------------------------- | ----------------------------------------------------- | ---- |
| M1  | **超图 hypergraph** | node = (state, action), hyperedge = 同 mode 的所有 step + 同 seed 的所有 difficulty 走法                    | hypergraph neural network / 节点分类 (预测下一 action)        | 中    |
| M2  | **粗细粒度双层**        | 粗 = mode 转移序列 (plan→explore→...→report), 细 = 每个 mode 内部的 step 链                                   | hierarchical sequence model; 粗层学策略, 细层学执行             | 中    |
| M3  | **双轨迹 paired**    | 成功 vs 失败 同 seed 的两条轨迹做 paired representation                                                      | contrastive learning / DPO                            | 低    |
| M4  | **离散空间 / 状态机**    | state = (前 N 步算子签名 + dataset profile bucket), action = solver_id                                  | discrete state-action transition matrix; 可用 IRL / 决策树 | 低    |
| M5  | **能量空间**          | 每条 trajectory 一个 energy score = f(verdict + reasoning quality + presentation fidelity), 低能量 = 好轨迹 | energy-based model; 采样时选低能量路径                         | 中    |
| M6  | **关键节点图**         | 提取每条轨迹的 ≤5 个"决策关键节点"(plan / 模式转移 / 修复点), 各 trajectory 共享关键节点构图                                    | graph attention; 重点关注 critical decision points        | 高    |


### 11.2 建议优先级（待学长确认）

根据 v5 的工作量预算 (S5 给 1-2 天), 我建议先做这 3 种, 兼顾"易实现"和"能匹配学长方向":

- **M3 双轨迹 paired**: 0.3d. 直接给 DPO 训练用。
- **M4 离散状态机**: 0.5d. 给出 mode transition matrix + 算子调用频率, 作为 baseline。
- **M1 超图**（学长原话 "直接学我构建超图"）: 0.5-1d. 用 PyG 或 DGL 的 hypergraph 表示, 给学长当 paper 主图。

M2/M5/M6 留待学长拍板再加。

### 11.3 建模产物的目录

```
distillation/trajectory/dataset/
  trajectory.jsonl                  # raw (S3 产出)
  modeling/
    M1_hypergraph/
      nodes.parquet                 # state-action pairs
      hyperedges.parquet            # mode/seed/difficulty groupings
      stats.md
    M3_paired/
      success_failure_pairs.jsonl
    M4_state_machine/
      transition_matrix.csv
      solver_freq.csv
      mode_transition.png
```

---

## §12. Tool use 即插即用机制（v5 新增章节, 学长"核心中的核心"）

> "tool use 是核心中的核心"
> "RL 的问题也很大, 新增了 tool 怎么办?"
> "理想状态是即插即用的 tools 调用机制. 新增了工具, 加了算子, 就可以直接用了"
> "要么就是一个即时的 agentic RL 的轻量化训练"
> "如果上重的 RL, 我觉得就没啥意义了, 套用现有技术就行"

### 12.1 现状已经做到一半

我们的 agent 现在是 **zero-shot from catalog**:

1. planner 每次跑前调 `catalog.build_catalog_for_planner()` → 渲染成 markdown 注入 prompt
2. agent 完全靠 prompt 里的 catalog 选算子
3. 加新算子 → 注册到 registry → catalog 自动包含 → **下一次 plan 即可用, 无需改任何代码或训练任何模型**

**这就是即插即用, 当前已实现。**

### 12.2 风险来源

即插即用性会**在以下情况被破坏**:


| 情况                               | 是否当前 plan 涉及          | 影响                         |
| -------------------------------- | --------------------- | -------------------------- |
| 上 SFT 学生模型                       | v5 已砍 S4              | 学生只学到训练时的 catalog → 加新算子失效 |
| 上 PPO / GRPO 重 RL                | v5 明确不做               | 同上                         |
| catalog 渲染长度溢出 prompt window     | 算子数 < 50 时无影响, 真做大了再说 | prompt 截断, 部分算子看不见         |
| LLM 训练数据里没有"读 catalog 选 tool"的能力 | qwen3-8b 已验证能做        | —                          |


### 12.3 若未来要上轻量学习方法, 怎么保持即插即用

学长方向是"**轻量化** agentic RL" 或 "**即时** training" — 不是 PPO 重训。
v5 不实现, 但 plan 必须想清楚以下三条出路:


| 出路                             | 思路                                                                                                         | 即插即用性                                     |
| ------------------------------ | ---------------------------------------------------------------------------------------------------------- | ----------------------------------------- |
| **O1 ICL + 轨迹 demonstration**  | 把 D1 中相关的 3-5 条 trajectory 当 few-shot example 注入 prompt                                                    | ✓ 完全保留, 加算子加 demo 即可                      |
| **O2 轻量 DPO 在"如何用 catalog"上**  | 训练数据是 (任务, catalog_view, good_plan) vs (任务, catalog_view, bad_plan); 模型学 **如何看 catalog 选**, 而非"用 cox 算 OS" | ✓ 训练时 catalog 用多种子集, 让模型学到 generalization |
| **O3 轻量 LoRA 在 reasoning 字段上** | 只蒸馏 reasoning (intent / analysis), 不蒸馏具体 solver_id                                                         | ✓ reasoning 是模式级抽象, 不绑算子                  |


**所有三条都保留即插即用**, 与学长"不上重 RL"的口径一致。

### 12.4 v5 在这一块的实际动作

- **必做**: trajectory 里记录每条 trajectory 当时的 **catalog 快照**（meta.catalog_signature: 算子列表+版本）, 这样后续学习方法能区分"训练时 catalog vs 推理时 catalog"
- **必做**: schema 显式区分 (`solver_id_at_train_time` vs `solver_id_at_inference_time`), 加新算子时旧 trajectory 仍可解析
- **不做**: 任何具体的训练 — 等数据集稳定 + 学长 align 后再开

---

## §13. 准确率与"证据"（v5 新增章节）

> 用户对话原文:
> "(^～^): 如果是验证我们的整个流程是否正确, 方法是否有保证, 我们是可测试的. 但是每一次用户都投入不同的东西, 然后让我们分析, 对于用户这一次的分析是否正确, 根本做不了我感觉."
> "(^～^): 最多是侧面验证. 因为没有 ground truth."
> "严彦东: 那就提供证据. 让用户自己觉得对不对吧."

**结论**: 不做量化 bench 来证明"分析对不对" — 没有 GT 没法测, 还烧 api。
**改为**: 让每条 trajectory 自身就是证据, 用户拿到能自己判断。

### 13.1 trajectory 即证据 — 字段对应表


| 用户疑问        | 看 trajectory 哪一段就能回答                                                 |
| ----------- | -------------------------------------------------------------------- |
| 你怎么理解我的问题的？ | `task_nl` + `plan.thinking` + `plan.alternatives_considered`         |
| 为什么选这些算子？   | 每个 step 的 `intent` + `alternatives_considered`                       |
| 算子算得对吗？     | `step.verdict.selftest_status` (链回 `VALIDATION_GUIDE.md` §1-§3 全部证据) |
| 数据形态对吗？     | `step.verdict.invariant_check` (行守恒/id唯一等)                           |
| 满足我说的呈现要求吗？ | `step.verdict.presentation_check` + `plan.limitations`               |
| 最终结论怎么读？    | `final.agent_summary`                                                |
| 你提到的细节有依据吗？ | 每步 `observation` + `analysis` 都附 outputs csv 路径, 可点开核                |


trajectory 本身**就是审计报告**。

### 13.2 用户反馈机制（轻量, 可选）

trajectory 末尾留一个 `user_feedback` 字段（空, 由用户后填）:

```jsonc
"user_feedback": {
  "verdict": null,                 // "correct" | "wrong" | "partially_correct" | null
  "notes": null,
  "feedback_at": null
}
```

- 不强制收集
- 收到的反馈进入第二批训练数据（不在 v5 范围内）

### 13.3 为什么不发 bench 论文（学长定调）

学长原话: "benchmark 不是我们工作核心, 就是一个附带的哈, 不单独发布 benchmark 的论文, 太烧 api 了, 要测太多."

接受。bench 只作 §6 S5 的副产品 (transition_matrix.csv / solver_freq.csv), 用来支撑学长 paper 的 method section, 不单独发表。

---

## 附 A: 三章新内容与学长原话的对照


| 学长原话             | 落到 plan                                    |
| ---------------- | ------------------------------------------ |
| 三点贡献 1 算子库       | 已基本完成 + §9 朴素贝叶斯小修                         |
| 三点贡献 2 轨迹蒸馏 + 建模 | §3/§4 (jsonl) + **§11 (建模 derived views)** |
| 三点贡献 3 学习方法依赖建模  | §6 S5 + **§12 (tool use 即插即用)**            |
| 轨迹不只是 csv        | §11 6 种建模, 至少做 3 种                         |
| tool use 是核心     | **§12 单独章节**                               |
| 不上重 RL           | §8 明确, §12.3 给出 3 条轻量出路                    |
| 即插即用             | §12.4 必做 catalog_signature 字段              |
| 聚焦精神科            | §3.5 15/10/5 分布                            |
| bench 不发论文       | §13.3                                      |
| 让用户自己看证据         | §13.1 字段对应表                                |


## 附 B: v4 → v5 改动总表

**新增**:

- §0 顶层定位（对齐学长三贡献）
- §11 轨迹建模方法（**核心**, 6 种 derived views）
- §12 tool use 即插即用（**核心**）
- §13 准确率与证据
- 附 A 学长原话对照

**修改**:

- §3.5 seed 任务分布偏精神科
- §5 默认表加 D9/D10
- §5.5 出图只爬 GEO, 4 个
- §6 砍 S4 加 S5

**砍掉**:

- vega-lite 临床图模板
- S4 SFT 学生实验
- 32 个 verifier 独立重算（v4 已砍, 留作记录）
- 主动错误注入（v4 已砍）

## 附 C: 待你拍板的 3 项

1. **§11 建模优先级** — 默认 M1(超图) + M3(双轨迹) + M4(状态机). 是否改为别的组合?
2. **§3.5 精神科比例** — 默认 15+10+5. 是否调整?
3. **§5 默认表全部按 v5 走** — 是否还有想推翻的?

不拍板我按 v5 默认走, 从 S0 开始。