"""
WELDocument - WEL 文档主类
完整的工作流执行语言文档
"""

from typing import Dict, Any, List
from dataclasses import dataclass, field, asdict
from datetime import datetime
import json

from .planner_context import PlannerContext
from .wel_action import WELAction
from .document_components import (
    ScenarioInfo, WorkflowConfig, WorkflowMetrics,
    FeedbackConfig, AdjustmentPolicy, LifecycleState
)


@dataclass
class WELDocument:
    """WEL 文档 - 完整的工作流执行语言"""
    
    # 文档标识
    wel_id: str
    planner_context: PlannerContext
    scenario: ScenarioInfo
    
    # 可选字段（有默认值）
    version: str = "2.0"
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    
    # 执行动作序列
    actions: List[WELAction] = field(default_factory=list)
    
    # 工作流配置
    workflow_config: WorkflowConfig = field(default_factory=WorkflowConfig)
    
    # 度量指标
    metrics: WorkflowMetrics = field(default_factory=WorkflowMetrics)
    
    # 反馈配置
    feedback_config: FeedbackConfig = field(default_factory=FeedbackConfig)
    
    # 调整策略
    adjustment_policy: AdjustmentPolicy = field(default_factory=AdjustmentPolicy)
    
    # 生命周期状态
    lifecycle: LifecycleState = field(default_factory=LifecycleState)
    
    def add_action(self, action: WELAction):
        """添加动作"""
        self.actions.append(action)
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        # 序列化 metrics
        metrics_dict = asdict(self.metrics)
        metrics_dict['risk_level'] = self.metrics.risk_level.value
        
        # 序列化 adjustment triggers
        triggers = []
        for trigger in self.adjustment_policy.triggers:
            trigger_dict = asdict(trigger)
            trigger_dict['type'] = trigger.type.value
            trigger_dict['action'] = trigger.action.value
            triggers.append(trigger_dict)
        
        # 序列化 state history
        state_history = []
        for trans in self.lifecycle.state_history:
            trans_dict = asdict(trans)
            trans_dict['from_state'] = trans.from_state.value
            trans_dict['to_state'] = trans.to_state.value
            state_history.append(trans_dict)
        
        return {
            'wel_id': self.wel_id,
            'version': self.version,
            'created_at': self.created_at,
            'planner_context': self.planner_context.to_dict(),
            'scenario': asdict(self.scenario),
            'actions': [action.to_dict() for action in self.actions],
            'workflow_config': asdict(self.workflow_config),
            'metrics': metrics_dict,
            'feedback_config': {
                'enable_feedback': self.feedback_config.enable_feedback,
                'frequency': self.feedback_config.frequency.value,
                'channels': [c.value for c in self.feedback_config.channels],
                'format': self.feedback_config.format.value
            },
            'adjustment_policy': {
                'enable_dynamic_adjustment': self.adjustment_policy.enable_dynamic_adjustment,
                'triggers': triggers,
                'replan_threshold': self.adjustment_policy.replan_threshold,
                'max_adjustments': self.adjustment_policy.max_adjustments
            },
            'lifecycle': {
                'state': self.lifecycle.state.value,
                'state_history': state_history,
                'error_info': self.lifecycle.error_info
            }
        }
    
    def to_json(self) -> str:
        """转换为 JSON"""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)
    
    def to_pdl_document(self) -> Dict[str, Any]:
        """转换为 PDL 格式（向后兼容）"""
        return {
            'pdl_id': f"pdl_{self.wel_id}",
            'version': self.version,
            'created_at': self.created_at,
            'scenario': self.scenario.name,
            'goal': self.scenario.goal,
            'steps': [action.to_pdl_step() for action in self.actions],
            'global_config': asdict(self.workflow_config),
            'data_flow': {}
        }

