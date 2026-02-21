from utils.config import PATH
import yaml
import os
from typing import Dict, Any, Optional

# 各个prompt的模板对应路径
PROMPTS_PATH = "prompts"


class PromptBuilder:
    def __init__(self, lang: str = "zh"):
        """
        初始化：设置默认语言
        Args:
            lang: 语言类型（如"zh"或"en"，需与YAML中的键对应）
        """
        self.lang = lang
        self.allowed_roles = {"planner", "worker", "reporter"}

    def _load_yaml(self, yaml_path: str) -> Dict[str, Any]:
        """
        加载YAML文件并返回解析后的字典（处理绝对路径）
        Args:
            yaml_path: 相对路径（如"system_planner.yaml"）
        Returns:
            Dict[str, Any]: 解析后的YAML字典
        Raises:
            FileNotFoundError: 当文件不存在时
            yaml.YAMLError: 当YAML格式错误时
        """
        # 拼接绝对路径（结合项目根路径PATH和相对路径yaml_path）
        abs_path = os.path.join(PATH, yaml_path)
        # 检查文件是否存在
        if not os.path.exists(abs_path):
            raise FileNotFoundError(f"YAML文件不存在：{abs_path}")
        with open(abs_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    def _stringify_params(self, params: Dict[str, Any]) -> Dict[str, str]:
        """将所有参数转换为字符串类型（处理非字符串参数）"""
        stringified = {}
        for key, value in params.items():
            # 特殊处理None（转为空字符串，可根据需求调整）
            if value is None:
                stringified[key] = ""
            else:
                # 其他类型直接转为字符串
                stringified[key] = str(value)
        return stringified

    def build(self, yaml_data: Dict, **params: Any) -> Optional[str]:
        """
        生成最终prompt：替换占位符
        Args:
           yaml_data: 解析后的YAML字典数据
           ** params: 要替换的参数（如input_data="用户的需求文本"）
        Returns:
            替换后的prompt文本；若语言不存在则返回None
        """
        # 检查语言键是否存在于YAML数据中
        if self.lang not in yaml_data:
            print(f"Warning：YAML中未找到语言键 '{self.lang}'")
            return None

        # 获取原始文本（支持多行字符串）
        raw_prompt = yaml_data[self.lang]
        if not isinstance(raw_prompt, str):
            print(f"Warning：YAML中'{yaml_data["id"]}({self.lang})'对应的不是字符串")
            return None

        try:
            stringified_params = self._stringify_params(params)
            # 替换占位符
            return raw_prompt.format(**stringified_params)
        except KeyError as e:
            # 处理缺失的参数
            print(f"错误：缺少替换参数 {e}")
            print(f"raw_prompt: {raw_prompt}")
            print(f"params: {params}")
            return None

    def build_system_prompt(self, role: str, **params: Any) -> Optional[str]:
        """
        构建指定角色的系统提示词(system prompt)
        Args:
            role (str): 角色名称，必须是allowed_roles集合中的一个
                       当前允许的角色包括：planner, worker, reporter
        Returns:
            Optional[str]: 成功时返回构建好的系统提示词字符串，
                          如果角色不被允许或发生异常则返回None
        """

        # 验证角色是否在允许的范围内
        if role not in self.allowed_roles:
            print(f"错误：role必须是{self.allowed_roles}中的一个，实际传入：{role}")
            return None

        try:
            # 获取system_prompt对应的YAML路径
            # 路径格式：prompts/system/{role}.yaml
            yaml_path = os.path.join(PROMPTS_PATH, "system", f"{role}.yaml")

            # 加载YAML数据
            yaml_data = self._load_yaml(yaml_path)

            # 调用通用build方法生成prompt
            prompt = self.build(yaml_data, **params)
            return prompt
        except Exception as e:
            print(f"构建system_prompt({role})失败：{e}")
            return None

    def build_user_prompt(self, role: str, task: str, **params: Any) -> Optional[str]:
        """
        构建user_prompt
        Args:
            role: 角色类型（如"planner"）
            task: 任务类型（如"req_analysis"）
            **params: 要替换的参数（如input_data="用户的需求文本"）
        Returns:
            Optional[str]: 成功时返回构建好的用户提示词字符串，
                          如果角色或任务不被允许或发生异常则返回None
        """

        try:
            # 获取user_prompt对应的YAML路径
            yaml_path = os.path.join(PROMPTS_PATH, "user", role, f"{task}.yaml")
            # 加载YAML数据
            yaml_data = self._load_yaml(yaml_path)
            # 调用通用build方法生成prompt
            prompt = self.build(yaml_data, **params)
            return prompt
        except Exception as e:
            print(f"构建user_prompt({role},{task})失败：{e}")
            return None
