# -*- coding: utf-8 -*-
"""
===================================
股票自选列表仓库 (Supabase PostgreSQL)
===================================

职责：
1. 连接 Supabase PostgreSQL
2. stock_watchlist 表的 CRUD 操作
3. 向后兼容：未配置时回退到 .env STOCK_LIST
"""

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 建表 SQL（需手动在 Supabase Dashboard 执行）
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS stock_watchlist (
    id SERIAL PRIMARY KEY,
    stock_code VARCHAR(20) NOT NULL UNIQUE,
    stock_name VARCHAR(100),
    notes TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_stock_watchlist_code ON stock_watchlist(stock_code);
CREATE INDEX IF NOT EXISTS idx_stock_watchlist_active ON stock_watchlist(is_active);
"""


def _get_connection():
    """获取 PostgreSQL 连接（每次调用新建连接，保持简单）。"""
    try:
        import psycopg2
    except ImportError:
        logger.error("psycopg2 未安装，请运行: uv add psycopg2-binary")
        return None

    dsn = os.getenv("SUPABASE_DATABASE_URL", "")
    if not dsn:
        return None

    try:
        conn = psycopg2.connect(dsn, connect_timeout=10)
        return conn
    except Exception as e:
        logger.error(f"连接 Supabase 失败: {e}")
        return None


class StockListRepository:
    """股票自选列表仓库 — Supabase PostgreSQL 实现。"""

    @staticmethod
    def is_available() -> bool:
        """检查 Supabase 是否已配置且可连接。"""
        conn = _get_connection()
        if conn is None:
            return False
        try:
            conn.close()
            return True
        except Exception:
            return False

    @staticmethod
    def ensure_table() -> bool:
        """确保 stock_watchlist 表存在（自动建表）。"""
        conn = _get_connection()
        if conn is None:
            return False
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS stock_watchlist (
                            id SERIAL PRIMARY KEY,
                            stock_code VARCHAR(20) NOT NULL UNIQUE,
                            stock_name VARCHAR(100),
                            notes TEXT,
                            is_active BOOLEAN DEFAULT TRUE,
                            created_at TIMESTAMPTZ DEFAULT NOW(),
                            updated_at TIMESTAMPTZ DEFAULT NOW()
                        );
                    """)
            return True
        except Exception as e:
            logger.error(f"建表失败: {e}")
            return False
        finally:
            conn.close()

    @staticmethod
    def list_active() -> List[str]:
        """获取所有活跃股票代码列表（仅返回 code）。"""
        conn = _get_connection()
        if conn is None:
            return []
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT stock_code FROM stock_watchlist WHERE is_active = TRUE ORDER BY id"
                )
                return [row[0] for row in cur.fetchall()]
        except Exception as e:
            logger.error(f"查询自选股失败: {e}")
            return []
        finally:
            conn.close()

    @staticmethod
    def list_all() -> List[Dict[str, Any]]:
        """获取所有自选股详情（含非活跃）。"""
        conn = _get_connection()
        if conn is None:
            return []
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, stock_code, stock_name, notes, is_active, "
                    "created_at, updated_at FROM stock_watchlist ORDER BY id"
                )
                columns = [desc[0] for desc in cur.description]
                return [dict(zip(columns, row)) for row in cur.fetchall()]
        except Exception as e:
            logger.error(f"查询自选股失败: {e}")
            return []
        finally:
            conn.close()

    @staticmethod
    def add(stock_code: str, stock_name: Optional[str] = None, notes: Optional[str] = None) -> bool:
        """添加股票到自选列表。"""
        conn = _get_connection()
        if conn is None:
            return False
        try:
            code = stock_code.strip().upper()
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO stock_watchlist (stock_code, stock_name, notes)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (stock_code) DO UPDATE SET
                            stock_name = COALESCE(EXCLUDED.stock_name, stock_watchlist.stock_name),
                            notes = COALESCE(EXCLUDED.notes, stock_watchlist.notes),
                            is_active = TRUE,
                            updated_at = NOW()
                        """,
                        (code, stock_name, notes),
                    )
            logger.info(f"股票 {code} 已添加到自选")
            return True
        except Exception as e:
            logger.error(f"添加股票失败: {e}")
            return False
        finally:
            conn.close()

    @staticmethod
    def remove(stock_code: str) -> bool:
        """从自选列表删除股票（物理删除）。"""
        conn = _get_connection()
        if conn is None:
            return False
        try:
            code = stock_code.strip().upper()
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM stock_watchlist WHERE stock_code = %s",
                        (code,),
                    )
                    if cur.rowcount == 0:
                        logger.warning(f"股票 {code} 不在自选列表中")
                        return False
            logger.info(f"股票 {code} 已从自选删除")
            return True
        except Exception as e:
            logger.error(f"删除股票失败: {e}")
            return False
        finally:
            conn.close()

    @staticmethod
    def update(
        stock_code: str,
        stock_name: Optional[str] = None,
        notes: Optional[str] = None,
        is_active: Optional[bool] = None,
    ) -> bool:
        """更新自选股信息。"""
        conn = _get_connection()
        if conn is None:
            return False
        try:
            code = stock_code.strip().upper()
            updates = []
            params = []
            if stock_name is not None:
                updates.append("stock_name = %s")
                params.append(stock_name)
            if notes is not None:
                updates.append("notes = %s")
                params.append(notes)
            if is_active is not None:
                updates.append("is_active = %s")
                params.append(is_active)

            if not updates:
                return True  # 无需更新

            updates.append("updated_at = NOW()")
            params.append(code)

            sql = f"UPDATE stock_watchlist SET {', '.join(updates)} WHERE stock_code = %s"
            with conn:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
                    if cur.rowcount == 0:
                        logger.warning(f"股票 {code} 不在自选列表中")
                        return False
            logger.info(f"股票 {code} 信息已更新")
            return True
        except Exception as e:
            logger.error(f"更新股票失败: {e}")
            return False
        finally:
            conn.close()

    @staticmethod
    def get(stock_code: str) -> Optional[Dict[str, Any]]:
        """获取单只股票详情。"""
        conn = _get_connection()
        if conn is None:
            return None
        try:
            code = stock_code.strip().upper()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, stock_code, stock_name, notes, is_active, "
                    "created_at, updated_at FROM stock_watchlist WHERE stock_code = %s",
                    (code,),
                )
                row = cur.fetchone()
                if row is None:
                    return None
                columns = [desc[0] for desc in cur.description]
                return dict(zip(columns, row))
        except Exception as e:
            logger.error(f"查询股票失败: {e}")
            return None
        finally:
            conn.close()
