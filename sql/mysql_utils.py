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
