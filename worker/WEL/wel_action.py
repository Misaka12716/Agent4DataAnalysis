"""
WELAction - WEL 动作主类
分层设计的核心类，整合所有 Action 组件
"""

from typing import Dict, Any
from dataclasses import dataclass, field, asdict

from .enums import ActionType
from .action_components import (
    ActionTarget, DataFlow, ActionMetadata, ExecutionConfig,
    QualityAssurance, ObservabilityConfig
)


@dataclass
class WELAction:
    """WEL 动作 - 分层设计（优化版）"""
    
    # 核心层（必需）
    action_id: str
    action_name: str
    action_type: ActionType
    target: ActionTarget
    
    # 数据流层
    data_flow: DataFlow
    
    # 元数据层（Planner 语义）
    metadata: ActionMetadata = field(default_factory=ActionMetadata)
    
    # 执行层
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    
    # 质量层
    quality: QualityAssurance = field(default_factory=QualityAssurance)
    
    # 可观测层
    observability: ObservabilityConfig = field(default_factory=ObservabilityConfig)
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        # 序列化 alternative_actions
        alt_actions = []
        for alt in self.metadata.alternative_actions:
            alt_dict = asdict(alt)
            alt_dict['action_type'] = alt.action_type.value
            alt_actions.append(alt_dict)
        
        # 序列化 assertions
        assertions = []
        for assertion in self.quality.assertions:
            assertion_dict = asdict(assertion)
            assertion_dict['type'] = assertion.type.value
            assertion_dict['severity'] = assertion.severity.value
            assertion_dict['on_failure'] = assertion.on_failure.value
            assertions.append(assertion_dict)
        
        return {
            'action_id': self.action_id,
            'action_name': self.action_name,
            'action_type': self.action_type.value,
            'target': asdict(self.target),
            'data_flow': {
                'inputs': [asdict(p) for p in self.data_flow.inputs],
                'outputs': [asdict(p) for p in self.data_flow.outputs]
            },
            'metadata': {
                'natural_language_description': self.metadata.natural_language_description,
                'rationale': self.metadata.rationale,
                'alternative_actions': alt_actions,
                'confidence_breakdown': asdict(self.metadata.confidence_breakdown),
                'source_info': self.metadata.source_info
            },
            'execution': {
                'strategy': self.execution.strategy.value,
                'timeout': self.execution.timeout,
                'retry_policy': asdict(self.execution.retry_policy),
                'priority': self.execution.priority
            },
            'quality': {
                'assertions': assertions
            },
            'observability': asdict(self.observability)
        }
    
    def to_pdl_step(self) -> Dict[str, Any]:
        """转换为 PDL 格式（向后兼容）"""
        # 映射 ActionType 到 TaskType
        action_to_task_mapping = {
            ActionType.DATA_QUERY: "query",
            ActionType.DATA_INSERT: "insert",
            ActionType.DATA_UPDATE: "update",
            ActionType.DATA_DELETE: "delete",
            ActionType.DATA_CLEAN: "transform",
            ActionType.DATA_TRANSFORM: "transform",
            ActionType.DATA_AGGREGATE: "compute",
            ActionType.STATISTICAL_ANALYSIS: "compute",
            ActionType.CORRELATION_ANALYSIS: "compute",
            ActionType.CHART_GENERATION: "compute",
            ActionType.REPORT_GENERATION: "compute",
        }
        
        # 从 DataFlow 提取传统的 input/output mapping
        input_mapping = {}
        for port in self.data_flow.inputs:
            if port.source:
                input_mapping[port.name] = port.source
        
        output_mapping = {}
        for port in self.data_flow.outputs:
            output_mapping[port.name] = f"{self.action_id}.{port.name}"
        
        return {
            'step_id': self.action_id,
            'step_name': self.action_name,
            'task_type': action_to_task_mapping.get(self.action_type, "compute"),
            'target_entity': self.target.entity,
            'parameters': self.target.parameters,
            'dependencies': self.data_flow.get_dependencies(),
            'input_mapping': input_mapping,
            'output_mapping': output_mapping,
            'description': self.metadata.natural_language_description,
            'timeout': self.execution.timeout,
            'retry_policy': asdict(self.execution.retry_policy),
            'metadata': {
                'rationale': self.metadata.rationale,
                'overall_confidence': self.metadata.confidence_breakdown.overall_confidence
            }
        }

