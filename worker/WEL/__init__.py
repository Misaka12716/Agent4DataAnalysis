"""
WEL (Workflow Execution Language) v2.0
工作流执行语言 - 模块化设计

核心目标：将 Planner 的规划转换为可执行的工作流语言

设计理念：
1. 分层设计：清晰的职责分离
2. 显式数据流：明确的数据依赖和转换
3. 统一质量保证：断言系统
4. 完整可观测性：追踪、度量、监控
5. 反馈闭环：动态调整能力

📚 详细文档请参阅: WEL_README.md
🎯 使用示例请参阅: example_usage.py
🧪 测试案例请参阅: ../test/test_humidity_psoriasis_analysis.py
"""

# ==================== 枚举类型 ====================
from .enums import (
    IntentType,
    ActionType,
    ExecutionStrategy,
    AssertionType,
    Severity,
    FailureAction,
    RiskLevel,
    FeedbackFrequency,
    FeedbackChannel,
    FeedbackFormat,
    TriggerType,
    AdjustmentAction,
    State
)

# ==================== Planner 上下文 ====================
from .planner_context import (  # pyright: ignore[reportMissingImports]
    IntentInfo,
    ExperienceInfo,
    KnowledgeInfo,
    ToolInfo,
    ToolRegistry,
    ConstraintSet,
    PlannerContext
)

# ==================== Action 组件 ====================
from .action_components import (  # pyright: ignore[reportMissingImports]
    ActionTarget,
    DataPort,
    DataFlow,
    ConfidenceBreakdown,
    AlternativeAction,
    ActionMetadata,
    RetryPolicy,
    ExecutionConfig,
    Assertion,
    QualityAssurance,
    ObservabilityConfig
)

# ==================== WELAction ====================
from .wel_action import WELAction  # pyright: ignore[reportMissingImports]

# ==================== Document 组件 ====================
from .document_components import (  # pyright: ignore[reportMissingImports]
    ScenarioInfo,
    WorkflowConfig,
    WorkflowMetrics,
    FeedbackConfig,
    AdjustmentTrigger,
    AdjustmentPolicy,
    StateTransition,
    LifecycleState
)

# ==================== WELDocument ====================
from .wel_document import WELDocument  # pyright: ignore[reportMissingImports]

# ==================== WEL Generator ====================
from .wel_generator import WELGenerator  # pyright: ignore[reportMissingImports]


# ==================== 版本信息 ====================
__version__ = "2.0"
__author__ = "Agent Platform Team"


# ==================== 公共接口 ====================
__all__ = [
    # 枚举类型
    "IntentType",
    "ActionType",
    "ExecutionStrategy",
    "AssertionType",
    "Severity",
    "FailureAction",
    "RiskLevel",
    "FeedbackFrequency",
    "FeedbackChannel",
    "FeedbackFormat",
    "TriggerType",
    "AdjustmentAction",
    "State",
    
    # Planner 上下文
    "IntentInfo",
    "ExperienceInfo",
    "KnowledgeInfo",
    "ToolInfo",
    "ToolRegistry",
    "ConstraintSet",
    "PlannerContext",
    
    # Action 组件
    "ActionTarget",
    "DataPort",
    "DataFlow",
    "ConfidenceBreakdown",
    "AlternativeAction",
    "ActionMetadata",
    "RetryPolicy",
    "ExecutionConfig",
    "Assertion",
    "QualityAssurance",
    "ObservabilityConfig",
    
    # WELAction
    "WELAction",
    
    # Document 组件
    "ScenarioInfo",
    "WorkflowConfig",
    "WorkflowMetrics",
    "FeedbackConfig",
    "AdjustmentTrigger",
    "AdjustmentPolicy",
    "StateTransition",
    "LifecycleState",
    
    # WELDocument
    "WELDocument",
    
    # WEL Generator
    "WELGenerator",
]


# ==================== 便捷导入 ====================
def create_generator() -> WELGenerator:
    """创建 WEL 生成器实例"""
    return WELGenerator()


def create_document(
    wel_id: str,
    planner_context: PlannerContext,
    scenario: ScenarioInfo
) -> WELDocument:
    """创建 WEL 文档实例"""
    return WELDocument(
        wel_id=wel_id,
        planner_context=planner_context,
        scenario=scenario
    )


# 添加到公共接口
__all__.extend([
    "create_generator",
    "create_document",
])

