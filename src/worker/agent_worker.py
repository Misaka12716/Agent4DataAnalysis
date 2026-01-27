from .base_worker import BaseWorker
from .tools_matcher import OperatorMatcher
from utils.workflow_api import WorkflowAPIClient, WorkflowAPIError
from utils.config import WORKFLOW_API_BASE, PATH
from langgraph.graph import StateGraph, END
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional
import json
import os
from collections import defaultdict, deque


class AgentWorker(BaseWorker):
    """单智能体Worker：使用langgraph构建智能体，执行子任务（需对接工作流平台的工具）"""

    def __init__(self, operator_json_path: str = None, user_id: int = 1001, workflow_base_url: str = None):
        """
        初始化AgentWorker
        
        参数:
            operator_json_path: 算子信息JSON文件路径（默认使用 tools/tools_info.json）
            user_id: 用户ID（用于工作流API调用）
            workflow_base_url: 工作流API基础URL（默认使用配置中的值）
        """
        # 确定算子JSON文件路径
        if operator_json_path is None:
            operator_json_path = os.path.join(PATH, "tools", "tools_info.json")
        
        # 初始化算子匹配器
        self.operator_matcher = OperatorMatcher(operator_json_path)
        
        # 初始化工作流API客户端
        workflow_url = workflow_base_url or WORKFLOW_API_BASE
        self.workflow_client = WorkflowAPIClient(base_url=workflow_url)
        
        self.user_id = user_id
        
        # 构建langgraph智能体
        self.agent_graph = self._build_agent_graph()

    def _build_agent_graph(self) -> StateGraph:
        """
        构建langgraph智能体状态图
        
        流程：
        1. match_operators: 为每个任务匹配算子
        2. adjust_io_mapping: 调整输入输出映射关系
        3. execute_workflow: 根据依赖关系执行工作流
        """
        
        # 定义智能体状态
        class WorkerState(BaseModel):
            """Worker智能体状态"""
            tasks: List[Dict[str, Any]] = Field(description="原始子任务列表")
            matched_operators: Dict[int, Dict[str, Any]] = Field(
                default_factory=dict, 
                description="任务ID到匹配算子的映射 {task_id: {operator, match_reason}}"
            )
            io_mappings: Dict[int, Dict[str, Any]] = Field(
                default_factory=dict,
                description="任务ID到输入输出映射的字典 {task_id: {input_mapping, output_mapping}}"
            )
            workflow_results: Dict[int, Dict[str, Any]] = Field(
                default_factory=dict,
                description="任务ID到工作流执行结果的映射 {task_id: {node_id, run_id, status, outputs}}"
            )
            template_id: Optional[int] = Field(default=None, description="工作流模板ID")
            current_step: str = Field(default="match_operators", description="当前执行步骤")
            error_messages: List[str] = Field(default_factory=list, description="错误信息列表")
            execution_order: List[int] = Field(default_factory=list, description="任务执行顺序（拓扑排序）")

        # Step1: 匹配算子节点
        def match_operators(state: WorkerState) -> WorkerState:
            """为每个任务匹配合适的算子"""
            print(f"[Worker] Step1: 开始为 {len(state.tasks)} 个任务匹配算子")
            state.current_step = "match_operators"
            
            for task in state.tasks:
                task_id = task["task_id"]
                try:
                    # 使用BM25初筛Top10算子
                    top10_operators = self.operator_matcher.bm25_top10(task)
                    print(f"[Worker] 任务{task_id} BM25初筛得到 {len(top10_operators)} 个候选算子")
                    
                    if not top10_operators:
                        state.error_messages.append(f"任务{task_id}未找到候选算子")
                        continue
                    
                    # 使用LLM智能体进一步筛选
                    filtered_operators = self.operator_matcher.run_agent_filter(task, top10_operators)
                    print(f"[Worker] 任务{task_id} LLM筛选得到 {len(filtered_operators)} 个匹配算子")
                    
                    if filtered_operators:
                        # 选择第一个匹配的算子（也可以选择匹配度最高的）
                        best_match = filtered_operators[0]
                        state.matched_operators[task_id] = {
                            "operator": best_match["operator"],
                            "match_reason": best_match.get("match_reason", ""),
                        }
                        print(f"[Worker] 任务{task_id} 选择算子: {best_match['operator']['name']}")
                    else:
                        state.error_messages.append(f"任务{task_id} LLM筛选后无匹配算子")
                        
                except Exception as e:
                    error_msg = f"任务{task_id}匹配算子失败: {str(e)}"
                    state.error_messages.append(error_msg)
                    print(f"[Worker] {error_msg}")
            
            return state

        # Step2: 调整输入输出映射节点
        def adjust_io_mapping(state: WorkerState) -> WorkerState:
            """根据工具信息和任务信息调整输入输出映射关系"""
            print(f"[Worker] Step2: 开始调整输入输出映射关系")
            state.current_step = "adjust_io_mapping"
            
            # 构建任务ID到任务的映射
            task_map = {task["task_id"]: task for task in state.tasks}
            
            for task_id, match_info in state.matched_operators.items():
                if task_id not in task_map:
                    continue
                
                task = task_map[task_id]
                operator = match_info["operator"]
                
                try:
                    # 获取算子的输入输出参数定义
                    operator_input_params = operator.get("input_params", [])
                    operator_output_params = operator.get("output_params", [])
                    
                    # 获取任务的输入输出描述
                    task_inputs = task.get("input", [])
                    task_outputs = task.get("output", [])
                    
                    # 构建输入映射：将任务的input描述映射到算子的input_params
                    input_mapping = {}
                    for idx, task_input_desc in enumerate(task_inputs):
                        # 尝试匹配到对应的算子输入参数
                        # 这里使用简单的索引映射，实际可以使用更智能的匹配逻辑
                        if idx < len(operator_input_params):
                            param_name = operator_input_params[idx]["name"]
                            input_mapping[param_name] = {
                                "task_input_index": idx,
                                "task_input_desc": task_input_desc,
                                "param_type": operator_input_params[idx].get("type", ""),
                                "param_description": operator_input_params[idx].get("description", ""),
                            }
                    
                    # 构建输出映射：将算子的output_params映射到任务的output描述
                    output_mapping = {}
                    for idx, operator_output in enumerate(operator_output_params):
                        output_name = operator_output["name"]
                        output_mapping[output_name] = {
                            "operator_output_index": idx,
                            "task_output_index": idx if idx < len(task_outputs) else None,
                            "task_output_desc": task_outputs[idx] if idx < len(task_outputs) else "",
                            "output_type": operator_output.get("type", ""),
                            "output_description": operator_output.get("description", ""),
                        }
                    
                    state.io_mappings[task_id] = {
                        "input_mapping": input_mapping,
                        "output_mapping": output_mapping,
                    }
                    print(f"[Worker] 任务{task_id} 输入输出映射调整完成")
                    
                except Exception as e:
                    error_msg = f"任务{task_id}调整输入输出映射失败: {str(e)}"
                    state.error_messages.append(error_msg)
                    print(f"[Worker] {error_msg}")
            
            return state

        # Step3: 执行工作流节点
        def execute_workflow(state: WorkerState) -> WorkerState:
            """根据依赖关系调用工作流API执行任务"""
            print(f"[Worker] Step3: 开始执行工作流")
            state.current_step = "execute_workflow"
            
            try:
                # 计算任务执行顺序（拓扑排序）
                execution_order = AgentWorker._topological_sort(state.tasks)
                state.execution_order = execution_order
                print(f"[Worker] 任务执行顺序: {execution_order}")
                
                # 创建工作流模板
                template_data = self.workflow_client.create_workflow_template(user_id=self.user_id)
                template_id = template_data.get("template_id")
                if not template_id:
                    raise ValueError("创建工作流模板失败：未返回template_id")
                state.template_id = template_id
                print(f"[Worker] 创建工作流模板成功，template_id={template_id}")
                
                # 为每个任务创建节点
                task_to_node_id = {}  # 任务ID到节点ID的映射
                for task in state.tasks:
                    task_id = task["task_id"]
                    if task_id not in state.matched_operators:
                        state.error_messages.append(f"任务{task_id}未匹配到算子，跳过创建节点")
                        continue
                    
                    operator = state.matched_operators[task_id]["operator"]
                    operator_id = operator.get("id") or operator.get("operator_id")
                    if not operator_id:
                        state.error_messages.append(f"任务{task_id}的算子缺少ID")
                        continue
                    
                    # 创建节点
                    node_data = self.workflow_client.create_node(
                        user_id=self.user_id,
                        template_id=template_id,
                        operator_id=operator_id
                    )
                    node_id = node_data.get("node_id")
                    if not node_id:
                        raise ValueError(f"任务{task_id}创建节点失败：未返回node_id")
                    
                    task_to_node_id[task_id] = node_id
                    print(f"[Worker] 任务{task_id} 创建节点成功，node_id={node_id}")
                    
                    # 设置节点输入参数
                    if task_id in state.io_mappings:
                        input_mapping = state.io_mappings[task_id]["input_mapping"]
                        # 构建节点输入参数字典
                        node_inputs = {}
                        for param_name, mapping_info in input_mapping.items():
                            # 检查输入是否来自上游任务
                            task_input_desc = mapping_info["task_input_desc"]
                            # 尝试从上游任务获取输出
                            upstream_value = AgentWorker._resolve_upstream_output(
                                task_input_desc, task, state.tasks, task_to_node_id, state.workflow_results
                            )
                            if upstream_value is not None:
                                node_inputs[param_name] = upstream_value
                            else:
                                # 如果没有上游依赖，使用原始描述（可能需要用户提供）
                                node_inputs[param_name] = task_input_desc
                        
                        if node_inputs:
                            self.workflow_client.set_node_inputs(
                                user_id=self.user_id,
                                inputs={
                                    "node_id": node_id,
                                    **node_inputs
                                }
                            )
                            print(f"[Worker] 任务{task_id} 设置节点输入参数: {node_inputs}")
                
                # 创建节点依赖关系
                for task in state.tasks:
                    task_id = task["task_id"]
                    if task_id not in task_to_node_id:
                        continue
                    
                    dependencies = task.get("dependencies", [])
                    for dep_task_id in dependencies:
                        if dep_task_id in task_to_node_id:
                            source_node_id = task_to_node_id[dep_task_id]
                            target_node_id = task_to_node_id[task_id]
                            self.workflow_client.create_node_dependency(
                                user_id=self.user_id,
                                source_id=source_node_id,
                                target_id=target_node_id
                            )
                            print(f"[Worker] 创建依赖关系: 任务{dep_task_id}(节点{source_node_id}) -> 任务{task_id}(节点{target_node_id})")
                
                # 运行工作流模板
                run_data = self.workflow_client.run_workflow_template(
                    user_id=self.user_id,
                    template_id=template_id,
                    name=f"AgentWorker执行任务",
                    desc="由AgentWorker自动生成的工作流"
                )
                run_id = run_data.get("run_id")
                if not run_id:
                    raise ValueError("运行工作流模板失败：未返回run_id")
                print(f"[Worker] 工作流运行成功，run_id={run_id}")
                
                # 轮询运行状态直到完成
                import time
                max_polling_attempts = 100
                polling_interval = 2  # 秒
                status = "unknown"
                
                for attempt in range(max_polling_attempts):
                    time.sleep(polling_interval)
                    
                    status_data = self.workflow_client.polling_run_status(
                        user_id=self.user_id,
                        run_id=run_id
                    )
                    status = status_data.get("status", "unknown")
                    print(f"[Worker] 工作流运行状态 (尝试{attempt+1}/{max_polling_attempts}): {status}")
                    
                    if status in ["completed", "success", "finished"]:
                        # 获取所有节点的输出
                        for task_id, node_id in task_to_node_id.items():
                            try:
                                outputs_data = self.workflow_client.get_node_outputs(
                                    user_id=self.user_id,
                                    node_id=node_id
                                )
                                state.workflow_results[task_id] = {
                                    "node_id": node_id,
                                    "run_id": run_id,
                                    "status": status,
                                    "outputs": outputs_data,
                                }
                            except Exception as e:
                                print(f"[Worker] 获取任务{task_id}节点输出失败: {str(e)}")
                                state.workflow_results[task_id] = {
                                    "node_id": node_id,
                                    "run_id": run_id,
                                    "status": status,
                                    "outputs": None,
                                    "error": str(e),
                                }
                        break
                    elif status in ["failed", "error"]:
                        # 获取错误日志
                        error_log = self.workflow_client.get_latest_error_workflowrun_log(workflow_id=run_id)
                        raise ValueError(f"工作流执行失败: {error_log or '未知错误'}")
                
                if status not in ["completed", "success", "finished"]:
                    state.error_messages.append(f"工作流执行超时，最终状态: {status}")
                
            except WorkflowAPIError as e:
                error_msg = f"工作流API调用失败: {e.msg} (code: {e.code})"
                state.error_messages.append(error_msg)
                print(f"[Worker] {error_msg}")
            except Exception as e:
                error_msg = f"执行工作流失败: {str(e)}"
                state.error_messages.append(error_msg)
                print(f"[Worker] {error_msg}")
            
            return state


        # 构建状态图
        graph_builder = StateGraph(WorkerState)
        
        # 添加节点
        graph_builder.add_node("match_operators", match_operators)
        graph_builder.add_node("adjust_io_mapping", adjust_io_mapping)
        graph_builder.add_node("execute_workflow", execute_workflow)
        
        # 设置入口点
        graph_builder.set_entry_point("match_operators")
        
        # 添加边
        graph_builder.add_edge("match_operators", "adjust_io_mapping")
        graph_builder.add_edge("adjust_io_mapping", "execute_workflow")
        graph_builder.add_edge("execute_workflow", END)
        
        # 编译状态图
        graph = graph_builder.compile()
        return graph

    def execute_task(
        self, 
        tasks: List[Dict[str, Any]], 
        knowledge: Dict[str, Any] = None, 
        tools_metadata: List[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        执行分配的子任务列表
        
        参数:
            tasks: 子任务列表，每个任务包含 task_id, task_name, description, dependencies, worker_type, input, output
            knowledge: 知识信息（可选）
            tools_metadata: 工具元数据列表（可选）
        
        返回:
            执行结果字典，包含任务执行状态和结果
        """
        print(f"[Worker] 开始执行 {len(tasks)} 个子任务")
        
        # 初始化状态
        initial_state = {
            "tasks": tasks,
            "matched_operators": {},
            "io_mappings": {},
            "workflow_results": {},
            "template_id": None,
            "current_step": "match_operators",
            "error_messages": [],
            "execution_order": [],
        }
        
        try:
            # 运行智能体
            final_state = self.agent_graph.invoke(initial_state)
            
            # 构建返回结果
            result = {
                "success": len(final_state["error_messages"]) == 0,
                "template_id": final_state["template_id"],
                "execution_order": final_state["execution_order"],
                "matched_operators": {
                    task_id: {
                        "operator_name": info["operator"].get("name", ""),
                        "match_reason": info.get("match_reason", ""),
                    }
                    for task_id, info in final_state["matched_operators"].items()
                },
                "workflow_results": final_state["workflow_results"],
                "error_messages": final_state["error_messages"],
            }
            
            print(f"[Worker] 任务执行完成，成功: {result['success']}")
            return result
            
        except Exception as e:
            error_msg = f"执行任务失败: {str(e)}"
            print(f"[Worker] {error_msg}")
            return {
                "success": False,
                "error": error_msg,
                "error_messages": [error_msg],
            }

    @staticmethod
    def _topological_sort(tasks: List[Dict[str, Any]]) -> List[int]:
        """对任务进行拓扑排序，返回执行顺序"""
        # 构建依赖图
        graph = defaultdict(list)
        in_degree = defaultdict(int)
        task_ids = {task["task_id"] for task in tasks}
        
        for task in tasks:
            task_id = task["task_id"]
            in_degree[task_id] = len(task.get("dependencies", []))
            for dep_id in task.get("dependencies", []):
                if dep_id in task_ids:
                    graph[dep_id].append(task_id)
        
        # 拓扑排序
        queue = deque([task_id for task_id in task_ids if in_degree[task_id] == 0])
        result = []
        
        while queue:
            current = queue.popleft()
            result.append(current)
            
            for neighbor in graph[current]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)
        
        # 检查是否有循环依赖
        if len(result) != len(task_ids):
            raise ValueError("任务依赖关系存在循环，无法进行拓扑排序")
        
        return result

    @staticmethod
    def _resolve_upstream_output(
        task_input_desc: str,
        current_task: Dict[str, Any],
        all_tasks: List[Dict[str, Any]],
        task_to_node_id: Dict[int, int],
        workflow_results: Dict[int, Dict[str, Any]]
    ) -> Any:
        """解析任务的输入描述，从上游任务获取实际输出值"""
        # 尝试从输入描述中提取上游任务ID和输出索引
        # 例如："任务2的output[0]：填充后的用户特征数据框"
        import re
        
        # 匹配模式：任务X的output[Y]
        pattern = r'任务(\d+)的output\[(\d+)\]'
        match = re.search(pattern, task_input_desc)
        
        if match:
            upstream_task_id = int(match.group(1))
            output_index = int(match.group(2))
            
            # 检查上游任务是否已执行
            if upstream_task_id in workflow_results:
                outputs = workflow_results[upstream_task_id].get("outputs", {})
                # 根据输出结构获取对应索引的输出
                if isinstance(outputs, dict):
                    # 尝试从outputs中获取对应索引的输出
                    output_keys = list(outputs.keys())
                    if output_index < len(output_keys):
                        return outputs[output_keys[output_index]]
                    elif "output" in outputs:
                        # 如果outputs有output字段，尝试获取
                        output_list = outputs.get("output", [])
                        if isinstance(output_list, list) and output_index < len(output_list):
                            return output_list[output_index]
        
        return None
