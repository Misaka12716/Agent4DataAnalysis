"""
WEL Document 组件
包含 WELDocument 的各个配置和状态组件
"""

from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from datetime import datetime

from .enums import (
    RiskLevel, FeedbackFrequency, FeedbackChannel, FeedbackFormat,
    TriggerType, AdjustmentAction, State
)


# ==================== 场景和配置组件 ====================

@dataclass
class ScenarioInfo:
    """场景信息"""
    name: str
    goal: str
    success_definition: str
    failure_definition: str = ""


@dataclass
class WorkflowConfig:
    """工作流配置"""
    execution_mode: str = "sequential"
    max_parallelism: int = 5
    enable_transaction: bool = True
    global_timeout: int = 3600


# ==================== 度量指标组件 ====================

@dataclass
class WorkflowMetrics:
    """工作流度量指标"""
    estimated_duration: int = 0
    estimated_cost: float = 0.0
    complexity_score: float = 0.0
    confidence_score: float = 0.0
    risk_level: RiskLevel = RiskLevel.LOW
    action_count: int = 0
    dependency_depth: int = 0
    parallel_potential: float = 0.0


# ==================== 反馈和调整组件 ====================

@dataclass
class FeedbackConfig:
    """反馈配置"""
    enable_feedback: bool = True
    frequency: FeedbackFrequency = FeedbackFrequency.PER_ACTION
    channels: List[FeedbackChannel] = field(default_factory=lambda: [FeedbackChannel.PLANNER, FeedbackChannel.LOG])
    format: FeedbackFormat = FeedbackFormat.STRUCTURED


@dataclass
class AdjustmentTrigger:
    """调整触发器"""
    type: TriggerType
    condition: str
    action: AdjustmentAction
    priority: int = 0


@dataclass
class AdjustmentPolicy:
    """调整策略"""
    enable_dynamic_adjustment: bool = True
    triggers: List[AdjustmentTrigger] = field(default_factory=list)
    replan_threshold: float = 0.3
    max_adjustments: int = 3


# ==================== 生命周期组件 ====================

@dataclass
class StateTransition:
    """状态转换记录"""
    from_state: State
    to_state: State
    timestamp: str
    reason: str = ""


@dataclass
class LifecycleState:
    """生命周期状态"""
    state: State = State.CREATED
    state_history: List[StateTransition] = field(default_factory=list)
    error_info: Optional[Dict[str, Any]] = None
    
    def transition_to(self, new_state: State, reason: str = ""):
        """状态转换"""
        transition = StateTransition(
            from_state=self.state,
            to_state=new_state,
            timestamp=datetime.now().isoformat(),
            reason=reason
        )
        self.state_history.append(transition)
        self.state = new_state

