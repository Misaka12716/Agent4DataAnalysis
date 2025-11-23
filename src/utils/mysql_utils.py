# mysql_utils.py
# MySQL数据库操作封装，提供连接管理和基本CRUD功能

import pymysql  # pyright: ignore[reportMissingModuleSource]
from pymysql.cursors import DictCursor  # pyright: ignore[reportMissingModuleSource]
from typing import List, Dict, Optional, Tuple
from utils.config import (
    MYSQL_HOST,
    MYSQL_USER,
    MYSQL_PASSWORD,
    MYSQL_DB,
    MYSQL_PORT,
    MYSQL_CHARSET,
    TABLE_TOOLS_META_INFO,
    TABLE_TOOLS_TAGS,
)


class MySQLHandler:
    def __init__(
        self,
        host: str,
        user: str,
        password: str,
        db: str,
        port: int = 3306,
        charset: str = "utf8mb4",
    ):
        """
        初始化MySQL连接
        :param host: 数据库主机地址
        :param user: 数据库用户名
        :param password: 数据库密码
        :param db: 数据库名称
        :param port: 数据库端口（默认3306）
        :param charset: 字符编码（默认utf8mb4，支持emoji）
        """
        self.host = host
        self.user = user
        self.password = password
        self.db = db
        self.port = port
        self.charset = charset

        self.connection: Optional[pymysql.connections.Connection] = None
        self.cursor: Optional[pymysql.cursors.Cursor] = None
        self._connect()  # 初始化时建立连接

        # 检查并创建必要的表（tools）
        self._check_and_create_tables()

    def _connect(self) -> None:
        """建立数据库连接"""
        try:
            self.connection = pymysql.connect(
                host=self.host,
                user=self.user,
                password=self.password,
                database=self.db,
                port=self.port,
                charset=self.charset,
                cursorclass=DictCursor,  # 查询结果以字典形式返回
            )
            self.cursor = self.connection.cursor()
            print("数据库连接成功")
        except pymysql.MySQLError as e:
            print(f"数据库连接失败: {e}")
            raise  # 抛出异常让调用者处理

    def close(self) -> None:
        """关闭数据库连接"""
        if self.cursor:
            self.cursor.close()
        if self.connection:
            self.connection.close()
            print("数据库连接已关闭")

    def query(
        self, sql: str, params: Optional[Tuple] = None
    ) -> Tuple[List[Dict], Optional[str]]:
        """
        执行查询操作（SELECT）
        :param sql: 查询SQL语句（使用%s作为占位符）
        :param params: SQL参数（元组类型，用于替换占位符，防止SQL注入）
        :return: (查询结果列表, 错误信息) 若成功，错误信息为None
        """
        result = []
        error = None
        try:
            # 若连接已关闭，重新建立连接
            if not self.connection or self.connection._closed:
                self._connect()

            self.cursor.execute(sql, params or ())
            result = self.cursor.fetchall()  # 获取所有结果
        except pymysql.MySQLError as e:
            error = f"查询失败: {e}"
            print(error)
        return result, error

    def execute(
        self, sql: str, params: Optional[Tuple] = None, auto_commit: bool = True
    ) -> Tuple[int, Optional[str]]:
        """
        执行写操作（INSERT/UPDATE/DELETE）
        :param sql: 操作SQL语句（使用%s作为占位符）
        :param params: SQL参数（元组类型）
        :param auto_commit: 是否自动提交事务（默认True）
        :return: (受影响的行数, 错误信息) 若成功，错误信息为None
        """
        affected_rows = 0
        error = None
        try:
            if not self.connection or self.connection._closed:
                self._connect()

            affected_rows = self.cursor.execute(sql, params or ())
            if auto_commit:
                self.connection.commit()  # 自动提交
        except pymysql.MySQLError as e:
            if self.connection:
                self.connection.rollback()  # 出错时回滚
            error = f"执行失败: {e}"
            print(error)
        return affected_rows, error

    def insert(
        self, table: str, data: Dict[str, any], auto_commit: bool = True
    ) -> Tuple[int, Optional[int], Optional[str]]:
        """
        插入单条数据（简化INSERT操作）
        :param table: 表名
        :param data: 插入的数据（字典，key为字段名，value为值）
        :param auto_commit: 是否自动提交
        :return: (受影响行数, 插入的ID, 错误信息)
        """
        fields = ", ".join(data.keys())
        placeholders = ", ".join(["%s"] * len(data))
        sql = f"INSERT INTO {table} ({fields}) VALUES ({placeholders})"
        params = tuple(data.values())

        affected_rows, error = self.execute(sql, params, auto_commit)
        last_insert_id = self.cursor.lastrowid if not error else None
        return affected_rows, last_insert_id, error

    def _check_table_exists(self, table_name: str) -> bool:
        """检查指定表是否存在"""
        try:
            # 查询information_schema判断表是否存在
            sql = """
                SELECT COUNT(*) AS exist 
                FROM information_schema.tables 
                WHERE table_schema = %s AND table_name = %s
            """
            self.cursor.execute(sql, (self.db, table_name))
            result = self.cursor.fetchone()
            return result["exist"] > 0 if result else False
        except pymysql.MySQLError as e:
            print(f"检查表 {table_name} 存在性失败: {e}")
            return False

    def _check_and_create_tables(self) -> None:
        """检查并创建必要的表（tools和tool_tags）"""
        # 1. 创建tools表（工具池元信息表）
        if not self._check_table_exists(TABLE_TOOLS_META_INFO):
            create_tools_sql = f"""
            CREATE TABLE {TABLE_TOOLS_META_INFO} (
                id VARCHAR(64) PRIMARY KEY COMMENT '工具唯一ID',
                name VARCHAR(128) NOT NULL COMMENT '工具名称',
                brief_desc VARCHAR(30) NOT NULL COMMENT '简要描述（30字以内）',
                detailed_desc TEXT COMMENT '详细描述',
                input_params TEXT NOT NULL COMMENT '输入参数（JSON字符串）',
                output_params TEXT NOT NULL COMMENT '输出参数（JSON字符串）',
                api_path VARCHAR(256) NOT NULL COMMENT '工具接口路径',
                create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间'
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT '工具池元信息表';
            """
            affected, error = self.execute(create_tools_sql)
            if error:
                print(f"创建工具元信息表{TABLE_TOOLS_META_INFO}失败: {error}")
            else:
                print(f"工具元信息表{TABLE_TOOLS_META_INFO}创建成功")
        else:
            print(f"已存在工具元信息表{TABLE_TOOLS_META_INFO}")

        # # 2. 创建tool_tags表（工具-标签多对多索引表）
        # if not self._check_table_exists(TABLE_TOOLS_TAGS):
        #     create_tags_sql = f"""
        #     CREATE TABLE {TABLE_TOOLS_TAGS} (
        #         id INT AUTO_INCREMENT PRIMARY KEY,
        #         tool_id VARCHAR(64) NOT NULL COMMENT '工具ID（关联tools.id）',
        #         tag VARCHAR(64) NOT NULL COMMENT '标签内容',
        #         FOREIGN KEY (tool_id) REFERENCES {TABLE_TOOLS_META_INFO}(id) ON DELETE CASCADE,
        #         UNIQUE KEY uk_tool_tag (tool_id, tag) COMMENT '避免同一工具重复添加同一标签'
        #     ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT '工具-标签索引表';
        #     """
        #     affected, error = self.execute(create_tags_sql)
        #     if error:
        #         print(f"创建工具标签表{TABLE_TOOLS_TAGS}失败: {error}")
        #     else:
        #         print(f"工具标签表{TABLE_TOOLS_TAGS}创建成功")
        # else:
        #     print(f"已存在工具标签表{TABLE_TOOLS_TAGS}")


mysql_handler = MySQLHandler(
    MYSQL_HOST, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DB, MYSQL_PORT, MYSQL_CHARSET
)
