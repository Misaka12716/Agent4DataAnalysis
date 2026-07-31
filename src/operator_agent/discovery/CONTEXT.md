# V8 Discovery Framework — Context

Scientific Discovery Agent 的多 agent 实现 (V7 → V8 系统性 bias 修正版).
原始设计文档在 `docs/v8_AGENT_DESIGN.md`; 这里只收"项目内部专用术语",
不重复设计内容.

## Language

### 阶段 / agent

**Discovery Run**:
一次 router 判为 `discovery` 后, supervisor 从 N1 跑到 N7 的完整端到端流程.
落盘成 `runs/discovery/<run_id>/`.
_Avoid_: session, job, task (后两个是 web/调度层概念)

**Lane**:
一条 hypothesis 在 verify-refine 循环里独占的执行通道 + 私有黑板.
一个 Discovery Run 内有 1..N 条 lane (== 通过新颖性闸门的 hypothesis 数).

**Top Agent / Top-level Coordinator**:
套在 `DiscoveryFlow` 外面、负责"和用户对话"的 session 层 (cancel /
progress / clarify), 仅当路由判为 `discovery` 时才"长这样"; 路由判为
`general` 时退化为透传.  代码: `top_agent.py`.

**Novelty Gate**:
在 hypothesis 阶段、开 lane **之前**对每条假设跑文献新颖性判定,
把已发表的 replication 挡在 verify 之前.  代码: `novelty.py` 的
`assess()` + `passes_gate()`.

### 数据流产物

**Public Blackboard**:
一次 Discovery Run 内**跨 stage 共享**的 in-process + on-disk 字典
(`runs/<run_id>/public_bb.json`), 持有 `requirement / profile /
clean_suggestions / hypotheses / novelty`.  生命周期: run 结束清空
(V8 §4).
_Avoid_: state, store, session_state

**Private Blackboard**:
每条 lane 一份的私有字典 (`runs/<run_id>/private_<hyp_id>.json`),
持有该 lane 自己的 `hypothesis / verify_result / refine_history /
review`.  Run 结束**保留**, 不清.

**Findings Archive** (Q1 决议, 2026-06-07):
跨 Discovery Run 的**长期产物目录** `findings_archive/`, 独立于
`runs/`.  每次 `compile.write_findings()` 成功后, `findings.yaml` 被
复制一份到 `findings_archive/<cohort_id>__<run_id>__<YYYYMMDD-HHMMSS>.yaml`.
存在意义: 让 `runs/<run_id>/` 可以激进清而不丢失用户唯一关心的产物.
具体保留策略 (Q2 决议, 2026-06-07):
- `runs/<run_id>/`: 删除条件 `age > 7 天 AND 总数 > 50`.  懒触发 ——
  `Supervisor.run()` 起手扫一遍, 没有 cron / 后台线程.  任何清理失败
  吞错并发 Error signal, 不阻塞新 run.
- `findings_archive/<file>.yaml`: 独立判定, `age > 365 天` 才删, 不看
  数量.
所有阈值落在 `discovery/config.py`, 环境变量可覆盖.
_Avoid_: findings repo, findings library, findings db (前两个像产品名,
db 暗示有索引)

### 信号 / 控制平面

**Signal Bus**:
synchronous pub/sub, 在 Discovery Run 内传 `Start / Done / Error /
Cancel` 等控制事件 (V8 §9).  与 blackboard 完全隔离 (控制信号不上黑板).
代码: `signals.py`.

**Cooperative Cancel**:
TopAgent 注入 stage 包装 (`_cancellable`), 在每个 stage **入口**检查
`threading.Event`, 命中即抛 `UserCancelledError`.  粒度 = stage 边界.
**不进入** stage 内部, 也**不杀** stage 已经起的 subprocess.

**LLM Stage Retry** (Q3 决议, 2026-06-07):
`chat_json` 5 次瞬时重试都失败时, **整个 LLM stage** (hypothesis /
refine / review / verify 内部 planner 调用) sleep 30s 后再调用一次.
**仅针对 chat_json 耗尽**, 不针对"LLM 拿到合法 JSON 但内容质量差"
(后者由 stage 内部 giveup / try-continue 逻辑处理, 不重试). 详见
ADR-0002.

**Planner Retry** (Q3 决议, 2026-06-07):
verify_stage 是唯一同时含 LLM (`plan_pipeline` 选算子) 和算子
(`execute_pipeline`) 的 stage.  当 `execute_pipeline` 抛异常 (算子
subprocess 崩) 时, 在 verify 内部重跑 `plan_pipeline` 一次 (sleep 30s,
带上次失败原因作 hint), 让 planner 选不同算子组合再 execute.  与
**LLM Stage Retry** 互不重叠 (那条管 LLM 调用本身挂掉, 这条管 LLM
说了一组算子但算子崩了).  详见 ADR-0002.

### 交互 / 用户协作

**Pre-flight Clarify**:
**Discovery Run** 启动**之前** TopAgent 向用户提的问题, 阻塞 run
启动直到用户答完 (或显式 cancel).  典型场景: 缺数据 / task 太短.

**Mid-run Clarify** (Q4 决议, 2026-06-07):
Discovery Run **进行中**某个 stage 暂停, 通过 `SignalType.Clarify`
事件向用户提问, 等用户答 (有限 timeout, 默认 10s) 或按预设默认放行.
当前唯一使用者是 N2 (清洗预览); 机制本身通用, 后续 stage 想中途问
用户也走这条.  详见 ADR-0003.

**Cleaning Approval** (Q4 决议, 2026-06-07):
Mid-run Clarify 的具体应用之一: N2 dry-run 完出 cleaning 预览, 通过
Clarify 事件给用户看 diff, 用户 10s 内可拒绝, 否则默认应用清洗
(opt-out).  CLI 同步 hook 模式直接同步问 hook; webapp/async 走 10s
timeout; headless 立即超时按默认走.  详见 ADR-0004.

### 数据流 (V8 §B.1.8 校正)

**Cleaned DataFrame** (Q4 决议, 2026-06-07):
N2 应用 cleaning snippet 后产出的 dataframe, 持久化到
`runs/<run_id>/cleaned_input.csv`.  从 N3 起, **下游所有 stage 看到的
就是这个**, 不是用户上传的原 CSV.  `dataset_hash` 在 verify_stage
里算的是它的 hash.  用户拒绝清洗时它退化为原 CSV 的拷贝, 路径不变.

**Cleaning Applied**:
`findings.yaml.reproducibility.cleaning_applied: List[str]` —— N2
真正应用的可执行 pandas snippet 列表, 用户拒绝时为空.  存在意义:
后续审计者可以从原 CSV 逐条 replay 这些 snippet 复现 cleaned_df.

### 文献检索 / Novelty (Q5 决议, 2026-06-07)

**Litcheck**:
对外文献检索的总称, 当前 `litcheck_stub.py` 占位, ADR-0005 落地为真
backend (Semantic Scholar 主, PubMed bio fallback).  N3 调它给
hypothesis 打 novelty 分.  目录 `litcheck_cache/` 与 `findings_archive/`
同级, 30 天 TTL.

**Novelty Score** (LLM-judged):
`0..1` 之间, 由 LLM 看 hypothesis card + 最多 top-5 retrieved abstract
后输出.  不是单纯 hit count.  缓存 key = `(query_hash, hypothesis_hash)`,
和 retrieval 缓存分开.

**Novelty Check Skipped**:
当外网不通 / backend 全失败时, litcheck 返回 `score=None`, N3 不卡门
hypothesis 但写 `metadata.novelty_check="skipped"`.  CI / 私网部署的
默认状态.  review_stage 可对此标记发警告.

**Strict Outbound**:
litcheck 出网的 query **只包含** `variables / primary_outcome /
edge_type / domain_hint (allow-list)`, 不含 user task 原文 / 数据集
文件名 / dataset_hash / cohort label.  PII / 合规默认.

### 数据 I/O (Q6 决议, 2026-06-07)

**DataLoadError**:
`discovery.errors.DataLoadError` —— 用户上传的文件无法读 (编码 / 大小
/ 类型) 时抛.  **不被 supervisor 包成 supervisor_uncaught**, 因为
discovery run 还没启动, 这是用户输入问题, 不是 agent 故障.  webapp
在启动 TopAgent 之前 catch 它并显示友好提示.

**Encoding Fallback Chain**:
`["utf-8-sig", "utf-8", "gbk", "latin-1"]` —— `charset-normalizer`
嗅探失败时按这个顺序逐个 try.  `latin-1` 永远不抛但可能产生乱码,
所以放最后.  实际生效的 encoding 写入 `data_profile.encoding`.

**Max CSV Bytes**:
500 MB 硬上限 (config: `MAX_CSV_BYTES`), 超了直接 `DataLoadError`,
不做 chunked 读.  超过这个尺度的工作流不属于 discovery 框架.

**NA Tokens**:
统一识别为 NaN 的字符串列表, 见 ADR-0006 §4.  `read_csv` 直接传
`na_values=NA_TOKENS, keep_default_na=True, low_memory=False`,
不做事后 `to_numeric(coerce)` 救场 (会静默丢非数字字符串).

### 错误分类 (Q7 决议, 2026-06-07)

**UserActionError** (基类):
用户主动行为导致 run 中止. 子类 `UserCancelledError` (cancel),
`UserRejectedCleaningError` (N2 拒绝), `UserTimeoutError` (clarify
超时未答, 保留).  supervisor 看到这一类不算 `error`, status 标
`cancelled`.

**DataInputError** (基类):
用户输入数据本身有问题导致 run 无法启动或继续. 子类
`DataLoadError` (Q6, 编码/大小/类型), `UnanalyzableDataError` (N1
检测无可分析列).  status 标 `rejected_input`.

**SystemError** (基类):
真系统/agent 故障 (LLM 重试耗尽, operator subprocess crash, 未知
异常).  status 标 `error`, supervisor 写完整 traceback 到 run.log.

**RunStatus** (5 档):
`ok` / `empty` / `cancelled` / `rejected_input` / `error`.  分别对应
"有 finding" / "跑完没 finding" / "用户取消" / "用户输入不行" / "系统
故障".  UI 单独渲染前四档, 第五档才显示 traceback.

**Findings Invariant** (Q7 决议, 2026-06-07):
每个 Discovery Run **永远写** `runs/<run_id>/findings.yaml`, 不论
status 是哪一档.  yaml 内的 `status` 字段告诉消费者发生了什么,
文件存在 == run 终结.  消费者不需要 parse log 判断状态.
`findings_archive/` 只归档 `status in {ok, cancelled, error} 且
findings 非空` 的 yaml.

**Reason vs Error**:
`RunResult.reason` 是用户可读原因 (任何非 ok 状态都有);
`RunResult.error` **只在 status==error 时**才有, 内容是
`supervisor_uncaught: ...` 完整字符串.  外部告警 / paging 系统
判 `if result.error is not None` 不会被取消事件假触发.

### Verify 证据 (Q8 决议, 2026-06-07)

**Evidence**:
verify_stage 内部 dataclass, 表示**单个 operator** 抽出的统计证据.
字段: `source_operator, effect, effect_kind, p, p_kind, n, n_kind,
raw`.  `effect_kind` 区分 logFC / fold_enrichment / OR 等;
`p_kind` 区分 raw_p / adj_p / permutation_p; 不再把不同语义的数
合一起.

**Per-operator Extractor Table**:
verify_stage 内部 mapping `_OPERATOR_EXTRACTORS: Dict[operator_id,
Callable]`.  每个已知 bio operator 有专属抽取器 (知道哪个 key 是
哪个语义).  未在表里的 operator 退化到 first-match 但**仍 per-operator
分组**, 不跨 operator merge.

**Primary Operator** (Verify 单值取数规则):
verify_stage 决定 `VerifyResult.effect / p / n` 单值的优先级:
(1) plan 标 `primary=true` 的 operator → (2) 第一个 e/p/n 全有的
Evidence → (3) 第一个 p 有值的 Evidence → (4) 都没 → None.
保持向后兼容.

**Evidence Per Operator**:
`VerifyResult.evidence_per_operator: List[Evidence]` —— 加性新字段,
按 plan order 保存所有 operator 的证据.  review_stage 当前只看单值,
未来可升级为多 evidence 综合判定 (如要求 gene-level + pathway-level
都显著).

### Webapp 集成 (Q9 决议, 2026-06-07)

**Web Session Registry**:
单进程内 `Dict[run_id, Session] + Lock`, 由 `web_session_registry.py`
封装.  webapp 启动 run 时注册, 终态后保留 1 小时再 evict.  显式假设
单进程 Flask 部署, 多 worker 留待未来.

**REST + Polling**:
webapp 用 RESTful endpoint (`/run/start /status /cancel /result
/clarifications`) + 前端 1Hz polling, 不上 SSE/WebSocket.

**Auto-cancel Previous Run**:
单用户同时只允许 1 个 in-flight run.  用户重新点 "开始" 时, webapp
对前一个 in-flight Session 调 `cancel()` 并等其到终态 (5s bound),
然后启新 run.  超时则 409 给新请求.

**Cooperative Cancel** (v1 唯一形态):
cancel 按钮 → `Session.cancel()` → cooperative cancel event →
当前 stage 在下次 checkpoint 抛 `UserCancelledError`.  v1 不做 hard
cancel (不 kill worker thread, 不 abort operator subprocess), 留 TODO.

### 修订与评审

**Refine Branch**:
Refine 阶段的四象限判定结果之一: `significant_weak_effect /
not_significant_wide_ci / not_significant_narrow_ci / converge`.
前三个走 LLM-driven 修订, 最后一个进 review.  V8 §addition 规定: 修订
必须用 dataset profile 里实际存在的列名, 禁止合成 `subgroup /
confounder` 占位词.

**Honest Giveup**:
Refine 阶段在 LLM 不可用 / 拒绝 / 输出非法 / 没 profile 时, **直接**
返回 `giveup` 而不是合成占位修订.  V8 §addition 决议.

## Relationships

- 一次 **Discovery Run** 含 1..N 条 **Lane** (== 通过 **Novelty Gate** 的 hypothesis 数)
- 每条 **Lane** 共享同一个 **Public Blackboard**, 各自独占一个 **Private Blackboard**
- 每次 **Discovery Run** 写一份 `findings.yaml` 进 `runs/`, 同步复制一份到 **Findings Archive**
- **Top Agent** 仅在路由 = `discovery` 时启 **Discovery Run**; `general` 路由直接走旧 legacy delegate

## Flagged ambiguities

- **"Session"** 这个词被 webapp / FastAPI / threading 各自使用不同含义.
  V8 内部用 **Discovery Run** 指代研究层概念, **Top Agent Session** 指代
  对话层概念 (cancel/progress 的句柄).  避免裸用 "session".

- **"Cancel" 的粒度**: V8 §9 的 `CancellationToken` 是**接口占位**,
  不抢占 stage 内部.  TopAgent 的 **Cooperative Cancel** 是真实可用的,
  但仍然只在 stage 边界生效, 不杀 subprocess.  两者**都不是**真正的
  preemption.

## Q-pending

按依赖顺序 (grilling 进行中):

- ✅ **Q1** (2026-06-07): Findings Archive 与 run 目录分离 → ADR-0001
- ✅ **Q2** (2026-06-07): retention 触发时机 + 双轨阈值 → 落 `config.py`
- ✅ **Q3** (2026-06-07): LLM 两层重试 (chat_json + 失败类别分流) → ADR-0002.
   预算 (per-session token cap) 本轮**不做**.
- **Q4** (next): N2 真清洗的数据流位置与授权机制
- **Q5**: N6 文献检索的 API 选型 + 缓存
- **Q6**: H2 CSV 健壮性
- **Q7**: H6 数字提取优先级
- **Q8**: webapp ↔ TopAgent 接线
