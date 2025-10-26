"""
WEL Action 组件（分层设计）
包含 Action 的各个层级组件：目标、数据流、元数据、执行、质量、可观测
"""

from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field, asdict
import json

from .enums import (
    ActionType, ExecutionStrategy, AssertionType, 
    Severity, FailureAction
)


# ==================== 核心层组件 ====================

@dataclass
class ActionTarget:
    """动作目标"""
    entity: str
    operation: str
    parameters: Dict[str, Any] = field(default_factory=dict)
    
    def to_qualified_name(self) -> str:
        """生成完全限定名"""
        return f"{self.entity}.{self.operation}"


# ==================== 数据流层组件 ====================

@dataclass
class DataPort:
    """数据端口"""
    name: str
    source: Optional[str] = None
    schema: Optional[Dict] = None
    required: bool = True
    transform: Optional[str] = None
    default_value: Any = None
    
    def resolve_source(self, context: Dict[str, Any]) -> Any:
        """解析数据源"""
        if not self.source:
            return self.default_value
        
        parts = self.source.split('.')
        if len(parts) != 2:
            return self.default_value
        
        source_id, field_name = parts
        if source_id not in context:
            return self.default_value
        
        value = context[source_id].get(field_name, self.default_value)
        
        # 应用转换
        if self.transform and value is not None:
            value = self._apply_transform(value, self.transform)
        
        return value
    
    def _apply_transform(self, value: Any, transform: str) -> Any:
        """应用数据转换"""
        transforms = {
            'to_json': json.dumps,
            'to_string': str,
            'to_int': int,
            'to_float': float,
            'to_list': lambda v: v if isinstance(v, list) else [v]
        }
        return transforms.get(transform, lambda x: x)(value)


@dataclass
class DataFlow:
    """统一的数据流描述"""
    inputs: List[DataPort] = field(default_factory=list)
    outputs: List[DataPort] = field(default_factory=list)
    
    def validate(self) -> bool:
        """验证数据流的完整性"""
        for port in self.inputs:
            if port.required and not port.source:
                return False
        return True
    
    def get_dependencies(self) -> List[str]:
        """提取依赖的 action_id"""
        deps = set()
        for port in self.inputs:
            if port.source and '.' in port.source:
                action_id = port.source.split('.')[0]
                if action_id != "user_input":
                    deps.add(action_id)
        return list(deps)


# ==================== 元数据层组件 ====================

@dataclass
class ConfidenceBreakdown:
    """置信度分解"""
    tool_match_confidence: float = 1.0
    parameter_confidence: float = 1.0
    sequence_confidence: float = 1.0
    experience_based_confidence: float = 1.0
    
    @property
    def overall_confidence(self) -> float:
        """综合置信度"""
        weights = [0.3, 0.3, 0.2, 0.2]
        scores = [
            self.tool_match_confidence,
            self.parameter_confidence,
            self.sequence_confidence,
            self.experience_based_confidence
        ]
        return sum(w * s for w, s in zip(weights, scores))


@dataclass
class AlternativeAction:
    """备选动作"""
    action_type: ActionType
    target: str
    reason: str
    confidence: float


@dataclass
class ActionMetadata:
    """动作元数据"""
    natural_language_description: str = ""
    rationale: str = ""
    alternative_actions: List[AlternativeAction] = field(default_factory=list)
    confidence_breakdown: ConfidenceBreakdown = field(default_factory=ConfidenceBreakdown)
    source_info: Dict[str, Any] = field(default_factory=dict)


# ==================== 执行层组件 ====================

@dataclass
class RetryPolicy:
    """重试策略"""
    max_retries: int = 3
    retry_delay: int = 1
    backoff_multiplier: float = 2.0
    retry_on_errors: List[str] = field(default_factory=lambda: ["timeout", "network_error"])


@dataclass
class ExecutionConfig:
    """执行配置"""
    strategy: ExecutionStrategy = ExecutionStrategy.IMMEDIATE
    timeout: int = 300
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    priority: int = 0


# ==================== 质量层组件 ====================

@dataclass
class Assertion:
    """统一的断言描述"""
    name: str
    type: AssertionType
    condition: str
    error_message: str
    severity: Severity = Severity.ERROR
    on_failure: FailureAction = FailureAction.ABORT
    
    def evaluate(self, context: Dict[str, Any]) -> bool:
        """评估断言"""
        try:
            # 简化版本，实际需要安全的表达式解析器
            return eval(self.condition, {"__builtins__": {}}, context)
        except Exception as e:
            print(f"断言评估失败: {e}")
            return False


@dataclass
class QualityAssurance:
    """质量保证 - 统一断言系统"""
    assertions: List[Assertion] = field(default_factory=list)
    
    def get_pre_conditions(self) -> List[Assertion]:
        """获取前置条件"""
        return [a for a in self.assertions if a.type == AssertionType.PRE_CONDITION]
    
    def get_post_conditions(self) -> List[Assertion]:
        """获取后置条件"""
        return [a for a in self.assertions if a.type == AssertionType.POST_CONDITION]
    
    def get_invariants(self) -> List[Assertion]:
        """获取不变量"""
        return [a for a in self.assertions if a.type == AssertionType.INVARIANT]


# ==================== 可观测层组件 ====================

@dataclass
class ObservabilityConfig:
    """可观测性配置"""
    enable_tracing: bool = True
    trace_id: Optional[str] = None
    span_id: Optional[str] = None
    enable_metrics: bool = True
    metric_tags: Dict[str, str] = field(default_factory=dict)
    log_level: str = "INFO"
    log_context: Dict[str, Any] = field(default_factory=dict)
    checkpoint_enabled: bool = False
    checkpoint_strategy: str = "on_completion"
    custom_tags: Dict[str, str] = field(default_factory=dict)

