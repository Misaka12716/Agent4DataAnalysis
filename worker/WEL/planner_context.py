"""
Planner 上下文组件
保留 Planner 的完整决策链路
"""

from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field, asdict
from datetime import datetime

from .enums import IntentType


@dataclass
class IntentInfo:
    """意图信息"""
    type: IntentType
    confidence: float
    description: str
    alternatives: List[IntentType] = field(default_factory=list)


@dataclass
class ExperienceInfo:
    """经验信息"""
    content: str
    relevance: float
    source: str
    timestamp: Optional[str] = None


@dataclass
class KnowledgeInfo:
    """知识信息"""
    content: Dict[str, Any]
    sources: List[str]
    confidence: float


@dataclass
class ToolInfo:
    """工具信息"""
    name: str
    description: str
    confidence: float
    capabilities: List[str] = field(default_factory=list)


@dataclass
class ToolRegistry:
    """工具注册表"""
    tools: List[ToolInfo] = field(default_factory=list)
    
    def get_tool(self, name: str) -> Optional[ToolInfo]:
        """根据名称获取工具"""
        for tool in self.tools:
            if tool.name == name:
                return tool
        return None
    
    def add_tool(self, tool: ToolInfo):
        """添加工具"""
        self.tools.append(tool)


@dataclass
class ConstraintSet:
    """约束集合"""
    time_limit: Optional[int] = None
    cost_limit: Optional[float] = None
    resource_limits: Dict[str, Any] = field(default_factory=dict)
    quality_requirements: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PlannerContext:
    """Planner 上下文 - 保留完整决策链路"""
    
    # 意图识别
    intent: IntentInfo
    
    # 需求分析
    requirement: Dict[str, Any]
    
    # 知识来源
    experience: Optional[ExperienceInfo] = None
    knowledge: Optional[KnowledgeInfo] = None
    
    # 工具匹配
    tools: ToolRegistry = field(default_factory=ToolRegistry)
    
    # 约束条件
    constraints: ConstraintSet = field(default_factory=ConstraintSet)
    
    # 元数据
    metadata: Dict[str, Any] = field(default_factory=dict)
    planner_version: str = "2.0"
    planning_timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        intent_dict = asdict(self.intent)
        intent_dict['type'] = self.intent.type.value
        if self.intent.alternatives:
            intent_dict['alternatives'] = [alt.value for alt in self.intent.alternatives]
        
        return {
            'intent': intent_dict,
            'requirement': self.requirement,
            'experience': asdict(self.experience) if self.experience else None,
            'knowledge': asdict(self.knowledge) if self.knowledge else None,
            'tools': {'tools': [asdict(t) for t in self.tools.tools]},
            'constraints': asdict(self.constraints),
            'metadata': self.metadata,
            'planner_version': self.planner_version,
            'planning_timestamp': self.planning_timestamp
        }

