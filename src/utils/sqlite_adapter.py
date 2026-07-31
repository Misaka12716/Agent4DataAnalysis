# sqlite_adapter.py - SQLite fallback for MySQLHandler
import sqlite3, os, re
from typing import Any, Dict, List, Optional, Tuple

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJ = os.path.dirname(os.path.dirname(_THIS_DIR))
DB_PATH = os.path.join(_PROJ, "workspace", "agent_platform.db")

class SQLiteHandler:
    def __init__(self, host="", user="", password="", db="", port=0, charset=""):
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        self.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=30000")
        self.conn.execute("PRAGMA foreign_keys=ON")

    def _translate_sql(self, sql):
        s = sql
        s = re.sub(r"%s", "?", s)
        # SHOW COLUMNS FROM `table` [LIKE ?] -> PRAGMA table_info(table)
        m = re.match(
            r"^\s*SHOW\s+COLUMNS\s+FROM\s+[`\"]?(\w+)[`\"]?(?:\s+LIKE\s+\?)?\s*$",
            s,
            flags=re.IGNORECASE,
        )
        if m:
            return f"PRAGMA table_info({m.group(1)})"
        # Convert MySQL BIGINT/INT AUTO_INCREMENT -> INTEGER PRIMARY KEY AUTOINCREMENT
        s = re.sub(r"(BIGINT|INT)\s+AUTO_INCREMENT\s+PRIMARY\s+KEY", "INTEGER PRIMARY KEY AUTOINCREMENT", s, flags=re.IGNORECASE)
        s = re.sub(r"\s*AUTO_INCREMENT\s*", " AUTOINCREMENT ", s)
        s = s.replace("AUTOINCREMENT", "AUTOINCREMENT")  # no-op, ensure consistency
        # Remove MySQL-specific table options
        s = re.sub(r"\s*ENGINE\s*=\s*\w+", "", s, flags=re.IGNORECASE)
        s = re.sub(r"\s*DEFAULT CHARSET\s*=\s*\w+", "", s, flags=re.IGNORECASE)
        s = re.sub(r"\s*CHARSET\s*=\s*\w+", "", s, flags=re.IGNORECASE)
        s = re.sub(r"\s*CHARACTER SET\s+\w+", "", s, flags=re.IGNORECASE)
        s = re.sub(r"\s*COLLATE\s+\w+", "", s, flags=re.IGNORECASE)
        # Remove COMMENT clauses
        s = re.sub(r"\s+COMMENT\s+'[^']*'", "", s)
        s = re.sub(r'\s+COMMENT\s+"[^"]*"', "", s)
        # Convert backticks to double quotes
        s = s.replace("`", '"')
        # Type conversions
        s = s.replace("TINYINT", "INTEGER")
        s = s.replace("tinyint", "INTEGER")
        s = s.replace("BIGINT", "INTEGER")
        s = s.replace("bigint", "INTEGER")
        s = s.replace("DOUBLE", "REAL")
        s = s.replace("double", "REAL")
        s = s.replace("JSON", "TEXT")
        s = s.replace("json", "TEXT")
        # Convert MySQL UNIQUE KEY to UNIQUE for SQLite
        s = re.sub(r"UNIQUE\s+KEY\s+\w+\s*\(", "UNIQUE (", s, flags=re.IGNORECASE)
        # Remove inline INDEX clauses (SQLite does not support inline INDEX)
        s = re.sub(r",?\s*\n\s*INDEX\s+\w+\s*\([^)]+\)", "", s, flags=re.IGNORECASE)
        # Remove ON UPDATE CURRENT_TIMESTAMP (SQLite doesn't support)
        s = re.sub(r"\s+ON UPDATE CURRENT_TIMESTAMP", "", s, flags=re.IGNORECASE)
        # Remove trailing commas before closing parens (common MySQL->SQLite issue)
        s = re.sub(r",\s*\)", ")", s)
        return s

    def query(self, sql, params=None):
        try:
            was_show_columns = bool(
                re.match(r"^\s*SHOW\s+COLUMNS\s+FROM\s+", sql or "", flags=re.IGNORECASE)
            )
            like_filter = None
            if was_show_columns and params:
                like_filter = params[0]
                params = ()
            sql = self._translate_sql(sql)
            cur = self.conn.execute(sql, params or ())
            rows = [dict(row) for row in cur.fetchall()]
            if was_show_columns:
                # PRAGMA table_info -> MySQL SHOW COLUMNS shape (Field)
                mapped = []
                for r in rows:
                    name = r.get("name")
                    if like_filter is not None and str(name) != str(like_filter):
                        continue
                    mapped.append({"Field": name, "Type": r.get("type"), "name": name})
                return mapped, None
            return rows, None
        except Exception as e:
            return [], str(e)

    def get_table_columns(self, table_name: str):
        """与 MySQLHandler.get_table_columns 对齐。"""
        if not table_name or not re.match(r"^[A-Za-z0-9_]+$", table_name):
            return set()
        rows, err = self.query(f"SHOW COLUMNS FROM `{table_name}`")
        if err or not rows:
            return set()
        cols = set()
        for r in rows:
            name = r.get("Field") or r.get("name")
            if name:
                cols.add(str(name))
        return cols

    def execute(self, sql, params=None, auto_commit=True):
        try:
            sql = self._translate_sql(sql)
            cur = self.conn.execute(sql, params or ())
            if auto_commit:
                self.conn.commit()
            return cur.rowcount, None
        except Exception as e:
            return 0, str(e)

    def insert(self, table, data, auto_commit=True):
        fields = ", ".join(data.keys())
        placeholders = ", ".join(["?"] * len(data))
        sql = f"INSERT INTO {table} ({fields}) VALUES ({placeholders})"
        params = tuple(data.values())
        try:
            cur = self.conn.execute(sql, params)
            if auto_commit:
                self.conn.commit()
            return cur.rowcount, cur.lastrowid, None
        except Exception as e:
            return 0, None, str(e)

    def _check_table_exists(self, table_name):
        try:
            cur = self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table_name,)
            )
            return cur.fetchone() is not None
        except Exception:
            return False

    def close(self):
        if self.conn:
            self.conn.close()

sqlite_handler = SQLiteHandler()
