# mysql_utils.py
# MySQL数据库操作封装，提供连接管理和基本CRUD功能

import threading

import pymysql  # pyright: ignore[reportMissingModuleSource]
from pymysql.cursors import DictCursor  # pyright: ignore[reportMissingModuleSource]
from typing import List, Dict, Optional, Tuple
from configs.config import (
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
        self._lock = threading.Lock()
        self._connect()  # 初始化时建立连接

    def _reset_connection(self) -> None:
        if self.cursor:
            try:
                self.cursor.close()
            except Exception:
                pass
        if self.connection:
            try:
                self.connection.close()
            except Exception:
                pass
        self.cursor = None
        self.connection = None

    def _connect(self) -> None:
        """建立数据库连接"""
        try:
            self._reset_connection()
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
        with self._lock:
            self._reset_connection()
            print("数据库连接已关闭")

    def _ensure_connection(self) -> None:
        if not self.connection or self.connection._closed:
            self._connect()

    def _query_once(
        self, sql: str, params: Optional[Tuple] = None
    ) -> Tuple[List[Dict], Optional[str]]:
        self._ensure_connection()
        self.cursor.execute(sql, params or ())
        return self.cursor.fetchall(), None

    def query(
        self, sql: str, params: Optional[Tuple] = None
    ) -> Tuple[List[Dict], Optional[str]]:
        """
        执行查询操作（SELECT）
        :param sql: 查询SQL语句（使用%s作为占位符）
        :param params: SQL参数（元组类型，用于替换占位符，防止SQL注入）
        :return: (查询结果列表, 错误信息) 若成功，错误信息为None
        """
        with self._lock:
            try:
                return self._query_once(sql, params)
            except pymysql.MySQLError:
                try:
                    self._connect()
                    return self._query_once(sql, params)
                except pymysql.MySQLError as retry_err:
                    error = f"查询失败: {retry_err}"
                    print(error)
                    return [], error

    def _execute_once(
        self, sql: str, params: Optional[Tuple] = None, auto_commit: bool = True
    ) -> Tuple[int, Optional[str]]:
        self._ensure_connection()
        affected_rows = self.cursor.execute(sql, params or ())
        if auto_commit:
            self.connection.commit()
        return affected_rows, None

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
        with self._lock:
            try:
                return self._execute_once(sql, params, auto_commit)
            except pymysql.MySQLError:
                if self.connection:
                    try:
                        self.connection.rollback()
                    except Exception:
                        pass
                try:
                    self._connect()
                    return self._execute_once(sql, params, auto_commit)
                except pymysql.MySQLError as retry_err:
                    if self.connection:
                        try:
                            self.connection.rollback()
                        except Exception:
                            pass
                    error = f"执行失败: {retry_err}"
                    print(error)
                    return 0, error

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

        with self._lock:
            try:
                affected_rows, _ = self._execute_once(sql, params, auto_commit)
            except pymysql.MySQLError:
                try:
                    self._connect()
                    affected_rows, _ = self._execute_once(sql, params, auto_commit)
                except pymysql.MySQLError as retry_err:
                    error = f"执行失败: {retry_err}"
                    print(error)
                    return 0, None, error
            last_insert_id = self.cursor.lastrowid
            return affected_rows, last_insert_id, None

    def _check_table_exists_unlocked(self, table_name: str) -> bool:
        sql = """
            SELECT COUNT(*) AS exist
            FROM information_schema.tables
            WHERE table_schema = %s AND table_name = %s
        """
        self._ensure_connection()
        self.cursor.execute(sql, (self.db, table_name))
        result = self.cursor.fetchone()
        return result["exist"] > 0 if result else False

    def _check_table_exists(self, table_name: str) -> bool:
        """检查指定表是否存在"""
        with self._lock:
            try:
                return self._check_table_exists_unlocked(table_name)
            except pymysql.MySQLError:
                try:
                    self._connect()
                    return self._check_table_exists_unlocked(table_name)
                except pymysql.MySQLError as retry_err:
                    print(f"检查表 {table_name} 存在性失败: {retry_err}")
                    return False

try:
    mysql_handler = MySQLHandler(
        MYSQL_HOST, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DB, MYSQL_PORT, MYSQL_CHARSET
    )
    print('MySQL connected')
except Exception as _e:
    print(f'MySQL failed ({_e}), using SQLite')
    try:
        from utils.sqlite_adapter import sqlite_handler as _fb
        mysql_handler = _fb
        print('SQLite fallback enabled')
    except Exception as _e2:
        print(f'SQLite also failed: {_e2}')
        raise RuntimeError('No database') from _e
