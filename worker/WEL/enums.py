"""
WEL 枚举类型定义
包含所有用于 WEL 系统的枚举常量
"""

from enum import Enum


class IntentType(Enum):
    """意图类型"""
    DATA_ANALYSIS = "data_analysis"
    DATA_PROCESSING = "data_processing"
    REPORT_GENERATION = "report_generation"
    QUERY_EXECUTION = "query_execution"
    WORKFLOW_AUTOMATION = "workflow_automation"
    KNOWLEDGE_EXTRACTION = "knowledge_extraction"
    CUSTOM = "custom"


class ActionType(Enum):
    """动作类型 - 细粒度分类"""
    # 数据操作 (4种)
    DATA_QUERY = "data_query"
    DATA_INSERT = "data_insert"
    DATA_UPDATE = "data_update"
    DATA_DELETE = "data_delete"
    
    # 数据处理 (4种)
    DATA_CLEAN = "data_clean"
    DATA_TRANSFORM = "data_transform"
    DATA_AGGREGATE = "data_aggregate"
    DATA_VALIDATE = "data_validate"
    
    # 分析和计算 (4种)
    STATISTICAL_ANALYSIS = "statistical_analysis"
    CORRELATION_ANALYSIS = "correlation_analysis"
    TREND_ANALYSIS = "trend_analysis"
    CUSTOM_COMPUTE = "custom_compute"
    
    # 可视化 (3种)
    CHART_GENERATION = "chart_generation"
    REPORT_GENERATION = "report_generation"
    DASHBOARD_UPDATE = "dashboard_update"
    
    # 工作流控制 (3种)
    CONDITIONAL_BRANCH = "conditional_branch"
    LOOP_ITERATION = "loop_iteration"
    PARALLEL_EXECUTION = "parallel_execution"
    
    # 外部交互 (3种)
    API_CALL = "api_call"
    FILE_OPERATION = "file_operation"
    NOTIFICATION = "notification"


class ExecutionStrategy(Enum):
    """执行策略"""
    IMMEDIATE = "immediate"
    SCHEDULED = "scheduled"
    CONDITIONAL = "conditional"
    MANUAL = "manual"


class AssertionType(Enum):
    """断言类型"""
    PRE_CONDITION = "pre_condition"  # 前置条件：执行前必须满足
    POST_CONDITION = "post_condition" # 后置条件：执行后必须满足
    INVARIANT = "invariant" # 不变量：执行过程中必须保持不变


class Severity(Enum):
    """严重程度"""
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class FailureAction(Enum):
    """失败时的动作"""
    ABORT = "abort"
    SKIP = "skip"
    RETRY = "retry"
    USE_ALTERNATIVE = "use_alternative"
    CONTINUE = "continue"


class RiskLevel(Enum):
    """风险等级"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class FeedbackFrequency(Enum):
    """反馈频率"""
    PER_ACTION = "per_action"
    PER_STAGE = "per_stage"
    ON_COMPLETION = "on_completion"
    ON_ERROR = "on_error"


class FeedbackChannel(Enum):
    """反馈通道"""
    PLANNER = "planner"
    LOG = "log"
    METRICS = "metrics"
    NOTIFICATION = "notification"


class FeedbackFormat(Enum):
    """反馈格式"""
    STRUCTURED = "structured"
    NATURAL_LANGUAGE = "natural_language"
    BOTH = "both"


class TriggerType(Enum):
    """触发类型"""
    ACTION_FAILURE = "action_failure"
    TIMEOUT = "timeout"
    QUALITY_ISSUE = "quality_issue"
    COST_OVERRUN = "cost_overrun"
    CONFIDENCE_DROP = "confidence_drop"


class AdjustmentAction(Enum):
    """调整动作"""
    RETRY_WITH_ALTERNATIVE = "retry_with_alternative"
    SKIP_OR_ABORT = "skip_or_abort"
    REPLAN = "replan"
    MANUAL_INTERVENTION = "manual_intervention"


class State(Enum):
    """生命周期状态"""
    CREATED = "created"
    VALIDATED = "validated"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

