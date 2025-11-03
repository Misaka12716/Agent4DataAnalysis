# utils/neo4j_utils.py

from neo4j import GraphDatabase, exceptions
from typing import Dict, List, Optional, Any, Tuple
from utils.config import NEO4J_HOST, NEO4J_USER, NEO4J_PASSWORD, NEO4J_DB, NEO4J_PORT
import json
import uuid


class Neo4jHandler:
    """Neo4j图数据库操作封装类，提供连接管理和常用CRUD操作接口"""

    def __init__(self, uri: str, user: str, password: str, database: str = "neo4j"):
        """
        初始化Neo4j连接

        :param uri: 数据库连接URI，如"bolt://localhost:7687"
        :param user: 用户名
        :param password: 密码
        :param database: 数据库名称，默认为"neo4j"
        """
        self.uri = uri
        self.user = user
        self.password = password
        self.database = database
        self.driver = None
        self._connect()

    def _connect(self) -> None:
        """建立数据库连接"""
        try:
            self.driver = GraphDatabase.driver(
                self.uri, auth=(self.user, self.password)
            )
            # 测试连接
            self.test_connection()
            print("成功连接到Neo4j数据库")
        except exceptions.Neo4jError as e:
            print(f"连接Neo4j数据库失败: {e}")
            self.driver = None

    def test_connection(self) -> bool:
        """
        测试数据库连接是否正常

        :return: 连接正常返回True，否则返回False
        """
        if not self.driver:
            return False

        try:
            with self.driver.session(database=self.database) as session:
                session.run("MATCH (n) RETURN count(n) AS count")
            return True
        except exceptions.Neo4jError:
            return False

    def close(self) -> None:
        """关闭数据库连接"""
        if self.driver:
            self.driver.close()
            print("已关闭Neo4j数据库连接")

    def execute_query(
        self, query: str, parameters: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """
        执行Cypher查询，返回结果

        :param query: Cypher查询语句
        :param parameters: 查询参数，字典形式
        :return: 查询结果列表，每个元素是一个字典表示一条记录
        """
        if not self.driver:
            raise exceptions.Neo4jError("数据库连接未建立")

        try:
            with self.driver.session(database=self.database) as session:
                result = session.run(query, parameters or {})
                return [record.data() for record in result]
        except exceptions.Neo4jError as e:
            print(f"执行查询失败: {e}")
            raise

    def execute_command(
        self, command: str, parameters: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        执行Cypher命令（如创建、更新、删除），不返回结果

        :param command: Cypher命令语句
        :param parameters: 命令参数，字典形式
        """
        if not self.driver:
            raise exceptions.Neo4jError("数据库连接未建立")

        try:
            with self.driver.session(database=self.database) as session:
                session.run(command, parameters or {})
        except exceptions.Neo4jError as e:
            print(f"执行命令失败: {e}")
            raise

    def create_node(self, label: str, properties: Dict[str, Any]) -> None:
        """
        创建一个节点

        :param label: 节点标签
        :param properties: 节点属性字典
        """
        cypher = f"CREATE (n:{label} $properties)"
        self.execute_command(cypher, {"properties": properties})

    def get_node_by_id(self, node_id: str) -> Optional[Dict[str, Any]]:
        """
        根据节点ID查询节点

        :param node_id: 节点ID
        :return: 节点信息字典，包含 elementId、labels和properties，不存在则返回None
        """
        cypher = """
        MATCH (n)
        WHERE  elementId(n) = $node_id
        RETURN  elementId(n) AS id, labels(n) AS labels, properties(n) AS properties
        """
        result = self.execute_query(cypher, {"node_id": node_id})
        return result[0] if result else None

    def get_nodes_by_label(self, label: str, limit: int = 100) -> List[Dict[str, Any]]:
        """
        根据标签查询节点

        :param label: 节点标签
        :param limit: 限制返回数量，默认100
        :return: 节点列表
        """
        cypher = f"""
        MATCH (n:{label})
        RETURN  elementId(n) AS id, labels(n) AS labels, properties(n) AS properties
        LIMIT $limit
        """
        return self.execute_query(cypher, {"limit": limit})

    def get_nodes_by_property(
        self, label: str, property_key: str, property_value: Any
    ) -> List[Dict[str, Any]]:
        """
        根据属性查询节点

        :param label: 节点标签
        :param property_key: 属性键
        :param property_value: 属性值
        :return: 节点列表
        """
        cypher = f"""
        MATCH (n:{label})
        WHERE n.{property_key} = $property_value
        RETURN  elementId(n) AS id, labels(n) AS labels, properties(n) AS properties
        """
        return self.execute_query(cypher, {"property_value": property_value})

    def update_node_properties(self, node_id: str, properties: Dict[str, Any]) -> None:
        """
        更新节点属性

        :param node_id: 节点ID
        :param properties: 要更新的属性字典
        """
        cypher = """
        MATCH (n)
        WHERE  elementId(n) = $node_id
        SET n += $properties
        """
        self.execute_command(cypher, {"node_id": node_id, "properties": properties})

    def delete_node(self, node_id: str) -> None:
        """
        删除节点（会同时删除与之相连的关系）

        :param node_id: 节点ID
        """
        cypher = """
        MATCH (n)
        WHERE  elementId(n) = $node_id
        DETACH DELETE n
        """
        self.execute_command(cypher, {"node_id": node_id})

    def create_relationship(
        self,
        start_node_id: str,
        end_node_id: str,
        relationship_type: str,
        properties: Optional[Dict[str, Any]] = None,
    ) -> None:
        """创建两个节点之间的关系（修复参数传递错误）"""
        # 确保properties默认为空字典，避免None值
        properties = properties or {}

        cypher = f"""
        MATCH (a), (b)
        WHERE  elementId(a) = $start_id AND  elementId(b) = $end_id
        CREATE (a)-[r:{relationship_type}]->(b)
        SET r += $properties
        """

        # 正确传递properties参数（键为"properties"，值为属性字典）
        params = {
            "start_id": start_node_id,
            "end_id": end_node_id,
            "properties": properties,
        }

        self.execute_command(cypher, params)

    def get_relationships_by_node(self, node_id: str) -> List[Dict[str, Any]]:
        """
        查询节点的所有关系

        :param node_id: 节点ID
        :return: 关系列表
        """
        cypher = """
        MATCH (n)-[r]-(m)
        WHERE  elementId(n) = $node_id
        RETURN 
             elementId(r) AS rel_id,
            type(r) AS rel_type,
            properties(r) AS rel_properties,
             elementId(n) AS start_id,
             elementId(m) AS end_id
        """
        return self.execute_query(cypher, {"node_id": node_id})

    def update_relationship_properties(
        self, relationship_id: str, properties: Dict[str, Any]
    ) -> None:
        """
        更新关系属性

        :param relationship_id: 关系ID
        :param properties: 要更新的属性字典
        """
        cypher = """
        MATCH ()-[r]-()
        WHERE  elementId(r) = $rel_id
        SET r += $properties
        """
        self.execute_command(
            cypher, {"rel_id": relationship_id, "properties": properties}
        )

    def delete_relationship(self, relationship_id: str) -> None:
        """
        删除关系

        :param relationship_id: 关系ID
        """
        cypher = """
        MATCH ()-[r]-()
        WHERE  elementId(r) = $rel_id
        DELETE r
        """
        self.execute_command(cypher, {"rel_id": relationship_id})

    def batch_create_nodes(self, nodes: List[Tuple[str, Dict[str, Any]]]) -> None:
        """
        批量创建节点

        :param nodes: 节点列表，每个元素是一个元组 (标签, 属性字典)
        """
        if not nodes:
            return

        cypher = []
        params = {}
        for i, (label, props) in enumerate(nodes):
            # 为每个节点变量添加唯一索引（如n0、n1、n2...），避免重复声明
            node_var = f"n{i}"
            # 属性参数名（保持唯一）
            param_key = f"props_{i}"
            # 拼接CREATE语句（变量唯一，标签静态写入，属性用参数）
            cypher.append(f"CREATE ({node_var}:{label} ${param_key})")
            # 存储属性参数
            params[param_key] = props

        # 合并所有CREATE语句为一个Cypher命令
        full_cypher = " ".join(cypher)
        self.execute_command(full_cypher, params)

    def run_transaction(self, func, *args, **kwargs) -> Any:
        """
        执行事务

        :param func: 事务函数，接收tx作为第一个参数
        :param args: 传递给事务函数的位置参数
        :param kwargs: 传递给事务函数的关键字参数
        :return: 事务函数的返回值
        """
        if not self.driver:
            raise exceptions.Neo4jError("数据库连接未建立")

        try:
            with self.driver.session(database=self.database) as session:
                return session.execute_write(func, *args, **kwargs)
        except exceptions.Neo4jError as e:
            print(f"事务执行失败: {e}")
            raise

    def __enter__(self):
        """支持上下文管理器"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器退出时关闭连接"""
        self.close()


# 测试用例优化
if __name__ == "__main__":
    # 生成唯一标识，避免测试数据冲突
    test_uuid = str(uuid.uuid4())[:8]
    print(f"=== 开始Neo4jHandler测试 (测试ID: {test_uuid}) ===")

    # 初始化连接
    neo4j_handler = None
    try:
        # 初始化连接
        neo4j_handler = Neo4jHandler(
            uri=f"bolt://{NEO4J_HOST}:{NEO4J_PORT}",
            user=NEO4J_USER,
            password=NEO4J_PASSWORD,
            database=NEO4J_DB,
        )
        assert neo4j_handler.test_connection(), "数据库连接测试失败"
        print("✅ 数据库连接成功")

        # 1. 单节点操作测试
        print("\n=== 单节点操作测试 ===")
        # 创建节点
        test_node_props = {
            "name": f"TestUser_{test_uuid}",
            "age": 28,
            "city": "Beijing",
        }
        neo4j_handler.create_node("Person", test_node_props)
        print("✅ 节点创建成功")

        # 查询节点（按属性）
        nodes = neo4j_handler.get_nodes_by_property(
            "Person", "name", test_node_props["name"]
        )
        assert len(nodes) == 1, "节点查询失败"
        node_id = nodes[0]["id"]
        print(f"✅ 节点查询成功，节点ID: {node_id}")

        # 更新节点属性
        update_props = {"age": 29, "email": f"test_{test_uuid}@example.com"}
        neo4j_handler.update_node_properties(node_id, update_props)
        updated_node = neo4j_handler.get_node_by_id(node_id)
        assert updated_node["properties"]["age"] == 29, "节点属性更新失败"
        assert (
            updated_node["properties"]["email"] == f"test_{test_uuid}@example.com"
        ), "节点属性更新失败"
        print("✅ 节点属性更新成功")

        # 2. 关系操作测试
        print("\n=== 关系操作测试 ===")
        # 创建第二个节点
        node2_props = {"name": f"TestFriend_{test_uuid}", "age": 30}
        neo4j_handler.create_node("Person", node2_props)
        node2 = neo4j_handler.get_nodes_by_property(
            "Person", "name", node2_props["name"]
        )[0]
        node2_id = node2["id"]

        # 创建关系
        rel_props = {"since": 2023, "type": "friend"}
        neo4j_handler.create_relationship(node_id, node2_id, "KNOWS", rel_props)
        print("✅ 关系创建成功")

        # 查询关系
        relationships = neo4j_handler.get_relationships_by_node(node_id)
        assert len(relationships) == 1, "关系查询失败"
        rel_id = relationships[0]["rel_id"]
        print(f"✅ 关系查询成功，关系ID: {rel_id}")

        # 更新关系属性
        update_rel_props = {"since": 2024, "strength": 8}
        neo4j_handler.update_relationship_properties(rel_id, update_rel_props)
        updated_rels = neo4j_handler.get_relationships_by_node(node_id)
        assert updated_rels[0]["rel_properties"]["since"] == 2024, "关系属性更新失败"
        print("✅ 关系属性更新成功")

        # 删除关系
        neo4j_handler.delete_relationship(rel_id)
        remaining_rels = neo4j_handler.get_relationships_by_node(node_id)
        assert len(remaining_rels) == 0, "关系删除失败"
        print("✅ 关系删除成功")

        # 3. 批量操作测试
        print("\n=== 批量操作测试 ===")
        batch_nodes = [
            ("Person", {"name": f"BatchUser1_{test_uuid}", "age": 25}),
            ("Person", {"name": f"BatchUser2_{test_uuid}", "age": 26}),
            ("Person", {"name": f"BatchUser3_{test_uuid}", "age": 27}),
        ]
        neo4j_handler.batch_create_nodes(batch_nodes)
        batch_result = neo4j_handler.get_nodes_by_label("Person")
        batch_count = sum(
            1 for n in batch_result if n["properties"]["name"].startswith(f"BatchUser")
        )
        assert batch_count == 3, f"批量创建节点失败，创建了{batch_count}/3个节点"
        print("✅ 批量创建节点成功")

        # 4. 事务测试
        print("\n=== 事务操作测试 ===")

        def tx_func(tx, name1, name2):
            # 创建节点并建立关系
            tx.run("CREATE (a:Person {name: $name1})", name1=name1)
            tx.run("CREATE (b:Person {name: $name2})", name2=name2)
            tx.run(
                """
                MATCH (a:Person {name: $name1}), (b:Person {name: $name2})
                CREATE (a)-[r:COLLEAGUE]->(b)
            """,
                name1=name1,
                name2=name2,
            )
            return True

        tx_result = neo4j_handler.run_transaction(
            tx_func, f"TxUser1_{test_uuid}", f"TxUser2_{test_uuid}"
        )
        assert tx_result, "事务执行失败"
        tx_node1 = neo4j_handler.get_nodes_by_property(
            "Person", "name", f"TxUser1_{test_uuid}"
        )
        assert len(tx_node1) == 1, "事务创建节点失败"
        print("✅ 事务执行成功")

        # 5. 删除操作测试
        print("\n=== 删除操作测试 ===")
        # 删除节点
        neo4j_handler.delete_node(node_id)
        deleted_node = neo4j_handler.get_node_by_id(node_id)
        assert deleted_node is None, "节点删除失败"
        print("✅ 节点删除成功")

        # 清理测试数据（删除所有测试节点）
        cleanup_query = """
        MATCH (n:Person)
        WHERE n.name CONTAINS $test_uuid
        DETACH DELETE n
        """
        neo4j_handler.execute_command(cleanup_query, {"test_uuid": test_uuid})
        print("✅ 测试数据清理完成")

        print("\n=== 所有测试用例执行完成，全部通过！ ===")

    except AssertionError as ae:
        print(f"\n❌ 测试失败: {ae}")
    except exceptions.Neo4jError as ne:
        print(f"\n❌ Neo4j错误: {ne}")
    except Exception as e:
        print(f"\n❌ 未知错误: {e}")
    finally:
        # 关闭连接
        if neo4j_handler:
            neo4j_handler.close()
        print("\n=== 测试结束 ===")
