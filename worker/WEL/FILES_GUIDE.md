# WEL 文件导览

## 📂 文件结构

```
WEL/
├── 📘 WEL_README.md              # 完整文档（推荐首先阅读）
├── 📋 FILES_GUIDE.md             # 本文件 - 快速导览
│
├── 🔧 核心模块
│   ├── __init__.py               # 模块入口和导出
│   ├── wel_generator.py          # WEL 生成器（主要入口）
│   ├── wel_document.py           # WEL 文档主类
│   └── wel_action.py             # WEL 动作主类
│
├── 📦 组件模块
│   ├── enums.py                  # 所有枚举类型定义
│   ├── planner_context.py        # Planner 上下文组件
│   ├── action_components.py      # Action 各层组件
│   └── document_components.py    # Document 配置组件
│
└── 📖 示例和文档
    └── example_usage.py          # 完整使用示例
```

---

## 📚 文件详解

### 核心模块

#### `__init__.py`
- **作用**: 模块入口，导出所有公共接口
- **内容**: 导入和导出所有组件类、枚举、工具函数
- **使用**: `from worker.WEL import WELGenerator, IntentType, ...`

#### `wel_generator.py` ⭐
- **作用**: WEL 生成器，从 Planner 输出生成 WEL 文档
- **核心类**: `WELGenerator`
- **主要方法**: `generate_from_planner()`
- **输入**: Planner 的五个输出（requirement, experience, knowledge, tools, tasks）
- **输出**: 完整的 `WELDocument` 对象
- **使用场景**: 这是主要的入口点，99% 的情况下你会使用这个类

#### `wel_document.py`
- **作用**: WEL 文档主类
- **核心类**: `WELDocument`
- **主要方法**: 
  - `add_action()` - 添加动作
  - `to_json()` - 导出为 JSON
  - `to_pdl_document()` - 转换为 PDL 格式
- **包含内容**: 
  - Planner 上下文
  - 动作序列
  - 工作流配置
  - 度量指标
  - 反馈和调整策略
  - 生命周期状态

#### `wel_action.py`
- **作用**: WEL 动作主类
- **核心类**: `WELAction`
- **架构**: 六层设计
  1. 核心层 - 标识和目标
  2. 数据流层 - 输入输出
  3. 元数据层 - 描述和理由
  4. 执行层 - 策略和超时
  5. 质量层 - 断言和验证
  6. 可观测层 - 追踪和监控
- **主要方法**:
  - `to_dict()` - 转换为字典
  - `to_pdl_step()` - 转换为 PDL 步骤

---

### 组件模块

#### `enums.py`
- **作用**: 定义所有枚举类型
- **包含枚举**:
  - `IntentType` - 意图类型（7种）
  - `ActionType` - 动作类型（21种细粒度分类）
  - `ExecutionStrategy` - 执行策略
  - `AssertionType` - 断言类型
  - `Severity` - 严重程度
  - `FailureAction` - 失败处理动作
  - `RiskLevel` - 风险等级
  - `State` - 生命周期状态
  - 等等...
- **使用**: `from worker.WEL import IntentType, ActionType`

#### `planner_context.py`
- **作用**: Planner 上下文相关组件
- **核心类**:
  - `PlannerContext` - 完整的 Planner 上下文
  - `IntentInfo` - 意图信息
  - `ExperienceInfo` - 经验信息
  - `KnowledgeInfo` - 知识信息
  - `ToolInfo` - 工具信息
  - `ToolRegistry` - 工具注册表
  - `ConstraintSet` - 约束集合
- **用途**: 保留 Planner 的完整决策链路

#### `action_components.py`
- **作用**: Action 的各层组件
- **组件分类**:
  
  **核心层**
  - `ActionTarget` - 动作目标
  
  **数据流层**
  - `DataPort` - 数据端口
  - `DataFlow` - 数据流描述
  
  **元数据层**
  - `ConfidenceBreakdown` - 置信度分解
  - `AlternativeAction` - 备选动作
  - `ActionMetadata` - 动作元数据
  
  **执行层**
  - `RetryPolicy` - 重试策略
  - `ExecutionConfig` - 执行配置
  
  **质量层**
  - `Assertion` - 断言
  - `QualityAssurance` - 质量保证
  
  **可观测层**
  - `ObservabilityConfig` - 可观测性配置

#### `document_components.py`
- **作用**: Document 的配置组件
- **核心类**:
  - `ScenarioInfo` - 场景信息
  - `WorkflowConfig` - 工作流配置
  - `WorkflowMetrics` - 度量指标
  - `FeedbackConfig` - 反馈配置
  - `AdjustmentTrigger` - 调整触发器
  - `AdjustmentPolicy` - 调整策略
  - `StateTransition` - 状态转换
  - `LifecycleState` - 生命周期状态

---

### 示例和文档

#### `example_usage.py`
- **作用**: 完整的使用示例
- **场景**: 麻醉药物对痛感影响的分析
- **展示内容**:
  - 如何准备 Planner 输出
  - 如何使用 WELGenerator
  - 如何访问生成的 WEL 文档
  - 如何查看度量指标
  - 如何转换为 PDL 格式
  - 如何管理生命周期
- **运行**: `python -m worker.WEL.example_usage`

#### `WEL_README.md` ⭐⭐⭐
- **作用**: 完整的框架文档
- **内容**: 
  - 框架介绍和核心概念
  - 架构设计图
  - 详细的 API 参考
  - 使用指南和最佳实践
  - 常见问题解答
  - 设计理念说明
- **推荐**: 强烈建议首先阅读此文档

---

## 🚀 快速开始路径

### 新手推荐路径

1. **阅读文档** (15分钟)
   - 📘 `WEL_README.md` - 了解核心概念和架构

2. **运行示例** (5分钟)
   ```bash
   cd /data/agent_platform
   python -m worker.WEL.example_usage
   ```

3. **查看代码** (10分钟)
   - 📖 `example_usage.py` - 理解使用方式
   - 🔧 `wel_generator.py` - 了解生成逻辑

4. **运行测试案例** (5分钟)
   ```bash
   python test/test_humidity_psoriasis_analysis.py
   ```

5. **尝试自己的案例** (30分钟)
   - 基于示例修改自己的场景
   - 准备 Planner 输出数据
   - 生成 WEL 文档

### 进阶学习路径

1. **深入组件** (30分钟)
   - 📦 `action_components.py` - Action 六层架构
   - 📦 `document_components.py` - Document 配置
   - 📦 `planner_context.py` - Planner 上下文

2. **理解枚举** (15分钟)
   - 🔧 `enums.py` - 所有枚举类型的含义

3. **研究核心类** (1小时)
   - 🔧 `wel_action.py` - Action 的实现细节
   - 🔧 `wel_document.py` - Document 的实现细节
   - 🔧 `wel_generator.py` - 生成器的实现逻辑

---

## 💡 常用操作速查

### 导入核心组件
```python
from worker.WEL import (
    WELGenerator,      # 生成器
    WELDocument,       # 文档类
    WELAction,         # 动作类
    IntentType,        # 意图类型枚举
    ActionType,        # 动作类型枚举
    State              # 状态枚举
)
```

### 创建 WEL 文档
```python
generator = WELGenerator()
wel_doc = await generator.generate_from_planner(
    requirement=req, experience=exp, knowledge=know,
    tools=tools, tasks=tasks,
    intent_type=IntentType.DATA_ANALYSIS,
    intent_confidence=0.9
)
```

### 导出为 JSON
```python
json_str = wel_doc.to_json()
```

### 转换为 PDL
```python
pdl_doc = wel_doc.to_pdl_document()
```

### 状态管理
```python
wel_doc.lifecycle.transition_to(State.EXECUTING, "开始执行")
```

---

## 📊 模块依赖关系

```
wel_generator.py
    ↓
    ├─→ wel_document.py
    │       ↓
    │       ├─→ planner_context.py
    │       ├─→ wel_action.py
    │       └─→ document_components.py
    │
    └─→ wel_action.py
            ↓
            ├─→ action_components.py
            └─→ enums.py

所有模块都依赖 enums.py
```

---

## 🎯 按需查找

### 我想知道...

| 需求 | 查看文件 | 位置 |
|------|---------|------|
| 如何使用 WEL | `example_usage.py` | 完整示例 |
| 有哪些动作类型 | `enums.py` | `ActionType` |
| 如何定义数据流 | `action_components.py` | `DataFlow`, `DataPort` |
| 如何添加断言 | `action_components.py` | `Assertion` |
| 如何管理生命周期 | `document_components.py` | `LifecycleState` |
| 如何计算度量指标 | `wel_generator.py` | `_calculate_metrics()` |
| 如何转换为 PDL | `wel_document.py` | `to_pdl_document()` |
| 完整 API 文档 | `WEL_README.md` | API 参考章节 |

---

## 📞 获取帮助

1. **查看文档**: `WEL_README.md` - 最全面的参考
2. **运行示例**: `example_usage.py` - 实际代码演示
3. **查看测试**: `../test/test_humidity_psoriasis_analysis.py` - 完整测试案例
4. **阅读代码**: 所有代码都有详细的文档字符串

---

**Happy Coding! 🎉**

*最后更新: 2025年10月24日*

