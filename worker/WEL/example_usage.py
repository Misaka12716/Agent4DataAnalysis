"""
WEL v2.0 使用示例
演示如何使用模块化的 WEL 包
"""

import asyncio
import json

# 从 WEL 包导入所需组件
from WEL import (
    WELGenerator,
    IntentType,
    ActionType,
    State
)


async def test_wel_generator_v2():
    """测试 WEL 生成器 v2.0"""
    
    # 模拟 Planner 的输出
    requirement = {
        'task_type': '数据分析',
        'goal': '分析麻醉药物对痛感的影响',
        'input_data': '患者麻醉记录表',
        'output_requirement': '统计报告和可视化图表',
        'constraints': '时间限制: 1小时内'
    }
    
    experience = {
        'success': True,
        'experience': '类似的医疗数据分析任务，建议先清洗数据再分析'
    }
    
    knowledge = {
        'success': False,
        'knowledge': {}
    }
    
    tools = {
        'success': True,
        'tools': [
            {
                'name': 'anesthesia_records',
                'description': '麻醉记录数据库',
                'confidence': 0.9
            },
            {
                'name': 'data_cleaner',
                'description': '数据清洗工具',
                'confidence': 0.85
            },
            {
                'name': 'statistical_analyzer',
                'description': '统计分析工具',
                'confidence': 0.8
            }
        ]
    }
    
    tasks = {
        'scenario': '麻醉药物痛感影响分析',
        'goal': '分析表格数据中麻醉药物剂量与患者痛感评分的关系',
        'tasks': [
            {
                'task_id': 1,
                'task_name': '加载麻醉数据',
                'description': '从数据库加载患者麻醉记录',
                'required_tool': 'anesthesia_records',
                'worker_type': 'database_worker',
                'dependencies': [],
                'deadline': '30秒',
                'parameters': {
                    'fields': ['patient_id', 'drug_name', 'dosage', 'pain_score']
                }
            },
            {
                'task_id': 2,
                'task_name': '数据清洗',
                'description': '清洗数据，去除空值和异常值',
                'required_tool': 'data_cleaner',
                'worker_type': 'transform_worker',
                'dependencies': [1],
                'deadline': '1分钟',
                'parameters': {'remove_nulls': True}
            },
            {
                'task_id': 3,
                'task_name': '统计分析',
                'description': '分析药物剂量与痛感的相关性',
                'required_tool': 'statistical_analyzer',
                'worker_type': 'compute_worker',
                'dependencies': [2],
                'deadline': '2分钟',
                'parameters': {'method': 'correlation'}
            }
        ]
    }
    
    # 生成 WEL
    generator = WELGenerator()
    wel_doc = await generator.generate_from_planner(
        requirement=requirement,
        experience=experience,
        knowledge=knowledge,
        tools=tools,
        tasks=tasks,
        intent_type=IntentType.DATA_ANALYSIS,
        intent_confidence=0.9
    )
    
    # 输出 WEL JSON
    print("=" * 60)
    print("WEL 文档 v2.0")
    print("=" * 60)
    print(wel_doc.to_json())
    
    # 输出关键指标
    print("\n" + "=" * 60)
    print("度量指标")
    print("=" * 60)
    print(f"预估时间: {wel_doc.metrics.estimated_duration}秒")
    print(f"动作数量: {wel_doc.metrics.action_count}")
    print(f"依赖深度: {wel_doc.metrics.dependency_depth}")
    print(f"复杂度: {wel_doc.metrics.complexity_score:.2f}")
    print(f"置信度: {wel_doc.metrics.confidence_score:.2f}")
    print(f"风险等级: {wel_doc.metrics.risk_level.value}")
    print(f"并行潜力: {wel_doc.metrics.parallel_potential:.2%}")
    
    # 输出数据流分析
    print("\n" + "=" * 60)
    print("数据流分析")
    print("=" * 60)
    for action in wel_doc.actions:
        print(f"\n动作: {action.action_name} ({action.action_id})")
        print(f"  类型: {action.action_type.value}")
        print(f"  依赖: {action.data_flow.get_dependencies()}")
        print(f"  置信度: {action.metadata.confidence_breakdown.overall_confidence:.2f}")
        print(f"  超时: {action.execution.timeout}秒")
    
    # 输出 PDL 格式（向后兼容）
    print("\n" + "=" * 60)
    print("PDL 格式（向后兼容）")
    print("=" * 60)
    print(json.dumps(wel_doc.to_pdl_document(), ensure_ascii=False, indent=2))
    
    # 演示生命周期管理
    print("\n" + "=" * 60)
    print("生命周期管理")
    print("=" * 60)
    wel_doc.lifecycle.transition_to(State.EXECUTING, "开始执行工作流")
    wel_doc.lifecycle.transition_to(State.COMPLETED, "工作流执行成功")
    
    for trans in wel_doc.lifecycle.state_history:
        print(f"{trans.timestamp}: {trans.from_state.value} → {trans.to_state.value}")
        print(f"  原因: {trans.reason}")


if __name__ == "__main__":
    asyncio.run(test_wel_generator_v2())

