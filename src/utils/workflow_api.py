import requests
from typing import Optional, Dict, Any, List
from utils.config import WORKFLOW_API_BASE


class WorkflowAPIError(Exception):
    """自定义异常类，用于表示接口调用错误"""
    def __init__(self, code: int, msg: str, data: Any = None):
        self.code = code
        self.msg = msg
        self.data = data
        super().__init__(f"API Error [code: {code}]: {msg}")


class WorkflowAPIClient:
    """工作流接口调用客户端，封装所有atomic接口"""
    
    def __init__(self, base_url: str, timeout: int = 30):
        """
        初始化客户端
        :param base_url: 接口基础URL
                         完整接口路径会拼接为：base_url + /atomic/接口名
        :param timeout: 请求超时时间，默认30秒
        """
        self.base_url = base_url.rstrip("/")  # 确保base_url末尾无/
        self.timeout = timeout
        self.session = requests.Session()
        # 可根据需要添加通用请求头（如token、Content-Type等）
        self.session.headers.update({
            "Content-Type": "application/json; charset=utf-8"
        })

    def _request(self, api_path: str, json_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        内部通用请求方法，处理POST请求和响应解析
        :param api_path: 接口相对路径（如/create_template）
        :param json_data: 请求体JSON数据
        :return: 接口返回的data字段数据
        :raises WorkflowAPIError: 接口返回错误码时抛出
        """
        full_url = f"{self.base_url}/atomic{api_path}"
        try:
            # 发送POST请求
            response = self.session.post(
                url=full_url,
                json=json_data,
                timeout=self.timeout
            )
            response.raise_for_status()  # 捕获HTTP状态码错误（如404、500）
            
            # 解析响应JSON
            result = response.json()
            
            # 检查接口返回的业务码
            if result.get("code") != 0:
                raise WorkflowAPIError(
                    code=result.get("code", 1),
                    msg=result.get("msg", "未知错误"),
                    data=result.get("data")
                )
            
            return result.get("data", {})
        
        except requests.exceptions.RequestException as e:
            # 处理网络/连接异常
            raise WorkflowAPIError(
                code=-1,
                msg=f"请求失败: {str(e)}",
                data=None
            ) from e

    def create_workflow_template(self, user_id: int) -> Dict[str, Any]:
        """
        创建工作流模板
        :param user_id: 用户ID（整数）
        :return: 接口返回的data数据
        """
        return self._request(
            api_path="/create_template",
            json_data={"user_id": user_id}
        )

    def create_node(self, user_id: int, template_id: int, operator_id: int) -> Dict[str, Any]:
        """
        创建工作流节点
        :param user_id: 用户ID
        :param template_id: 模板ID
        :param operator_id: 算子ID
        :return: 接口返回的data数据
        """
        return self._request(
            api_path="/create_node",
            json_data={
                "user_id": user_id,
                "template_id": template_id,
                "operator_id": operator_id
            }
        )

    def create_node_dependency(self, user_id: int, source_id: int, target_id: int) -> Dict[str, Any]:
        """
        创建节点依赖关系
        :param user_id: 用户ID
        :param source_id: 源节点ID
        :param target_id: 目标节点ID
        :return: 接口返回的data数据
        """
        return self._request(
            api_path="/create_node_dependency",
            json_data={
                "user_id": user_id,
                "source_id": source_id,
                "target_id": target_id
            }
        )

    def set_template_inputs(self, user_id: int, template_id: int, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        设置模板输入参数
        :param user_id: 用户ID
        :param template_id: 模板ID
        :param inputs: 输入参数（字典格式）
        :return: 接口返回的data数据
        """
        return self._request(
            api_path="/set_template_inputs",
            json_data={
                "user_id": user_id,
                "template_id": template_id,
                "inputs": inputs
            }
        )

    def set_node_inputs(self, user_id: int, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        设置节点输入参数
        :param user_id: 用户ID
        :param inputs: 输入参数（字典格式）
        :return: 接口返回的data数据
        """
        return self._request(
            api_path="/set_node_inputs",
            json_data={
                "user_id": user_id,
                "inputs": inputs
            }
        )

    def run_workflow_template(
            self,
            user_id: int,
            template_id: int,
            name: Optional[str] = None,
            desc: Optional[str] = None,
            detailed_desc: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        运行工作流模板
        :param user_id: 用户ID
        :param template_id: 模板ID
        :param name: 运行实例名称（可选）
        :param desc: 简要描述（可选）
        :param detailed_desc: 详细描述（可选）
        :return: 接口返回的data数据
        """
        json_data = {
            "user_id": user_id,
            "template_id": template_id
        }
        # 仅添加非空的可选参数
        if name is not None:
            json_data["name"] = name
        if desc is not None:
            json_data["desc"] = desc
        if detailed_desc is not None:
            json_data["detailed_desc"] = detailed_desc
        
        return self._request(
            api_path="/run_template",
            json_data=json_data
        )

    def get_node_outputs(self, user_id: int, node_id: int) -> Dict[str, Any]:
        """
        获取节点输出
        :param user_id: 用户ID
        :param node_id: 节点ID
        :return: 接口返回的data数据
        """
        return self._request(
            api_path="/get_node_outputs",
            json_data={
                "user_id": user_id,
                "node_id": node_id
            }
        )

    def delete_nodes(self, user_id: int, node_ids: List[int]) -> Dict[str, Any]:
        """
        删除节点
        :param user_id: 用户ID
        :param node_ids: 节点ID列表
        :return: 接口返回的data数据
        """
        return self._request(
            api_path="/delete_nodes",
            json_data={
                "user_id": user_id,
                "node_ids": node_ids
            }
        )

    def polling_run_status(self, user_id: int, run_id: int) -> Dict[str, Any]:
        """
        轮询工作流运行状态
        :param user_id: 用户ID
        :param run_id: 运行实例ID
        :return: 接口返回的data数据
        """
        return self._request(
            api_path="/polling_run_status",
            json_data={
                "user_id": user_id,
                "run_id": run_id
            }
        )

    def get_latest_error_node_log(self, node_id: int) -> Optional[str]:
        """
        获取节点最新错误日志
        :param node_id: 节点ID
        :return: 错误信息（无则返回None）
        """
        data = self._request(
            api_path="/get_latest_error_node_log",
            json_data={"node_id": node_id}
        )
        return data.get("error_message")

    def get_latest_error_workflowrun_log(self, workflow_id: int) -> Optional[str]:
        """
        获取工作流运行最新错误日志
        :param workflow_id: 工作流ID
        :return: 错误信息（无则返回None）
        """
        data = self._request(
            api_path="/get_latest_error_workflowrun_log",
            json_data={"workflow_id": workflow_id}
        )
        return data.get("error_message")


# ------------------- 使用示例 -------------------
if __name__ == "__main__":
    # 初始化客户端（替换为你的实际接口地址）
    client = WorkflowAPIClient(base_url=WORKFLOW_API_BASE)

    try:
        # 1. 创建工作流模板
        template_data = client.create_workflow_template(user_id=1001)
        print(f"创建模板成功: {template_data}")
        template_id = template_data.get("template_id")  # 假设返回的data包含template_id

        # 2. 创建节点
        node_data = client.create_node(
            user_id=1001,
            template_id=template_id,
            operator_id=2001
        )
        print(f"创建节点成功: {node_data}")
        node_id = node_data.get("node_id")

        # 3. 其他接口调用示例
        # 设置模板输入
        client.set_template_inputs(
            user_id=1001,
            template_id=template_id,
            inputs={"param1": "value1", "param2": 123}
        )

        # 运行模板
        run_data = client.run_workflow_template(
            user_id=1001,
            template_id=template_id,
            name="测试运行",
            desc="测试工作流运行"
        )
        run_id = run_data.get("run_id")

        # 轮询运行状态
        status_data = client.polling_run_status(user_id=1001, run_id=run_id)
        print(f"运行状态: {status_data}")

    except WorkflowAPIError as e:
        print(f"接口调用失败: 错误码={e.code}, 错误信息={e.msg}")
    except Exception as e:
        print(f"未知错误: {str(e)}")