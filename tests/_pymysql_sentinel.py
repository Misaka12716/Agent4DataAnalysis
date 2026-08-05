"""在 pymysql.connect 被 mock 之前保存真实 connect，供功能集成测恢复。"""

from __future__ import annotations

import pymysql

REAL_PYMYSQL_CONNECT = pymysql.connect
