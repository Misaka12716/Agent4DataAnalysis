# PickU 页面级目标模板对照

> 目标：把当前 PickU 的每个功能页，映射到更适合的参考页面和改版模板。  
> 结论先行：不要整站套一个模板。建议统一一个 PickU Research OS 壳层，再按页面类型分别采用 6 类模板。

## 1. 总体模板选择

PickU 最适合的主模板不是单一竞品，而是：

**PickU Research OS = Findin 的科研工作流组织 + ReadPaper 的文献资产管理 + Hex/Benchling 的项目资产工作台 + ScienceOne/磐石的可信科研品牌气质。**

建议统一成以下 6 类页面模板：

| 模板 | 适用页面 | 主要参考 |
|---|---|---|
| T1 研究驾驶舱 | 首页、任务、热点摘要、继续研究 | TXYZ 应用首页、Findin 登录后首页、ScienceOne workflow |
| T2 三栏 AI 工作台 | 对话分析、PDF 阅读、方法抽取、AI 云盘/助手 | TXYZ 输入区、ReadPaper 笔记、SciSpace Chat PDF、Findin 阅读 |
| T3 项目资产中心 | 项目、文件、数据集、模型、成员、会话、任务 | Benchling、Hex、ReadPaper 文献管理 |
| T4 证据检索与图谱 | 论文搜索、热点、文献追踪、知识图谱 | Semantic Scholar、Elicit、Connected Papers、AMiner |
| T5 临床/数据质量仪表盘 | 临床分析、数据质量、患者/风险/共病/报告 | CBK、Benchling、现有临床页结构 |
| T6 成果工作室 | 综述、图表、PPT、报告、思维导图 | Findin 综述/科研绘图、SciSpace Writer、ReadPaper AI 辅写 |

视觉气质建议以 **Findin + ReadPaper** 为日常产品主基调：白底、深蓝文本、克制蓝色行动色、少量青绿色证据色。ScienceOne/磐石适合登录页、官网首屏和首页上方品牌区，不适合所有工作台都铺大面积深蓝。

## 2. 页面逐项对照

| 当前页面 | 推荐目标模板 | 主要参考截图 | 改造重点 |
|---|---|---|---|
| `01-data-analysis.png` | T2 三栏 AI 工作台 | TXYZ 应用首页、Hex/Deepnote AI notebook | 左侧项目/数据，中间分析目标与结果，右侧运行过程/证据；减少空白聊天感 |
| `01-data-dialog-process.png` | T2 三栏 AI 工作台 | TXYZ 输入与每日精选、Elicit reports | 把“过程”做成任务时间线，不做孤立面板 |
| `data-dialog-process.png` | T2 三栏 AI 工作台 | TXYZ、SciSpace Chat PDF | 同上，合并为数据分析工作台的执行态 |
| `data-dialog-tools.png` | T2 三栏 AI 工作台 | SciSpace templates、Paperpal tools | 工具区改成可搜索命令/模板抽屉，而不是右侧堆列表 |
| `data-dialog-workspace.png` | T2 三栏 AI 工作台 | Hex notebook、Deepnote data app | 工作区应承载数据、代码/步骤、结果块和可复现记录 |
| `data-template.png` | T2/T3 模板库工作台 | Hex templates、SciSpace templates | 模板卡片按任务类型、输入数据、产出物筛选 |
| `02-clinical-dashboard.png` | T5 临床/数据质量仪表盘 | CBK、Benchling platform | 保留专业感，去掉紫色大横幅，改成指标摘要 + 数据表 + 风险提示 |
| `03-clinical-patient.png` | T5 临床详情页 | CBK 搜索/医学知识页、Benchling registry | 患者/队列详情应采用档案页：摘要、标签、时间线、相关证据 |
| `04-clinical-reference.png` | T5 证据参考页 | CBK、Semantic Scholar paper detail | 参考范围应像证据库：来源、版本、适用人群、可信度 |
| `05-clinical-followup.png` | T5 时间线页 | Benchling notebook、RSpace workspace | 随访改成纵向时间线，支持事件、检查、干预、备注 |
| `06-clinical-risk.png` | T5 风险评估页 | CBK、Elicit evidence table | 风险模型显示输入变量、权重、解释和建议动作 |
| `07-clinical-comorbidity.png` | T5 关系分析页 | Connected Papers、CBK | 共病关系可用小型网络图 + 表格证据，不只展示空表 |
| `08-clinical-correlation.png` | T5 相关性分析页 | Hex chart/result blocks | 使用统计结果块、筛选器和可复现参数面板 |
| `09-clinical-report.png` | T6 报告工作室 | SciSpace Writer、ReadPaper AI 辅写 | 临床报告与成果工作室统一：左侧来源，中间报告，右侧引用/导出 |
| `dq-phi.png` | T5 数据质量工作台 | Benchling registry、Deepnote data app | PHI 检测突出字段、命中规则、脱敏预览和审核状态 |
| `dq-qc.png` | T5 数据质量工作台 | Hex data workflow、Benchling table | QC 页改成规则表 + 质量分 + 问题队列 |
| `dq-timeline.png` | T5 时间线校验页 | Benchling protocol history、Deepnote comments | 时间线异常用事件流和可展开证据，不用静态表单 |
| `10-science-home.png` | T1 研究驾驶舱 | Findin 登录后首页、TXYZ 应用首页、ScienceOne workflow | 科研首页改成“当前研究上下文 + 下一步 + 最近成果”，不罗列所有功能 |
| `13-science-home.png` | T1 研究驾驶舱 | 同上 | 与 `10-science-home` 合并，避免两个科研首页 |
| `11-science-scheduled-tasks.png` | T3/T1 自动化任务页 | ReadPaper 小组/任务、Elicit reports | 定时任务作为项目自动化，显示触发条件、运行历史、产出 |
| `12-science-hotspot.png` | T4 热点发现页 | Semantic Scholar search、AMiner、OpenRead trending | 搜索/筛选/趋势/保存到项目同屏，不做多个拥挤筛选区 |
| `science-paper-tracking.png` | T4 文献追踪页 | ReadPaper 文献管理、Semantic Scholar alerts | 追踪规则、论文列表、变更摘要、项目沉淀形成闭环 |
| `13-science-pdf-reading.png` | T2 阅读工作台 | Findin 全文对照翻译、ReadPaper 笔记、SciSpace Chat PDF | 三栏：文献列表/正文阅读/AI 问答与笔记 |
| `14-science-method-extraction.png` | T2/T6 方法抽取工作台 | Findin 文献综述、Elicit report、SciSpace concepts | 来源证据、方法卡、参数、适用条件和一键进入分析 |
| `15-science-review-mindmap.png` | T4/T6 画布页 | Connected Papers、Ponder mindmap | 全屏画布，左侧来源，右侧节点详情，不放在普通卡片里 |
| `16-science-figure-studio.png` | T6 成果工作室 | Findin 科研绘图、LKStudio results | 左侧来源/模板，中间画布，右侧样式、图例、导出设置 |
| `17-science-ppt-generation.png` | T6 成果工作室 | SciSpace Writer、ReadPaper AI 辅写 | PPT 作为成果资产：大纲、页面预览、引用、导出 |
| `18-science-review-ppt.png` | T6 成果复盘页 | Findin 文献综述、Connected Papers graph | 用报告/图谱混合画布，支持章节、引用、节点说明 |
| `profile-delivery.png` | 设置型页面 | ReadPaper 首次个人信息认证、Findin 设置 | 邮件/投递放进研究设置，做成简洁表单 + 状态说明 |
| `profile-directions.png` | 设置型页面 | Findin 主题分类、ReadPaper onboarding | 研究方向用标签/领域树/推荐主题，首次设置可跳过 |
| `profile-hot.png` | 设置型页面 + T4 | OpenRead trending、Semantic Scholar alerts | 热点偏好作为订阅规则，不作为独立复杂页面 |
| `profile-l1.png` | 设置型页面 | ReadPaper onboarding、Findin 设置 | L1/L2/L3 改名为“基础画像/研究偏好/高级记忆” |
| `profile-l2.png` | 设置型页面 | ReadPaper onboarding | 统一为分步设置，不要多个平级标签页 |
| `profile-l3.png` | 设置型页面 | Findin 设置、ReadPaper 认证 | 高级项折叠，显示系统如何使用这些信息 |
| `profile-memory.png` | 设置型知识页 | Findin 笔记摘录、ReadPaper 笔记 | 长期记忆应显示来源、可编辑摘要、使用开关 |
| `profile-methods.png` | 设置型方法库 | SciSpace concepts、Elicit reports | 方法偏好连接到“方法卡”和分析模板，不放在纯文本页 |
| `profile-search.png` | 设置型搜索偏好 | Semantic Scholar search filters | 搜索偏好做成筛选预设：领域、期刊、时间、证据等级 |
| `projects-overview.png` | T3 项目资产中心 | Benchling platform、Hex product、ReadPaper 文献管理 | 项目详情顶部显示目标、成员、资产、任务、成果摘要 |
| `projects-current.png` | T3 当前项目页 | Benchling workspace、Hex project | 当前项目作为全局上下文入口，强调继续任务和最近变更 |
| `projects-assets.png` | T3 资产页 | Benchling registry、OSF files | 资产表格统一文件/数据/模型/成果，支持预览右栏 |
| `projects-members.png` | T3 成员权限页 | Benchling collaboration、ReadPaper 小组 | 成员页做角色、权限、最近协作，而不是简单空列表 |
| `projects-sessions.png` | T3 会话记录页 | ReadPaper 文献管理、Deepnote comments | 会话作为项目资产，按任务、产出、更新时间筛选 |
| `projects-tasks.png` | T3 任务页 | Elicit reports、Benchling task form | 任务页显示状态、负责人、输入资产、产出资产和运行日志 |
| `projects-tree.png` | T3 文件树/项目结构 | OSF files、Benchling workspace | 树结构放左侧，右侧预览详情，支持拖拽组织 |
| `resources-files.png` | T3 资源表格 | OSF files、ReadPaper 文献管理 | 文件统一资源表格，字段包含来源、项目、处理状态、关联任务 |
| `resources-datasets.png` | T3 数据集表格 | Benchling registry、Hex data | 数据集突出 schema、质量分、最近分析、使用次数 |
| `resources-models.png` | T3 模型资源表格 | Benchling registry、Deepnote environment | 模型资源显示版本、适用任务、依赖、调用记录 |

## 3. 每个模块的目标参考优先级

### 数据分析与实验

优先学 **Hex / Deepnote / TXYZ**。  
原因：PickU 的数据分析不是聊天产品，而是可复现研究工作台。页面应围绕“输入数据、分析计划、执行过程、结果块、可导出资产”组织。

### 文献阅读与科研发现

优先学 **Findin / ReadPaper / SciSpace / Semantic Scholar**。  
原因：这些页面对“文献集合、阅读、笔记、AI 问答、引用证据”的分区最接近 PickU。

### 项目与资源

优先学 **Benchling / Hex / OSF / ReadPaper**。  
原因：PickU 的长期价值在项目资产沉淀，项目、文件、数据、模型、任务、成员应共享一个资源模型。

### 临床与数据质量

优先学 **CBK / Benchling / Hex**。  
原因：临床页面需要可信、低干扰、证据导向。少用营销式大色块，多用表格、时间线、风险解释和数据质量状态。

### 成果生成

优先学 **Findin / SciSpace Writer / ReadPaper AI 辅写 / LKStudio results**。  
原因：综述、图、PPT、报告都不是聊天窗口，而是“来源资产 -> 编辑产出 -> 引用/导出”的成果工作室。

## 4. 我建议第一批先改的页面

第一批不建议 47 页同时动。先做 5 个主模板页面，确认风格后再迁移：

1. `10-science-home.png`：做成 Research OS 首页。
2. `01-data-analysis.png`：做成三栏 AI 数据分析工作台。
3. `projects-overview.png`：做成项目资产中心。
4. `13-science-pdf-reading.png`：做成文献阅读工作台。
5. `16-science-figure-studio.png`：做成成果工作室。

这 5 个页面确定后，剩下页面基本是套模板迁移，而不是每页重新设计。

## 5. 参考截图入口

- 单页图库：`/data1/hyf/frontend/research/report/SINGLE_PAGE_GALLERY.html`
- 单页索引：`/data1/hyf/frontend/research/report/SINGLE_PAGE_INDEX.md`
- 单页图片目录：`/data1/hyf/frontend/research/single-page-screenshots`
