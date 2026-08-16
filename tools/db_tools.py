import os
import re

from dotenv import load_dotenv
from langchain_core.tools import tool
from mysql.connector import Error, connect

from api.monitor import monitor

load_dotenv()


def get_db_config():
    config = {
        "host": os.getenv("MYSQL_HOST", "localhost"),
        "port": int(os.getenv("MYSQL_PORT", "3306")),
        "user": os.getenv("MYSQL_USER"),
        "password": os.getenv("MYSQL_PASSWORD"),
        "database": os.getenv("MYSQL_DATABASE"),
        "charset": os.getenv("MYSQL_CHARSET", "utf8mb4"),
        "collation": os.getenv("MYSQL_COLLATION", "utf8mb4_unicode_ci"),
        "autocommit": True,
        "sql_mode": os.getenv("MYSQL_SQL_MODE", "TRADITIONAL"),
    }
    config = {key: value for key, value in config.items() if value is not None}
    missing_keys = [key for key in ("user", "password", "database") if key not in config]
    if missing_keys:
        raise ValueError(f"缺失数据库核心配置：{', '.join(missing_keys)}")
    return config


@tool
def list_sql_tables() -> str:
    """查询当前库中所有可用的表。"""
    monitor.report_tool("数据库表名查询工具：list_sql_tables", {})
    try:
        with connect(**get_db_config()) as conn:
            with conn.cursor() as cursor:
                cursor.execute("show tables")
                tables = cursor.fetchall()
                if not tables:
                    return "没有可用的表"
                return f"可用的表有：{', '.join(table[0] for table in tables)}"
    except Error as exc:
        return f"查询出现异常：{exc}"


@tool
def get_table_data(table_name: str) -> str:
    """读取指定表的前 100 行用于结构预览。"""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table_name or ""):
        raise ValueError("Invalid table name")
    monitor.report_tool("数据库表数据查询工具：get_table_data", {"table_name": table_name})
    try:
        with connect(**get_db_config()) as conn:
            with conn.cursor() as cursor:
                cursor.execute(f"select * from `{table_name}` limit 100")
                description = cursor.description
                if not description:
                    return f"数据表：{table_name}为空没有数据！"
                columns = [desc[0] for desc in description]
                rows = cursor.fetchmany(100)
                return ",".join(columns) + "\n" + "\n".join(
                    ",".join(map(str, row)) for row in rows
                )
    except Error as exc:
        return f"查询出现异常：{exc}"


def validate_sql_query(query: str) -> str:
    if not isinstance(query, str) or not query.strip():
        raise ValueError("SQL query must not be empty")
    normalized = query.strip()
    if len(normalized.encode("utf-8")) > 32 * 1024:
        raise ValueError("SQL query is too large")
    statement = normalized[:-1].rstrip() if normalized.endswith(";") else normalized
    if ";" in statement:
        raise ValueError("Multiple SQL statements are not allowed")
    if re.search(r"(^|\s)(drop|truncate|alter|grant|revoke|create)\b", statement, re.I):
        raise ValueError("DDL and permission-changing SQL are not allowed")
    if re.search(r"(^|\s)(insert|update|delete|replace)\b", statement, re.I):
        raise ValueError("DML SQL requires a dedicated write tool")
    if not re.match(r"^(select|with)\b", statement, re.I):
        raise ValueError("Only SELECT or WITH queries are allowed")
    return statement


@tool
def execute_sql_query(query: str) -> str:
    """执行经过校验的只读自定义 SQL 查询。"""
    query = validate_sql_query(query)
    monitor.report_tool("数据库表数据查询工具：execute_sql_query", {"query": query})
    try:
        with connect(**get_db_config()) as conn:
            with conn.cursor() as cursor:
                cursor.execute(query)
                description = cursor.description
                if not description:
                    return "执行自定义 SQL 查询没有返回结果。"
                columns = [desc[0] for desc in description]
                rows = cursor.fetchmany(100)
                return ",".join(columns) + "\n" + "\n".join(
                    ",".join(map(str, row)) for row in rows
                )
    except Error as exc:
        return f"查询出现异常：{exc}"
