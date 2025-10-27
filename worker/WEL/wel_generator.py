"""
WEL Generator v2.0
从 Planner 的输出生成 WEL 文档
"""

from typing import Dict, Any, List
import uuid

from .enums import (
    IntentType, ActionType, State, TriggerType, AdjustmentAction,
    AssertionType, Severity, FailureAction, ExecutionStrategy, RiskLevel
)
from .planner_context import (
    PlannerContext, IntentInfo, ExperienceInfo, KnowledgeInfo,
    ToolInfo, ToolRegistry, ConstraintSet
)
from .action_components import (
    ActionTarget, DataPort, DataFlow, ConfidenceBreakdown,
    ActionMetadata, RetryPolicy, ExecutionConfig, Assertion,
    QualityAssurance, ObservabilityConfig
)
from .wel_action import WELAction
from .document_components import (
    ScenarioInfo, WorkflowMetrics, AdjustmentTrigger, AdjustmentPolicy
)
from .wel_document import WELDocument


class WELGenerator:
    """WEL 生成器 v2.0 - 从 Planner 输出生成 WEL"""
    
    def __init__(self):
        self.version = "2.0"
    
    async def generate_from_planner(
        self,
        requirement: Dict[str, Any],
        experience: Dict[str, Any],
        knowledge: Dict[str, Any],
        tools: Dict[str, Any],
        tasks: Dict[str, Any],
        intent_type: IntentType = IntentType.CUSTOM,
        intent_confidence: float = 0.8
    ) -> WELDocument:
        """
        从 Planner 的完整输出生成 WEL 文档
        
        Args:
            requirement: organize_requirement 的输出
            experience: search_experience 的输出
            knowledge: search_knowledge 的输出
            tools: find_tools 的输出
            tasks: assign_tasks 的输出
            intent_type: 意图类型
            intent_confidence: 意图置信度
        """
        # 1. 构建 Planner 上下文
        planner_context = self._build_planner_context(
            requirement, experience, knowledge, tools,
            intent_type, intent_confidence
        )
        
        # 2. 构建场景信息
        scenario = self._build_scenario_info(requirement, tasks)
        
        # 3. 创建 WEL 文档
        wel_doc = WELDocument(
            wel_id=f"wel_{uuid.uuid4().hex[:12]}",
            planner_context=planner_context,
            scenario=scenario
        )
        
        # 4. 转换任务为 WEL 动作
        for task in tasks.get('tasks', []):
            action = self._convert_task_to_action(task, planner_context)
            wel_doc.add_action(action)
        
        # 5. 计算度量指标
        wel_doc.metrics = self._calculate_metrics(wel_doc.actions, planner_context)
        
        # 6. 生成调整策略
        wel_doc.adjustment_policy = self._generate_adjustment_policy(planner_context)
        
        # 7. 验证文档
        wel_doc.lifecycle.transition_to(State.VALIDATED, "WEL 文档生成完成")
        
        return wel_doc
    
    def _build_planner_context(
        self,
        requirement: Dict[str, Any],
        experience: Dict[str, Any],
        knowledge: Dict[str, Any],
        tools: Dict[str, Any],
        intent_type: IntentType,
        intent_confidence: float
    ) -> PlannerContext:
        """构建 Planner 上下文"""
        
        # 构建意图信息
        intent = IntentInfo(
            type=intent_type,
            confidence=intent_confidence,
            description=requirement.get('goal', '')
        )
        
        # 构建经验信息
        experience_info = None
        if experience.get('success'):
            experience_info = ExperienceInfo(
                content=experience.get('experience', ''),
                relevance=0.8,  # 可以根据实际情况计算
                source="experience_db"
            )
        
        # 构建知识信息
        knowledge_info = None
        if knowledge.get('success'):
            knowledge_info = KnowledgeInfo(
                content=knowledge.get('knowledge', {}),
                sources=knowledge.get('sources', []),
                confidence=0.8
            )
        
        # 构建工具注册表
        tool_registry = ToolRegistry()
        for tool_data in tools.get('tools', []):
            if isinstance(tool_data, str):
                tool = ToolInfo(
                    name=tool_data,
                    description=f"工具：{tool_data}",
                    confidence=0.8
                )
            else:
                tool = ToolInfo(
                    name=tool_data.get('name', ''),
                    description=tool_data.get('description', ''),
                    confidence=tool_data.get('confidence', 0.8),
                    capabilities=tool_data.get('capabilities', [])
                )
            tool_registry.add_tool(tool)
        
        # 构建约束集合
        constraints = ConstraintSet()
        if 'constraints' in requirement:
            const_data = requirement['constraints']
            if isinstance(const_data, str) and 'time' in const_data.lower():
                # 简单解析时间约束（实际需要更复杂的解析）
                constraints.time_limit = 3600
        
        return PlannerContext(
            intent=intent,
            requirement=requirement,
            experience=experience_info,
            knowledge=knowledge_info,
            tools=tool_registry,
            constraints=constraints
        )
    
    def _build_scenario_info(
        self,
        requirement: Dict[str, Any],
        tasks: Dict[str, Any]
    ) -> ScenarioInfo:
        """构建场景信息"""
        return ScenarioInfo(
            name=tasks.get('scenario', requirement.get('task_type', '未知场景')),
            goal=tasks.get('goal', requirement.get('goal', '')),
            success_definition=self._generate_success_definition(tasks, requirement),
            failure_definition="任何步骤失败且无法恢复"
        )
    
    def _convert_task_to_action(
        self,
        task: Dict[str, Any],
        context: PlannerContext
    ) -> WELAction:
        """将 Planner 的 task 转换为 WEL action"""
        
        # 1. 推断动作类型
        action_type = self._infer_action_type(task)
        
        # 2. 构建目标
        target = ActionTarget(
            entity=task.get('required_tool', 'unknown'),
            operation=self._infer_operation(action_type),
            parameters=task.get('parameters', {})
        )
        
        # 3. 构建数据流
        data_flow = self._build_data_flow(task, context)
        
        # 4. 构建元数据
        metadata = self._build_action_metadata(task, context)
        
        # 5. 构建执行配置
        execution = ExecutionConfig(
            strategy=ExecutionStrategy.IMMEDIATE,
            timeout=self._parse_deadline_to_seconds(task.get('deadline', '5分钟')),
            retry_policy=RetryPolicy(max_retries=3)
        )
        
        # 6. 构建质量保证
        quality = self._build_quality_assurance(task, action_type)
        
        # 7. 构建可观测性配置
        observability = ObservabilityConfig(
            enable_tracing=True,
            trace_id=f"trace_{uuid.uuid4().hex[:8]}",
            metric_tags={'worker_type': task.get('worker_type', 'unknown')}
        )
        
        return WELAction(
            action_id=f"action_{task['task_id']}",
            action_name=task.get('task_name', ''),
            action_type=action_type,
            target=target,
            data_flow=data_flow,
            metadata=metadata,
            execution=execution,
            quality=quality,
            observability=observability
        )
    
    def _infer_action_type(self, task: Dict[str, Any]) -> ActionType:
        """推断动作类型"""
        description = task.get('description', '').lower()
        task_name = task.get('task_name', '').lower()
        text = f"{description} {task_name}"
        
        # 细粒度匹配
        if any(word in text for word in ['查询', 'query', '获取', 'fetch', 'load', '加载']):
            return ActionType.DATA_QUERY
        elif any(word in text for word in ['插入', 'insert', '添加', 'add', '保存', 'save']):
            return ActionType.DATA_INSERT
        elif any(word in text for word in ['更新', 'update', '修改', 'modify']):
            return ActionType.DATA_UPDATE
        elif any(word in text for word in ['删除', 'delete', '移除', 'remove']):
            return ActionType.DATA_DELETE
        elif any(word in text for word in ['清洗', 'clean', '预处理', 'preprocess']):
            return ActionType.DATA_CLEAN
        elif any(word in text for word in ['转换', 'transform', '处理', 'process']):
            return ActionType.DATA_TRANSFORM
        elif any(word in text for word in ['聚合', 'aggregate', '分组', 'group']):
            return ActionType.DATA_AGGREGATE
        elif any(word in text for word in ['统计', 'statistical', '分析', 'analysis']):
            return ActionType.STATISTICAL_ANALYSIS
        elif any(word in text for word in ['相关性', 'correlation']):
            return ActionType.CORRELATION_ANALYSIS
        elif any(word in text for word in ['图表', 'chart', '可视化', 'visualize']):
            return ActionType.CHART_GENERATION
        elif any(word in text for word in ['报告', 'report', '报表']):
            return ActionType.REPORT_GENERATION
        else:
            return ActionType.CUSTOM_COMPUTE
    
    def _infer_operation(self, action_type: ActionType) -> str:
        """推断操作类型"""
        mapping = {
            ActionType.DATA_QUERY: "query",
            ActionType.DATA_INSERT: "insert",
            ActionType.DATA_UPDATE: "update",
            ActionType.DATA_DELETE: "delete",
            ActionType.DATA_CLEAN: "clean",
            ActionType.DATA_TRANSFORM: "transform",
            ActionType.DATA_AGGREGATE: "aggregate",
            ActionType.STATISTICAL_ANALYSIS: "analyze",
        }
        return mapping.get(action_type, "execute")
    
    def _build_data_flow(
        self,
        task: Dict[str, Any],
        context: PlannerContext
    ) -> DataFlow:
        """构建数据流"""
        inputs = []
        outputs = []
        
        # 从依赖关系构建输入端口
        for dep_id in task.get('dependencies', []):
            inputs.append(DataPort(
                name=f"input_from_{dep_id}",
                source=f"action_{dep_id}.output",
                required=True
            ))
        
        # 如果没有依赖，检查是否需要用户输入
        if not inputs:
            # 从参数中推断需要的输入
            for param_name, param_value in task.get('parameters', {}).items():
                if param_value is None or param_value == "":
                    inputs.append(DataPort(
                        name=param_name,
                        source=f"user_input.{param_name}",
                        required=True
                    ))
        
        # 默认输出
        outputs.append(DataPort(
            name="output",
            schema={"type": "object"}
        ))
        
        return DataFlow(inputs=inputs, outputs=outputs)
    
    def _build_action_metadata(
        self,
        task: Dict[str, Any],
        context: PlannerContext
    ) -> ActionMetadata:
        """构建动作元数据"""
        
        # 构建理由
        rationale = task.get('description', '')
        if context.experience:
            rationale += f"\n基于经验: {context.experience.content[:100]}"
        
        # 构建置信度分解
        tool = context.tools.get_tool(task.get('required_tool', ''))
        tool_confidence = tool.confidence if tool else 0.5
        
        confidence_breakdown = ConfidenceBreakdown(
            tool_match_confidence=tool_confidence,
            parameter_confidence=0.8,  # 可以根据参数完整性计算
            sequence_confidence=0.9 if task.get('dependencies') else 1.0,
            experience_based_confidence=context.experience.relevance if context.experience else 0.5
        )
        
        return ActionMetadata(
            natural_language_description=task.get('description', ''),
            rationale=rationale,
            alternative_actions=[],  # 可以根据工具列表生成备选方案
            confidence_breakdown=confidence_breakdown,
            source_info={
                'original_task_id': task['task_id'],
                'worker_type': task.get('worker_type', '')
            }
        )
    
    def _build_quality_assurance(
        self,
        task: Dict[str, Any],
        action_type: ActionType
    ) -> QualityAssurance:
        """构建质量保证"""
        assertions = []
        
        # 根据动作类型添加常见的断言
        if action_type == ActionType.DATA_QUERY:
            assertions.append(Assertion(
                name="connection_available",
                type=AssertionType.PRE_CONDITION,
                condition="database.status == 'connected'",
                error_message="数据库未连接",
                severity=Severity.ERROR,
                on_failure=FailureAction.ABORT
            ))
            assertions.append(Assertion(
                name="result_not_empty",
                type=AssertionType.POST_CONDITION,
                condition="len(output.results) > 0",
                error_message="查询结果为空",
                severity=Severity.WARNING,
                on_failure=FailureAction.CONTINUE
            ))
        
        return QualityAssurance(assertions=assertions)
    
    def _parse_deadline_to_seconds(self, deadline: str) -> int:
        """解析截止时间为秒数"""
        deadline_lower = deadline.lower()
        if '秒' in deadline_lower or 'second' in deadline_lower:
            return int(''.join(filter(str.isdigit, deadline)) or 30)
        elif '分钟' in deadline_lower or 'minute' in deadline_lower:
            return int(''.join(filter(str.isdigit, deadline)) or 5) * 60
        elif '小时' in deadline_lower or 'hour' in deadline_lower:
            return int(''.join(filter(str.isdigit, deadline)) or 1) * 3600
        else:
            return 300  # 默认5分钟
    
    def _calculate_metrics(
        self,
        actions: List[WELAction],
        context: PlannerContext
    ) -> WorkflowMetrics:
        """计算工作流度量指标"""
        
        # 预估执行时间
        estimated_duration = sum(action.execution.timeout for action in actions)
        
        # 动作数量
        action_count = len(actions)
        
        # 依赖深度（最长依赖链）
        dependency_depth = self._calculate_dependency_depth(actions)
        
        # 复杂度评分
        complexity_score = min((action_count / 10.0) * 0.5 + (dependency_depth / 5.0) * 0.5, 1.0)
        
        # 整体置信度
        if actions:
            avg_confidence = sum(
                action.metadata.confidence_breakdown.overall_confidence 
                for action in actions
            ) / len(actions)
            confidence_score = avg_confidence * 0.7 + context.intent.confidence * 0.3
        else:
            confidence_score = context.intent.confidence
        
        # 并行潜力（没有依赖的动作比例）
        independent_actions = sum(1 for action in actions if not action.data_flow.get_dependencies())
        parallel_potential = independent_actions / max(action_count, 1)
        
        # 风险等级
        risk_level = self._calculate_risk_level(complexity_score, confidence_score)
        
        return WorkflowMetrics(
            estimated_duration=estimated_duration,
            complexity_score=complexity_score,
            confidence_score=confidence_score,
            risk_level=risk_level,
            action_count=action_count,
            dependency_depth=dependency_depth,
            parallel_potential=parallel_potential
        )
    
    def _calculate_dependency_depth(self, actions: List[WELAction]) -> int:
        """计算依赖深度"""
        # 简化版本：统计最多的依赖链长度
        max_depth = 0
        for action in actions:
            deps = action.data_flow.get_dependencies()
            if deps:
                # 递归计算（简化版，实际需要图遍历）
                max_depth = max(max_depth, len(deps))
        return max_depth
    
    def _calculate_risk_level(
        self,
        complexity_score: float,
        confidence_score: float
    ) -> RiskLevel:
        """计算风险等级"""
        risk_score = (1 - confidence_score) * 0.6 + complexity_score * 0.4
        
        if risk_score < 0.3:
            return RiskLevel.LOW
        elif risk_score < 0.5:
            return RiskLevel.MEDIUM
        elif risk_score < 0.7:
            return RiskLevel.HIGH
        else:
            return RiskLevel.CRITICAL
    
    def _generate_success_definition(
        self,
        tasks: Dict[str, Any],
        requirement: Dict[str, Any]
    ) -> str:
        """生成成功定义"""
        goal = requirement.get('goal', '')
        output_req = requirement.get('output_requirement', '')
        return f"目标: {goal}\n输出要求: {output_req}\n所有任务成功完成且输出符合要求"
    
    def _generate_adjustment_policy(
        self,
        context: PlannerContext
    ) -> AdjustmentPolicy:
        """生成调整策略"""
        triggers = [
            AdjustmentTrigger(
                type=TriggerType.ACTION_FAILURE,
                condition="retry_count < 3",
                action=AdjustmentAction.RETRY_WITH_ALTERNATIVE,
                priority=1
            ),
            AdjustmentTrigger(
                type=TriggerType.TIMEOUT,
                condition="execution_time > timeout",
                action=AdjustmentAction.SKIP_OR_ABORT,
                priority=2
            ),
            AdjustmentTrigger(
                type=TriggerType.QUALITY_ISSUE,
                condition="quality_score < 0.7",
                action=AdjustmentAction.REPLAN,
                priority=3
            )
        ]
        
        return AdjustmentPolicy(
            enable_dynamic_adjustment=True,
            triggers=triggers,
            replan_threshold=0.3,
            max_adjustments=3
        )

