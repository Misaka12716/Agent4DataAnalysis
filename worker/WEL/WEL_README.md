# WEL v2.0 - Workflow Execution Language

## 📖 目录

- [简介](#简介)
- [核心概念](#核心概念)
- [架构设计](#架构设计)
- [主要组件](#主要组件)
- [快速开始](#快速开始)
- [使用指南](#使用指南)
- [API 参考](#api-参考)
- [示例](#示例)
- [设计理念](#设计理念)
- [版本历史](#版本历史)

---

## 简介

**WEL (Workflow Execution Language)** 是一个用于 Agent 系统的工作流执行语言框架，旨在将 Planner 的规划转换为可执行的工作流定义。

### 核心特性

- ✅ **分层设计**：清晰的职责分离，模块化组件
- ✅ **显式数据流**：明确的数据依赖和转换
- ✅ **统一质量保证**：完整的断言系统
- ✅ **完整可观测性**：追踪、度量、监控
- ✅ **反馈闭环**：动态调整能力
- ✅ **向后兼容**：支持 PDL 格式转换

### 适用场景

- 数据分析工作流
- 数据处理管道
- 报告生成自动化
- 查询执行编排
- 知识提取流程
- 自定义业务流程

---

## 核心概念

### 1. WEL Document（工作流文档）

WEL Document 是工作流的完整定义，包含：
- **标识信息**：唯一 ID、版本、创建时间
- **Planner 上下文**：保留完整的规划决策链路
- **场景信息**：目标、成功/失败定义
- **动作序列**：具体的执行步骤
- **配置和策略**：执行配置、反馈、调整策略
- **生命周期**：状态追踪和历史记录

### 2. WEL Action（工作流动作）

WEL Action 是工作流的基本执行单元，采用**六层架构设计**：

1. **核心层** - 动作标识和类型
2. **数据流层** - 输入输出端口和依赖关系
3. **元数据层** - Planner 语义和理由
4. **执行层** - 策略、超时、重试
5. **质量层** - 断言和验证
6. **可观测层** - 追踪和监控

### 3. Planner Context（规划上下文）

保留 Planner 的完整决策链路：
- **意图识别**：任务类型和置信度
- **需求分析**：用户需求的结构化表示
- **知识来源**：经验库和知识库
- **工具匹配**：可用工具和能力
- **约束条件**：时间、成本、资源限制

---

## 架构设计

### 整体架构

```
┌─────────────────────────────────────────────────────────┐
│                    WEL Document                         │
│  ┌───────────────────────────────────────────────────┐ │
│  │           Planner Context (决策链路)              │ │
│  │  - Intent (意图)                                  │ │
│  │  - Requirement (需求)                             │ │
│  │  - Experience & Knowledge (经验与知识)            │ │
│  │  - Tools (工具)                                   │ │
│  │  - Constraints (约束)                             │ │
│  └───────────────────────────────────────────────────┘ │
│                                                         │
│  ┌───────────────────────────────────────────────────┐ │
│  │              Action Sequence (动作序列)           │ │
│  │                                                   │ │
│  │  ┌─────────────┐  ┌─────────────┐  ┌──────────┐ │ │
│  │  │  Action 1   │→ │  Action 2   │→ │ Action 3 │ │ │
│  │  └─────────────┘  └─────────────┘  └──────────┘ │ │
│  │                                                   │ │
│  └───────────────────────────────────────────────────┘ │
│                                                         │
│  ┌───────────────────────────────────────────────────┐ │
│  │       Workflow Configuration (工作流配置)         │ │
│  │  - Execution Mode (执行模式)                      │ │
│  │  - Metrics (度量指标)                             │ │
│  │  - Feedback (反馈配置)                            │ │
│  │  - Adjustment Policy (调整策略)                   │ │
│  └───────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────┘
```

### Action 六层架构

```
┌─────────────────────────────────────────────────────┐
│                   WEL Action                        │
│                                                     │
│  ┌───────────────────────────────────────────────┐ │
│  │  Layer 1: 核心层 (Core)                       │ │
│  │  - action_id, action_name, action_type        │ │
│  │  - target (entity, operation, parameters)     │ │
│  └───────────────────────────────────────────────┘ │
│                                                     │
│  ┌───────────────────────────────────────────────┐ │
│  │  Layer 2: 数据流层 (Data Flow)                │ │
│  │  - inputs[] (DataPort)                        │ │
│  │  - outputs[] (DataPort)                       │ │
│  │  - dependencies (自动解析)                    │ │
│  └───────────────────────────────────────────────┘ │
│                                                     │
│  ┌───────────────────────────────────────────────┐ │
│  │  Layer 3: 元数据层 (Metadata)                 │ │
│  │  - natural_language_description               │ │
│  │  - rationale (理由)                           │ │
│  │  - confidence_breakdown (置信度分解)          │ │
│  │  - alternative_actions (备选方案)             │ │
│  └───────────────────────────────────────────────┘ │
│                                                     │
│  ┌───────────────────────────────────────────────┐ │
│  │  Layer 4: 执行层 (Execution)                  │ │
│  │  - strategy (IMMEDIATE/SCHEDULED/...)         │ │
│  │  - timeout                                    │ │
│  │  - retry_policy                               │ │
│  └───────────────────────────────────────────────┘ │
│                                                     │
│  ┌───────────────────────────────────────────────┐ │
│  │  Layer 5: 质量层 (Quality)                    │ │
│  │  - assertions[] (PRE/POST/INVARIANT)          │ │
│  │  - validation rules                           │ │
│  └───────────────────────────────────────────────┘ │
│                                                     │
│  ┌───────────────────────────────────────────────┐ │
│  │  Layer 6: 可观测层 (Observability)            │ │
│  │  - tracing (trace_id, span_id)                │ │
│  │  - metrics (tags, enabled)                    │ │
│  │  - logging (level, context)                   │ │
│  └───────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────┘
```

---

## 主要组件

### 1. 枚举类型 (`enums.py`)

定义了所有系统使用的枚举常量：

#### IntentType - 意图类型
- `DATA_ANALYSIS` - 数据分析
- `DATA_PROCESSING` - 数据处理
- `REPORT_GENERATION` - 报告生成
- `QUERY_EXECUTION` - 查询执行
- `WORKFLOW_AUTOMATION` - 工作流自动化
- `KNOWLEDGE_EXTRACTION` - 知识提取
- `CUSTOM` - 自定义

#### ActionType - 动作类型（细粒度分类）

**数据操作** (4种)
- `DATA_QUERY` - 数据查询
- `DATA_INSERT` - 数据插入
- `DATA_UPDATE` - 数据更新
- `DATA_DELETE` - 数据删除

**数据处理** (4种)
- `DATA_CLEAN` - 数据清洗
- `DATA_TRANSFORM` - 数据转换
- `DATA_AGGREGATE` - 数据聚合
- `DATA_VALIDATE` - 数据验证

**分析和计算** (4种)
- `STATISTICAL_ANALYSIS` - 统计分析
- `CORRELATION_ANALYSIS` - 相关性分析
- `TREND_ANALYSIS` - 趋势分析
- `CUSTOM_COMPUTE` - 自定义计算

**可视化** (3种)
- `CHART_GENERATION` - 图表生成
- `REPORT_GENERATION` - 报告生成
- `DASHBOARD_UPDATE` - 仪表板更新

**工作流控制** (3种)
- `CONDITIONAL_BRANCH` - 条件分支
- `LOOP_ITERATION` - 循环迭代
- `PARALLEL_EXECUTION` - 并行执行

**外部交互** (3种)
- `API_CALL` - API 调用
- `FILE_OPERATION` - 文件操作
- `NOTIFICATION` - 通知

#### 其他枚举
- `ExecutionStrategy` - 执行策略
- `AssertionType` - 断言类型
- `Severity` - 严重程度
- `FailureAction` - 失败动作
- `RiskLevel` - 风险等级
- `State` - 生命周期状态

### 2. Planner 上下文 (`planner_context.py`)

保留 Planner 的完整决策链路：

```python
@dataclass
class PlannerContext:
    intent: IntentInfo              # 意图识别
    requirement: Dict[str, Any]     # 需求分析
    experience: Optional[ExperienceInfo]  # 经验信息
    knowledge: Optional[KnowledgeInfo]    # 知识信息
    tools: ToolRegistry             # 工具注册表
    constraints: ConstraintSet      # 约束条件
```

### 3. Action 组件 (`action_components.py`)

包含 Action 的各层组件：

#### 核心层
- `ActionTarget` - 动作目标（entity, operation, parameters）

#### 数据流层
- `DataPort` - 数据端口（输入/输出定义）
- `DataFlow` - 数据流描述（inputs, outputs）

#### 元数据层
- `ConfidenceBreakdown` - 置信度分解
- `AlternativeAction` - 备选动作
- `ActionMetadata` - 动作元数据

#### 执行层
- `RetryPolicy` - 重试策略
- `ExecutionConfig` - 执行配置

#### 质量层
- `Assertion` - 断言
- `QualityAssurance` - 质量保证

#### 可观测层
- `ObservabilityConfig` - 可观测性配置

### 4. Document 组件 (`document_components.py`)

包含 Document 的配置组件：

- `ScenarioInfo` - 场景信息
- `WorkflowConfig` - 工作流配置
- `WorkflowMetrics` - 度量指标
- `FeedbackConfig` - 反馈配置
- `AdjustmentTrigger` - 调整触发器
- `AdjustmentPolicy` - 调整策略
- `StateTransition` - 状态转换
- `LifecycleState` - 生命周期状态

### 5. WEL Action (`wel_action.py`)

动作主类，整合所有组件：

```python
@dataclass
class WELAction:
    # 核心层
    action_id: str
    action_name: str
    action_type: ActionType
    target: ActionTarget
    
    # 数据流层
    data_flow: DataFlow
    
    # 元数据层
    metadata: ActionMetadata
    
    # 执行层
    execution: ExecutionConfig
    
    # 质量层
    quality: QualityAssurance
    
    # 可观测层
    observability: ObservabilityConfig
```

### 6. WEL Document (`wel_document.py`)

文档主类，完整的工作流定义：

```python
@dataclass
class WELDocument:
    wel_id: str                          # 文档标识
    planner_context: PlannerContext      # Planner 上下文
    scenario: ScenarioInfo               # 场景信息
    actions: List[WELAction]             # 动作序列
    workflow_config: WorkflowConfig      # 工作流配置
    metrics: WorkflowMetrics             # 度量指标
    feedback_config: FeedbackConfig      # 反馈配置
    adjustment_policy: AdjustmentPolicy  # 调整策略
    lifecycle: LifecycleState            # 生命周期
```

### 7. WEL Generator (`wel_generator.py`)

从 Planner 输出生成 WEL 文档：

```python
class WELGenerator:
    async def generate_from_planner(
        requirement: Dict[str, Any],
        experience: Dict[str, Any],
        knowledge: Dict[str, Any],
        tools: Dict[str, Any],
        tasks: Dict[str, Any],
        intent_type: IntentType,
        intent_confidence: float
    ) -> WELDocument
```

---

## 快速开始

### 安装

WEL 是 agent_platform 的一部分，无需单独安装。

### 基本使用

```python
import asyncio
from worker.WEL import WELGenerator, IntentType

async def main():
    # 创建生成器
    generator = WELGenerator()
    
    # 准备 Planner 输出
    requirement = {
        'task_type': '数据分析',
        'goal': '分析销售数据趋势',
        'input_data': '销售记录表',
        'output_requirement': '趋势分析报告'
    }
    
    experience = {
        'success': True,
        'experience': '类似任务建议先清洗数据'
    }
    
    knowledge = {
        'success': False,
        'knowledge': {}
    }
    
    tools = {
        'success': True,
        'tools': [
            {
                'name': 'data_loader',
                'description': '数据加载工具',
                'confidence': 0.9
            }
        ]
    }
    
    tasks = {
        'scenario': '销售数据分析',
        'goal': '生成趋势分析报告',
        'tasks': [
            {
                'task_id': 1,
                'task_name': '加载数据',
                'description': '从数据库加载销售数据',
                'required_tool': 'data_loader',
                'worker_type': 'database_worker',
                'dependencies': [],
                'deadline': '1分钟',
                'parameters': {'table': 'sales'}
            }
        ]
    }
    
    # 生成 WEL 文档
    wel_doc = await generator.generate_from_planner(
        requirement=requirement,
        experience=experience,
        knowledge=knowledge,
        tools=tools,
        tasks=tasks,
        intent_type=IntentType.DATA_ANALYSIS,
        intent_confidence=0.9
    )
    
    # 输出 JSON
    print(wel_doc.to_json())
    
    # 或转换为 PDL 格式
    pdl_doc = wel_doc.to_pdl_document()

if __name__ == "__main__":
    asyncio.run(main())
```

---

## 使用指南

### 1. 创建 WEL Generator

```python
from worker.WEL import WELGenerator

generator = WELGenerator()
```

### 2. 准备 Planner 输出

WEL Generator 需要 Planner 的五个输出：

#### a. Requirement（需求）
```python
requirement = {
    'task_type': '任务类型',
    'goal': '目标描述',
    'input_data': '输入数据描述',
    'output_requirement': '输出要求',
    'constraints': '约束条件'
}
```

#### b. Experience（经验）
```python
experience = {
    'success': True,  # 是否找到相关经验
    'experience': '经验描述文本'
}
```

#### c. Knowledge（知识）
```python
knowledge = {
    'success': True,  # 是否找到相关知识
    'knowledge': {
        'domain_facts': ['事实1', '事实2'],
        'methods': ['方法1', '方法2']
    },
    'sources': ['来源1', '来源2']
}
```

#### d. Tools（工具）
```python
tools = {
    'success': True,
    'tools': [
        {
            'name': '工具名称',
            'description': '工具描述',
            'confidence': 0.9,
            'capabilities': ['能力1', '能力2']
        }
    ]
}
```

#### e. Tasks（任务分解）
```python
tasks = {
    'scenario': '场景名称',
    'goal': '场景目标',
    'tasks': [
        {
            'task_id': 1,
            'task_name': '任务名称',
            'description': '任务描述',
            'required_tool': '所需工具',
            'worker_type': 'worker类型',
            'dependencies': [],  # 依赖的任务ID列表
            'deadline': '截止时间',
            'parameters': {}  # 参数字典
        }
    ]
}
```

### 3. 生成 WEL 文档

```python
wel_doc = await generator.generate_from_planner(
    requirement=requirement,
    experience=experience,
    knowledge=knowledge,
    tools=tools,
    tasks=tasks,
    intent_type=IntentType.DATA_ANALYSIS,
    intent_confidence=0.9
)
```

### 4. 使用 WEL 文档

#### 输出为 JSON
```python
json_str = wel_doc.to_json()
print(json_str)
```

#### 转换为字典
```python
doc_dict = wel_doc.to_dict()
```

#### 转换为 PDL 格式（向后兼容）
```python
pdl_doc = wel_doc.to_pdl_document()
```

#### 访问文档属性
```python
# 获取动作列表
actions = wel_doc.actions

# 获取度量指标
print(f"动作数量: {wel_doc.metrics.action_count}")
print(f"预估时间: {wel_doc.metrics.estimated_duration}秒")
print(f"置信度: {wel_doc.metrics.confidence_score}")
print(f"风险等级: {wel_doc.metrics.risk_level.value}")

# 遍历动作
for action in wel_doc.actions:
    print(f"动作: {action.action_name}")
    print(f"  类型: {action.action_type.value}")
    print(f"  依赖: {action.data_flow.get_dependencies()}")
    print(f"  置信度: {action.metadata.confidence_breakdown.overall_confidence}")
```

#### 生命周期管理
```python
from worker.WEL import State

# 转换状态
wel_doc.lifecycle.transition_to(State.EXECUTING, "开始执行")
wel_doc.lifecycle.transition_to(State.COMPLETED, "执行完成")

# 查看状态历史
for trans in wel_doc.lifecycle.state_history:
    print(f"{trans.timestamp}: {trans.from_state.value} → {trans.to_state.value}")
```

---

## API 参考

### WELGenerator

#### `generate_from_planner()`

从 Planner 的完整输出生成 WEL 文档。

**参数**：
- `requirement` (Dict): organize_requirement 的输出
- `experience` (Dict): search_experience 的输出
- `knowledge` (Dict): search_knowledge 的输出
- `tools` (Dict): find_tools 的输出
- `tasks` (Dict): assign_tasks 的输出
- `intent_type` (IntentType): 意图类型
- `intent_confidence` (float): 意图置信度

**返回**：
- `WELDocument`: 完整的 WEL 文档

### WELDocument

#### 方法

- `add_action(action: WELAction)` - 添加动作
- `to_dict() -> Dict[str, Any]` - 转换为字典
- `to_json() -> str` - 转换为 JSON 字符串
- `to_pdl_document() -> Dict[str, Any]` - 转换为 PDL 格式

#### 属性

- `wel_id` - 文档唯一标识
- `version` - 版本号
- `planner_context` - Planner 上下文
- `scenario` - 场景信息
- `actions` - 动作列表
- `workflow_config` - 工作流配置
- `metrics` - 度量指标
- `feedback_config` - 反馈配置
- `adjustment_policy` - 调整策略
- `lifecycle` - 生命周期状态

### WELAction

#### 方法

- `to_dict() -> Dict[str, Any]` - 转换为字典
- `to_pdl_step() -> Dict[str, Any]` - 转换为 PDL 步骤

#### 属性

- `action_id` - 动作唯一标识
- `action_name` - 动作名称
- `action_type` - 动作类型
- `target` - 目标实体和操作
- `data_flow` - 数据流定义
- `metadata` - 元数据（描述、理由、置信度等）
- `execution` - 执行配置
- `quality` - 质量保证
- `observability` - 可观测性配置

### DataFlow

#### 方法

- `validate() -> bool` - 验证数据流完整性
- `get_dependencies() -> List[str]` - 获取依赖的动作ID列表

#### 属性

- `inputs` - 输入端口列表
- `outputs` - 输出端口列表

### LifecycleState

#### 方法

- `transition_to(new_state: State, reason: str)` - 状态转换

#### 属性

- `state` - 当前状态
- `state_history` - 状态历史记录
- `error_info` - 错误信息（如果有）

---

## 示例

### 完整示例：医疗数据分析

详见 `example_usage.py` 文件，该示例展示了：
- 麻醉药物对痛感影响的分析
- 完整的数据加载、清洗、分析流程
- 度量指标和数据流分析
- PDL 格式转换
- 生命周期管理

### 测试案例：空气湿度与银屑病相关性分析

详见 `/test/test_humidity_psoriasis_analysis.py`，该测试展示了：
- 医疗环境因素相关性分析
- 虚拟测试数据生成
- 9个步骤的完整分析工作流
- 统计分析、可视化、报告生成
- 输出 WEL/PDL/测试数据 JSON 文件

运行测试：
```bash
cd /data/agent_platform
python test/test_humidity_psoriasis_analysis.py
```

---

## 设计理念

### 1. 分层设计

WEL 采用六层架构设计，每一层都有明确的职责：

- **核心层**：定义"是什么"（What）
- **数据流层**：定义"从哪来到哪去"（Where）
- **元数据层**：定义"为什么"（Why）
- **执行层**：定义"怎么执行"（How）
- **质量层**：定义"如何保证"（Quality）
- **可观测层**：定义"如何监控"（Monitor）

### 2. 显式数据流

- 明确的输入输出端口定义
- 自动解析依赖关系
- 支持数据转换
- 验证数据完整性

### 3. 统一质量保证

- 前置条件（Pre-condition）
- 后置条件（Post-condition）
- 不变量（Invariant）
- 失败处理策略

### 4. 完整可观测性

- 分布式追踪（Trace ID, Span ID）
- 指标收集（Metrics）
- 日志记录（Logging）
- 检查点机制（Checkpoint）

### 5. 反馈闭环

- 动态调整策略
- 失败重试机制
- 备选方案支持
- 重新规划能力

### 6. Planner 决策链路保留

- 保留意图识别信息
- 保留经验和知识来源
- 保留工具匹配过程
- 保留约束条件
- 支持决策追溯和审计

### 7. 向后兼容

- 支持 PDL 格式转换
- 兼容旧版执行引擎
- 渐进式升级路径

---

## 文件结构

```
WEL/
├── __init__.py                 # 模块导出
├── enums.py                    # 枚举类型定义
├── planner_context.py          # Planner 上下文组件
├── action_components.py        # Action 各层组件
├── document_components.py      # Document 配置组件
├── wel_action.py               # WEL Action 主类
├── wel_document.py             # WEL Document 主类
├── wel_generator.py            # WEL Generator
├── example_usage.py            # 使用示例
└── WEL_README.md               # 本文档
```

---

## 版本历史

### v2.0 (当前版本)

**重大更新**：
- ✨ 全新的六层架构设计
- ✨ 完整的 Planner 上下文保留
- ✨ 统一的数据流描述
- ✨ 完整的质量保证系统
- ✨ 全面的可观测性支持
- ✨ 动态调整策略

**核心改进**：
- 🔧 模块化组件设计
- 🔧 细粒度的动作类型分类（21种）
- 🔧 置信度多维度分解
- 🔧 生命周期状态管理
- 🔧 PDL 格式兼容

**新增功能**：
- ➕ 工具注册表
- ➕ 约束集合
- ➕ 备选动作
- ➕ 断言系统
- ➕ 反馈配置
- ➕ 调整触发器

### v1.0 (已弃用)

- 基础的 PDL 格式
- 简单的任务定义
- 基本的执行配置

---

## 最佳实践

### 1. 动作设计

- 保持动作的原子性和单一职责
- 明确定义输入输出端口
- 添加合适的质量断言
- 设置合理的超时和重试策略

### 2. 数据流管理

- 使用明确的数据源引用（`action_id.port_name`）
- 验证数据流的完整性
- 处理可选输入的默认值
- 考虑数据转换需求

### 3. 错误处理

- 为关键步骤添加前置条件
- 设置合适的失败处理策略
- 提供备选动作方案
- 记录详细的错误信息

### 4. 可观测性

- 启用追踪功能便于调试
- 添加有意义的度量标签
- 设置合适的日志级别
- 使用检查点保存状态

### 5. 性能优化

- 识别可并行执行的动作
- 设置合理的超时时间
- 优化数据传输
- 考虑资源限制

---

## 常见问题

### Q1: WEL 和 PDL 的区别是什么？

**A**: WEL v2.0 是全新设计的工作流语言，相比 PDL：
- 更完整的语义信息（保留 Planner 决策链路）
- 更细粒度的动作分类
- 更强大的质量保证系统
- 更全面的可观测性支持
- 支持动态调整和反馈闭环

WEL 同时提供 `to_pdl_document()` 方法向后兼容。

### Q2: 如何处理动作之间的依赖关系？

**A**: 通过 DataFlow 的输入端口自动处理：
```python
DataPort(
    name="input_data",
    source="action_1.output",  # 依赖 action_1 的输出
    required=True
)
```

### Q3: 如何添加自定义断言？

**A**: 使用 Assertion 对象：
```python
from worker.WEL import Assertion, AssertionType, Severity, FailureAction

assertion = Assertion(
    name="data_not_empty",
    type=AssertionType.POST_CONDITION,
    condition="len(output.data) > 0",
    error_message="输出数据为空",
    severity=Severity.ERROR,
    on_failure=FailureAction.ABORT
)

action.quality.assertions.append(assertion)
```

### Q4: 如何追踪工作流执行？

**A**: 使用 ObservabilityConfig：
```python
observability = ObservabilityConfig(
    enable_tracing=True,
    trace_id="trace_xxx",
    enable_metrics=True,
    metric_tags={'project': 'my_project'},
    log_level="INFO"
)
```

### Q5: 如何实现条件分支？

**A**: 使用 `CONDITIONAL_BRANCH` 动作类型，并在参数中指定条件：
```python
action = WELAction(
    action_type=ActionType.CONDITIONAL_BRANCH,
    target=ActionTarget(
        entity="condition_evaluator",
        operation="evaluate",
        parameters={
            'condition': 'input.value > 100',
            'true_action': 'action_2',
            'false_action': 'action_3'
        }
    ),
    ...
)
```

---

## 贡献指南

如需为 WEL 框架做出贡献，请遵循以下步骤：

1. Fork 项目
2. 创建功能分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 开启 Pull Request

---

## 联系方式

- **团队**: Agent Platform Team
- **版本**: v2.0
- **最后更新**: 2025年10月24日

---

## 许可证

Copyright © 2025 Agent Platform Team. All rights reserved.

---

**Happy Coding! 🚀**

