import json
import jieba
import numpy as np
from rank_bm25 import BM25Okapi
from typing import List, Dict, Any
from langgraph.graph import StateGraph, END
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from pydantic import BaseModel, Field
from langchain_openai import ChatOpenAI
from dotenv import load_dotenv
import os

from utils.config import OPENAI_COMPATIBLE_API_BASE, API_KEY


class OperatorMatcher:
    def __init__(self, operator_json_path: str):
        """
        初始化算子匹配器
        :param operator_json_path: 算子信息JSON文件路径
        """
        # 加载算子数据
        self.operators = self._load_operators(operator_json_path)
        # 预处理算子文本，构建BM25语料库
        self.corpus, self.operator_text_map = self._preprocess_corpus()
        # 初始化BM25模型
        self.bm25 = BM25Okapi(self.corpus)

    def _load_operators(self, json_path: str) -> List[Dict]:
        """加载JSON文件中的算子数据"""
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list):
                raise ValueError("算子JSON文件必须是列表格式")
            return data
        except Exception as e:
            raise RuntimeError(f"加载算子文件失败：{str(e)}")

    def _preprocess_corpus(self) -> tuple[List[List[str]], Dict[int, Dict]]:
        """
        预处理算子文本：拼接关键信息并分词，构建语料库
        :return: (分词后的语料库, 文本索引到算子信息的映射)
        """
        corpus = []
        operator_text_map = {}
        for idx, operator in enumerate(self.operators):
            # 拼接算子核心文本（name+brief_desc+detailed_desc+输入输出参数）
            operator_text = f"{operator['name']} {operator['brief_desc']} {operator['detailed_desc']} "
            # 拼接输入参数
            for input_param in operator.get("input_params", []):
                operator_text += f"{input_param['name']} {input_param['type']} {input_param['description']} "
            # 拼接输出参数
            for output_param in operator.get("output_params", []):
                operator_text += f"{output_param['name']} {output_param['type']} {output_param['description']} "
            # 中文分词（去停用词可根据需求添加）
            tokenized_text = jieba.lcut(operator_text)
            corpus.append(tokenized_text)
            operator_text_map[idx] = operator
        return corpus, operator_text_map

    def bm25_top10(self, task: Dict[str, Any]) -> List[Dict]:
        """
        基于BM25算法，根据任务描述筛选Top30匹配的算子
        :param task: 任务描述字典（符合你给定的任务格式）
        :return: Top30匹配的算子列表（按相似度降序）
        """
        # 拼接任务核心文本
        task_text = f"{task['task_name']} {task['description']} {task['worker_type']} "
        # 拼接输入输出描述
        for input_item in task.get("input", []):
            task_text += f"{input_item} "
        for output_item in task.get("output", []):
            task_text += f"{output_item} "
        # 任务文本分词
        tokenized_task = jieba.lcut(task_text)
        # 计算相似度得分
        scores = self.bm25.get_scores(tokenized_task)
        # 按得分降序取Top30的索引
        top30_indices = np.argsort(scores)[::-1][:10]
        # 映射回算子信息
        top10_operators = [
            self.operator_text_map[idx] for idx in top30_indices if scores[idx] > 0
        ]
        # 确保至少返回非空列表（若无匹配则返回空）
        return top10_operators if top10_operators else []

    def build_agent(self, llm_model: str = "qwen2.5:7b") -> StateGraph:
        """
        构建langgraph智能体，用于精细化筛选算子
        :param llm_model: LLM模型名称（支持OpenAI系列，可替换为通义千问等）
        :return: 构建好的langgraph状态图
        """

        # 定义智能体状态（存储任务信息、待筛选算子、已筛选结果、当前处理索引）
        class AgentState(BaseModel):
            task: Dict[str, Any] = Field(description="任务描述信息")
            candidate_operators: List[Dict] = Field(description="待筛选的算子列表")
            filtered_operators: List[Dict] = Field(
                default=[], description="筛选通过的算子"
            )
            current_index: int = Field(default=0, description="当前处理的算子索引")

        # 初始化LLM
        llm = ChatOpenAI(
            model=llm_model,
            temperature=0,
            base_url=OPENAI_COMPATIBLE_API_BASE,
            api_key=API_KEY,
        )

        # 定义算子评估节点
        def evaluate_operator(state: AgentState) -> AgentState:
            """
            评估单个算子是否匹配任务需求
            :param state: 智能体状态
            :return: 更新后的状态
            """
            # 获取当前待评估的算子
            current_idx = state.current_index
            if current_idx >= len(state.candidate_operators):
                return state

            operator = state.candidate_operators[current_idx]
            task = state.task

            # 构建评估提示词（精准匹配任务的各个维度）
            prompt = PromptTemplate(
                template="""
                请你作为算子匹配专家，评估以下算子是否适配给定的任务，仅输出JSON格式结果（无需其他文字）。
                评估维度：
                1. 算子功能是否匹配任务核心动作（task_name+description）；
                2. 算子输入参数是否匹配任务输入（input）的内容和格式；
                3. 算子输出参数是否匹配任务输出（output）的内容和格式；
                4. 算子类型是否匹配任务的worker_type（数据/文本/逻辑/图表）。

                任务信息：
                - 任务名称：{task_name}
                - 任务描述：{task_description}
                - 任务类型：{worker_type}
                - 任务输入：{task_input}
                - 任务输出：{task_output}

                算子信息：
                - 算子名称：{op_name}
                - 算子简介：{op_brief}
                - 算子详细描述：{op_detailed}
                - 算子输入参数：{op_input}
                - 算子输出参数：{op_output}

                输出格式要求（JSON）：
                {{
                    "is_match": true/false,  // 仅布尔值，是否匹配
                    "reason": "简要说明匹配/不匹配的原因（≤100字）"
                }}
                """,
                input_variables=[
                    "task_name",
                    "task_description",
                    "worker_type",
                    "task_input",
                    "task_output",
                    "op_name",
                    "op_brief",
                    "op_detailed",
                    "op_input",
                    "op_output",
                ],
            )

            # 格式化提示词参数
            prompt_params = {
                "task_name": task["task_name"],
                "task_description": task["description"],
                "worker_type": task["worker_type"],
                "task_input": json.dumps(task["input"], ensure_ascii=False),
                "task_output": json.dumps(task["output"], ensure_ascii=False),
                "op_name": operator["name"],
                "op_brief": operator["brief_desc"],
                "op_detailed": operator["detailed_desc"],
                "op_input": json.dumps(
                    operator.get("input_params", []), ensure_ascii=False
                ),
                "op_output": json.dumps(
                    operator.get("output_params", []), ensure_ascii=False
                ),
            }

            # 调用LLM并解析结果
            chain = prompt | llm | JsonOutputParser()
            try:
                result = chain.invoke(prompt_params)
                # 如果匹配，加入筛选结果
                if result.get("is_match", False):
                    state.filtered_operators.append(
                        {"operator": operator, "match_reason": result["reason"]}
                    )
            except Exception as e:
                print(f"评估算子{operator['name']}失败：{str(e)}")

            # 推进到下一个算子
            state.current_index += 1
            return state

        # 定义终止判断节点
        def should_continue(state: AgentState) -> str:
            """判断是否继续筛选：未处理完则继续，否则终止"""
            if state.current_index < len(state.candidate_operators):
                return "evaluate_operator"
            return END

        # 构建langgraph状态图
        graph_builder = StateGraph(AgentState)
        # 添加节点
        graph_builder.add_node("evaluate_operator", evaluate_operator)
        # 设置起始节点
        graph_builder.set_entry_point("evaluate_operator")
        # 添加条件边：评估后判断是否继续
        graph_builder.add_conditional_edges(
            "evaluate_operator",
            should_continue,
            {"evaluate_operator": "evaluate_operator", END: END},
        )

        # 编译状态图
        graph = graph_builder.compile()
        return graph

    def run_agent_filter(
        self, task: Dict[str, Any], top30_operators: List[Dict]
    ) -> List[Dict]:
        """
        运行智能体，筛选Top30算子
        :param task: 任务描述字典
        :param top30_operators: BM25初筛的Top30算子
        :return: 最终筛选通过的算子列表（含匹配原因）
        """
        if not top30_operators:
            return []
        # 构建智能体
        graph = self.build_agent()
        # 初始化状态
        initial_state = {
            "task": task,
            "candidate_operators": top30_operators,
            "filtered_operators": [],
            "current_index": 0,
        }
        # 运行智能体
        final_state = graph.invoke(initial_state)
        return final_state["filtered_operators"]

# ------------------------------ 多测试用例批量测试 ------------------------------
if __name__ == "__main__":
    # 1. 初始化匹配器（替换为你的算子JSON文件路径）
    matcher = OperatorMatcher("tools/tools_info.json")

    # 2. 定义多个测试用例
    test_cases = [
        {
            "task_id": 1,
            "task_name": "机器学习建模预测",
            "description": "使用机器学习算法对结构化数据进行建模，实现分类或回归预测",
            "dependencies": [],
            "worker_type": "数据",
            "input": ["结构化特征数据集", "标签列名称"],
            "output": ["训练好的模型文件", "预测结果报告"]
        },
        {
            "task_id": 2,
            "task_name": "特征标准化",
            "description": "对数值型特征做Z-score标准化，消除量纲影响，输出标准化后的特征数据框",
            "dependencies": [1],
            "worker_type": "数据",
            "input": ["填充后的用户特征数据框"],
            "output": ["Z-score标准化后的用户特征数据框"]
        },
        {
            "task_id": 3,
            "task_name": "中文文本分词",
            "description": "对中文文档进行分词处理，去除停用词，输出分词后的词列表",
            "dependencies": [],
            "worker_type": "文本",
            "input": ["原始中文文档字符串"],
            "output": ["分词后的词列表", "停用词过滤后的词频统计"]
        },
        {
            "task_id": 4,
            "task_name": "逻辑规则校验",
            "description": "根据预设的业务逻辑规则，校验输入数据是否符合合规要求",
            "dependencies": [2],
            "worker_type": "逻辑",
            "input": ["标准化后的特征数据", "业务规则配置文件"],
            "output": ["合规校验结果", "异常数据明细"]
        }
    ]

    # 3. 批量执行每个测试用例
    for case_idx, test_task in enumerate(test_cases, 1):
        print("=" * 80)
        print(f"正在执行测试用例 {case_idx}：{test_task['task_name']}")
        print("=" * 80)

        # 3.1 BM25初筛Top10算子
        top10_ops = matcher.bm25_top10(test_task)
        print(f"\n【BM25初筛结果】 匹配到算子数量：{len(top10_ops)}")
        for idx, op in enumerate(top10_ops):
            print(f"  {idx+1}. 算子名称：{op['name']} | 简介：{op['brief_desc']}")

        # 3.2 智能体精细化筛选
        filtered_ops = matcher.run_agent_filter(test_task, top10_ops)
        print(f"\n【智能体筛选结果】 最终匹配算子数量：{len(filtered_ops)}")
        for idx, op in enumerate(filtered_ops):
            print(f"  {idx+1}. 算子名称：{op['operator']['name']}")
            print(f"     匹配原因：{op['match_reason']}")
        
        # 用空行分隔不同用例
        print("\n" * 2)