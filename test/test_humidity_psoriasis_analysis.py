"""
空气湿度对银屑病影响的相关性分析测试案例
本测试案例使用 WEL v2.0 框架来执行完整的数据分析工作流

测试场景：
- 分析空气湿度与银屑病症状严重程度的相关性
- 使用虚拟测试数据模拟真实医疗场景
- 生成统计分析报告和可视化图表
"""

import asyncio
import json
import sys
from pathlib import Path

# 添加父目录到 Python 路径，以便导入 WEL 模块
sys.path.insert(0, str(Path(__file__).parent.parent))

# 从 WEL 包导入所需组件
from worker.WEL import (
    WELGenerator,
    IntentType,
    ActionType,
    State
)


# ==================== 虚拟测试数据 ====================

def generate_test_data():
    """生成虚拟的空气湿度与银屑病相关测试数据"""
    return {
        'patients': [
            # 患者ID, 观测日期, 空气湿度(%), 银屑病严重程度评分(PASI: 0-72)
            {'patient_id': 'P001', 'date': '2025-01-01', 'humidity': 30, 'pasi_score': 28.5},
            {'patient_id': 'P001', 'date': '2025-01-08', 'humidity': 35, 'pasi_score': 26.2},
            {'patient_id': 'P001', 'date': '2025-01-15', 'humidity': 45, 'pasi_score': 22.8},
            {'patient_id': 'P001', 'date': '2025-01-22', 'humidity': 50, 'pasi_score': 19.5},
            {'patient_id': 'P001', 'date': '2025-01-29', 'humidity': 55, 'pasi_score': 17.2},
            
            {'patient_id': 'P002', 'date': '2025-01-01', 'humidity': 25, 'pasi_score': 32.1},
            {'patient_id': 'P002', 'date': '2025-01-08', 'humidity': 30, 'pasi_score': 29.8},
            {'patient_id': 'P002', 'date': '2025-01-15', 'humidity': 40, 'pasi_score': 25.5},
            {'patient_id': 'P002', 'date': '2025-01-22', 'humidity': 45, 'pasi_score': 22.3},
            {'patient_id': 'P002', 'date': '2025-01-29', 'humidity': 60, 'pasi_score': 16.8},
            
            {'patient_id': 'P003', 'date': '2025-01-01', 'humidity': 20, 'pasi_score': 35.6},
            {'patient_id': 'P003', 'date': '2025-01-08', 'humidity': 25, 'pasi_score': 33.2},
            {'patient_id': 'P003', 'date': '2025-01-15', 'humidity': 35, 'pasi_score': 28.9},
            {'patient_id': 'P003', 'date': '2025-01-22', 'humidity': 50, 'pasi_score': 21.7},
            {'patient_id': 'P003', 'date': '2025-01-29', 'humidity': 65, 'pasi_score': 15.3},
            
            {'patient_id': 'P004', 'date': '2025-01-01', 'humidity': 28, 'pasi_score': 30.2},
            {'patient_id': 'P004', 'date': '2025-01-08', 'humidity': 32, 'pasi_score': 28.5},
            {'patient_id': 'P004', 'date': '2025-01-15', 'humidity': 42, 'pasi_score': 24.1},
            {'patient_id': 'P004', 'date': '2025-01-22', 'humidity': 48, 'pasi_score': 20.6},
            {'patient_id': 'P004', 'date': '2025-01-29', 'humidity': 58, 'pasi_score': 16.9},
            
            {'patient_id': 'P005', 'date': '2025-01-01', 'humidity': 22, 'pasi_score': 34.8},
            {'patient_id': 'P005', 'date': '2025-01-08', 'humidity': 28, 'pasi_score': 31.5},
            {'patient_id': 'P005', 'date': '2025-01-15', 'humidity': 38, 'pasi_score': 26.7},
            {'patient_id': 'P005', 'date': '2025-01-22', 'humidity': 52, 'pasi_score': 19.8},
            {'patient_id': 'P005', 'date': '2025-01-29', 'humidity': 62, 'pasi_score': 14.5},
        ],
        'metadata': {
            'study_name': '空气湿度对银屑病影响研究',
            'observation_period': '2025年1月',
            'patient_count': 5,
            'observation_count': 25,
            'pasi_range': '0-72 (0为无症状，72为最严重)',
            'humidity_range': '20-65%'
        }
    }


# ==================== WEL 工作流测试 ====================

async def test_humidity_psoriasis_correlation():
    """测试空气湿度与银屑病相关性分析的 WEL 工作流生成"""
    
    print("=" * 80)
    print("空气湿度对银屑病影响的相关性分析测试")
    print("=" * 80)
    print()
    
    # 1. 模拟 Planner 的 organize_requirement 输出
    requirement = {
        'task_type': '医疗数据相关性分析',
        'goal': '分析空气湿度对银屑病患者症状严重程度的影响',
        'input_data': '患者银屑病观测记录表（包含湿度和PASI评分）',
        'output_requirement': '相关性分析报告、散点图、趋势图和统计显著性检验结果',
        'constraints': '时间限制: 30分钟内完成分析',
        'domain': '医疗健康',
        'analysis_type': '相关性分析'
    }
    
    # 2. 模拟 search_experience 输出
    experience = {
        'success': True,
        'experience': (
            '类似的医疗环境因素分析显示，应该：\n'
            '1. 先对数据进行清洗，去除异常值和缺失值\n'
            '2. 进行描述性统计分析，了解数据分布\n'
            '3. 使用皮尔逊或斯皮尔曼相关系数进行相关性检验\n'
            '4. 绘制散点图和回归线以可视化相关性\n'
            '5. 注意控制混杂变量，如季节、患者年龄等'
        )
    }
    
    # 3. 模拟 search_knowledge 输出
    knowledge = {
        'success': True,
        'knowledge': {
            'domain_facts': [
                'PASI评分是银屑病严重程度的标准评估工具，范围0-72',
                '较低的空气湿度会导致皮肤干燥，可能加重银屑病症状',
                '理想的室内湿度范围通常为40-60%',
                '银屑病是一种慢性炎症性皮肤病，受多种环境因素影响'
            ],
            'analysis_methods': [
                '皮尔逊相关系数：适用于线性相关性分析',
                '斯皮尔曼等级相关：适用于非线性单调关系',
                'P值检验：评估相关性的统计显著性'
            ]
        },
        'sources': ['医疗知识库', '皮肤病学文献']
    }
    
    # 4. 模拟 find_tools 输出
    tools = {
        'success': True,
        'tools': [
            {
                'name': 'medical_database',
                'description': '医疗数据库，存储患者观测记录',
                'confidence': 0.95,
                'capabilities': ['query', 'filter', 'aggregate']
            },
            {
                'name': 'data_cleaner',
                'description': '数据清洗工具，处理缺失值和异常值',
                'confidence': 0.90,
                'capabilities': ['remove_nulls', 'outlier_detection', 'normalization']
            },
            {
                'name': 'statistical_analyzer',
                'description': '统计分析工具，支持相关性分析和假设检验',
                'confidence': 0.92,
                'capabilities': ['correlation', 'regression', 'significance_test']
            },
            {
                'name': 'data_validator',
                'description': '数据验证工具，确保数据质量',
                'confidence': 0.88,
                'capabilities': ['schema_validation', 'range_check', 'consistency_check']
            },
            {
                'name': 'visualization_engine',
                'description': '数据可视化引擎，生成图表和报告',
                'confidence': 0.85,
                'capabilities': ['scatter_plot', 'trend_line', 'heatmap', 'report_generation']
            }
        ]
    }
    
    # 5. 模拟 assign_tasks 输出
    tasks = {
        'scenario': '空气湿度与银屑病相关性分析',
        'goal': '通过统计分析确定空气湿度与银屑病严重程度的相关关系',
        'tasks': [
            {
                'task_id': 1,
                'task_name': '加载患者观测数据',
                'description': '从医疗数据库加载患者银屑病观测记录，包括患者ID、日期、湿度和PASI评分',
                'required_tool': 'medical_database',
                'worker_type': 'database_worker',
                'dependencies': [],
                'deadline': '1分钟',
                'parameters': {
                    'table': 'psoriasis_observations',
                    'fields': ['patient_id', 'date', 'humidity', 'pasi_score'],
                    'filters': {'date_range': '2025-01'}
                }
            },
            {
                'task_id': 2,
                'task_name': '数据验证',
                'description': '验证数据完整性和范围，确保湿度在0-100%之间，PASI评分在0-72之间',
                'required_tool': 'data_validator',
                'worker_type': 'validation_worker',
                'dependencies': [1],
                'deadline': '30秒',
                'parameters': {
                    'validation_rules': {
                        'humidity': {'min': 0, 'max': 100},
                        'pasi_score': {'min': 0, 'max': 72}
                    }
                }
            },
            {
                'task_id': 3,
                'task_name': '数据清洗',
                'description': '清洗数据，去除缺失值和异常值，确保数据质量',
                'required_tool': 'data_cleaner',
                'worker_type': 'transform_worker',
                'dependencies': [2],
                'deadline': '2分钟',
                'parameters': {
                    'remove_nulls': True,
                    'outlier_method': 'iqr',
                    'outlier_threshold': 3.0
                }
            },
            {
                'task_id': 4,
                'task_name': '描述性统计分析',
                'description': '计算湿度和PASI评分的均值、标准差、中位数等描述性统计量',
                'required_tool': 'statistical_analyzer',
                'worker_type': 'compute_worker',
                'dependencies': [3],
                'deadline': '1分钟',
                'parameters': {
                    'statistics': ['mean', 'std', 'median', 'min', 'max'],
                    'variables': ['humidity', 'pasi_score']
                }
            },
            {
                'task_id': 5,
                'task_name': '相关性分析',
                'description': '分析空气湿度与PASI评分的相关性，计算皮尔逊和斯皮尔曼相关系数',
                'required_tool': 'statistical_analyzer',
                'worker_type': 'compute_worker',
                'dependencies': [3],
                'deadline': '3分钟',
                'parameters': {
                    'method': ['pearson', 'spearman'],
                    'x_variable': 'humidity',
                    'y_variable': 'pasi_score',
                    'significance_level': 0.05
                }
            },
            {
                'task_id': 6,
                'task_name': '线性回归分析',
                'description': '建立湿度与PASI评分的线性回归模型，评估预测能力',
                'required_tool': 'statistical_analyzer',
                'worker_type': 'compute_worker',
                'dependencies': [3],
                'deadline': '3分钟',
                'parameters': {
                    'method': 'linear_regression',
                    'independent_var': 'humidity',
                    'dependent_var': 'pasi_score',
                    'compute_r_squared': True
                }
            },
            {
                'task_id': 7,
                'task_name': '生成散点图',
                'description': '绘制湿度与PASI评分的散点图，添加回归线和置信区间',
                'required_tool': 'visualization_engine',
                'worker_type': 'visualization_worker',
                'dependencies': [5, 6],
                'deadline': '2分钟',
                'parameters': {
                    'chart_type': 'scatter',
                    'x_axis': 'humidity',
                    'y_axis': 'pasi_score',
                    'add_regression_line': True,
                    'add_confidence_interval': True,
                    'title': '空气湿度与银屑病严重程度相关性'
                }
            },
            {
                'task_id': 8,
                'task_name': '生成趋势分析图',
                'description': '生成不同患者的湿度-PASI趋势图，展示个体变化模式',
                'required_tool': 'visualization_engine',
                'worker_type': 'visualization_worker',
                'dependencies': [3],
                'deadline': '2分钟',
                'parameters': {
                    'chart_type': 'line',
                    'group_by': 'patient_id',
                    'x_axis': 'humidity',
                    'y_axis': 'pasi_score',
                    'title': '各患者湿度-症状变化趋势'
                }
            },
            {
                'task_id': 9,
                'task_name': '生成分析报告',
                'description': '整合统计结果和图表，生成完整的相关性分析报告',
                'required_tool': 'visualization_engine',
                'worker_type': 'report_worker',
                'dependencies': [4, 5, 6, 7, 8],
                'deadline': '5分钟',
                'parameters': {
                    'report_format': 'html',
                    'include_sections': [
                        'executive_summary',
                        'descriptive_statistics',
                        'correlation_analysis',
                        'regression_analysis',
                        'visualizations',
                        'conclusions'
                    ],
                    'output_file': 'humidity_psoriasis_correlation_report.html'
                }
            }
        ]
    }
    
    # 6. 使用 WEL Generator 生成工作流
    print("正在生成 WEL 工作流文档...")
    print()
    
    generator = WELGenerator()
    wel_doc = await generator.generate_from_planner(
        requirement=requirement,
        experience=experience,
        knowledge=knowledge,
        tools=tools,
        tasks=tasks,
        intent_type=IntentType.DATA_ANALYSIS,
        intent_confidence=0.92
    )
    
    # 7. 输出 WEL 文档
    print("=" * 80)
    print("WEL 工作流文档 (JSON格式)")
    print("=" * 80)
    wel_json = wel_doc.to_json()
    print(json.dumps(json.loads(wel_json), ensure_ascii=False, indent=2))
    print()
    
    # 8. 输出关键度量指标
    print("=" * 80)
    print("工作流度量指标")
    print("=" * 80)
    print(f"场景名称: {wel_doc.scenario.name}")
    print(f"分析目标: {wel_doc.scenario.goal}")
    print(f"动作数量: {wel_doc.metrics.action_count}")
    print(f"预估时间: {wel_doc.metrics.estimated_duration} 秒 ({wel_doc.metrics.estimated_duration/60:.1f} 分钟)")
    print(f"依赖深度: {wel_doc.metrics.dependency_depth}")
    print(f"复杂度评分: {wel_doc.metrics.complexity_score:.3f}")
    print(f"置信度评分: {wel_doc.metrics.confidence_score:.3f}")
    print(f"风险等级: {wel_doc.metrics.risk_level.value}")
    print(f"并行潜力: {wel_doc.metrics.parallel_potential:.1%}")
    print()
    
    # 9. 输出数据流和依赖关系
    print("=" * 80)
    print("工作流动作详情")
    print("=" * 80)
    for i, action in enumerate(wel_doc.actions, 1):
        print(f"\n[动作 {i}] {action.action_name} ({action.action_id})")
        print(f"  类型: {action.action_type.value}")
        print(f"  目标: {action.target.entity}.{action.target.operation}")
        print(f"  依赖: {action.data_flow.get_dependencies() or '无'}")
        print(f"  输入端口: {len(action.data_flow.inputs)} 个")
        print(f"  输出端口: {len(action.data_flow.outputs)} 个")
        print(f"  置信度: {action.metadata.confidence_breakdown.overall_confidence:.3f}")
        print(f"  超时设置: {action.execution.timeout} 秒")
        print(f"  重试次数: {action.execution.retry_policy.max_retries}")
        print(f"  质量断言: {len(action.quality.assertions)} 个")
        
        # 显示参数
        if action.target.parameters:
            print(f"  参数:")
            for key, value in action.target.parameters.items():
                print(f"    - {key}: {value}")
    
    print()
    
    # 10. 输出依赖关系图（文本形式）
    print("=" * 80)
    print("任务依赖关系图")
    print("=" * 80)
    print()
    print("  [1] 加载患者观测数据")
    print("   |")
    print("   v")
    print("  [2] 数据验证")
    print("   |")
    print("   v")
    print("  [3] 数据清洗")
    print("   |")
    print("   +---> [4] 描述性统计分析")
    print("   |")
    print("   +---> [5] 相关性分析 ------+")
    print("   |                           |")
    print("   +---> [6] 线性回归分析 ----+---> [7] 生成散点图")
    print("   |                           |")
    print("   +---> [8] 生成趋势分析图   |")
    print("                               |")
    print("           [4], [5], [6], [7], [8]")
    print("                    |")
    print("                    v")
    print("           [9] 生成分析报告")
    print()
    
    # 11. 输出虚拟测试数据示例
    print("=" * 80)
    print("虚拟测试数据示例")
    print("=" * 80)
    test_data = generate_test_data()
    print(f"\n研究名称: {test_data['metadata']['study_name']}")
    print(f"观测期间: {test_data['metadata']['observation_period']}")
    print(f"患者数量: {test_data['metadata']['patient_count']}")
    print(f"观测记录数: {test_data['metadata']['observation_count']}")
    print(f"PASI评分范围: {test_data['metadata']['pasi_range']}")
    print(f"湿度范围: {test_data['metadata']['humidity_range']}")
    print()
    print("前10条观测记录:")
    print(f"{'患者ID':<12} {'日期':<12} {'湿度(%)':<10} {'PASI评分':<10}")
    print("-" * 50)
    for record in test_data['patients'][:10]:
        print(f"{record['patient_id']:<12} {record['date']:<12} {record['humidity']:<10} {record['pasi_score']:<10.1f}")
    print()
    
    # 12. 显示生命周期状态
    print("=" * 80)
    print("工作流生命周期状态")
    print("=" * 80)
    print(f"当前状态: {wel_doc.lifecycle.state.value}")
    print(f"\n状态历史:")
    for trans in wel_doc.lifecycle.state_history:
        print(f"  {trans.timestamp}: {trans.from_state.value} → {trans.to_state.value}")
        print(f"    原因: {trans.reason}")
    print()
    
    # 13. 模拟执行工作流（演示）
    print("=" * 80)
    print("模拟工作流执行")
    print("=" * 80)
    wel_doc.lifecycle.transition_to(State.EXECUTING, "开始执行相关性分析工作流")
    print(f"状态转换: {wel_doc.lifecycle.state.value}")
    print()
    
    # 模拟成功完成
    wel_doc.lifecycle.transition_to(State.COMPLETED, "工作流执行成功完成")
    print(f"最终状态: {wel_doc.lifecycle.state.value}")
    print()
    
    # 14. 输出 PDL 格式（向后兼容）
    print("=" * 80)
    print("PDL 格式 (向后兼容)")
    print("=" * 80)
    pdl_doc = wel_doc.to_pdl_document()
    print(json.dumps(pdl_doc, ensure_ascii=False, indent=2))
    print()
    
    # 15. 保存结果到文件
    output_dir = Path(__file__).parent / "output"
    output_dir.mkdir(exist_ok=True)
    
    # 保存 WEL JSON
    wel_output_file = output_dir / "humidity_psoriasis_wel.json"
    with open(wel_output_file, 'w', encoding='utf-8') as f:
        f.write(wel_json)
    print(f"WEL 文档已保存到: {wel_output_file}")
    
    # 保存测试数据
    data_output_file = output_dir / "humidity_psoriasis_test_data.json"
    with open(data_output_file, 'w', encoding='utf-8') as f:
        json.dump(test_data, f, ensure_ascii=False, indent=2)
    print(f"测试数据已保存到: {data_output_file}")
    
    # 保存 PDL 文档
    pdl_output_file = output_dir / "humidity_psoriasis_pdl.json"
    with open(pdl_output_file, 'w', encoding='utf-8') as f:
        json.dump(pdl_doc, f, ensure_ascii=False, indent=2)
    print(f"PDL 文档已保存到: {pdl_output_file}")
    
    print()
    print("=" * 80)
    print("测试完成！")
    print("=" * 80)
    print()
    print("总结:")
    print(f"✓ 成功生成了包含 {wel_doc.metrics.action_count} 个动作的 WEL 工作流")
    print(f"✓ 工作流置信度: {wel_doc.metrics.confidence_score:.1%}")
    print(f"✓ 预估执行时间: {wel_doc.metrics.estimated_duration/60:.1f} 分钟")
    print(f"✓ 风险等级: {wel_doc.metrics.risk_level.value}")
    print(f"✓ 生成了 {test_data['metadata']['observation_count']} 条虚拟测试数据")
    print(f"✓ 输出文件已保存到 {output_dir}")
    print()
    print("该工作流可用于:")
    print("  1. 加载和验证医疗观测数据")
    print("  2. 清洗和预处理数据")
    print("  3. 执行描述性统计分析")
    print("  4. 计算相关系数和显著性检验")
    print("  5. 建立回归模型")
    print("  6. 生成可视化图表")
    print("  7. 输出完整的分析报告")
    print()
    
    return wel_doc


# ==================== 主函数 ====================

def main():
    """主函数：运行测试"""
    try:
        asyncio.run(test_humidity_psoriasis_correlation())
    except Exception as e:
        print(f"测试执行出错: {e}")
        import traceback
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    exit(main())

