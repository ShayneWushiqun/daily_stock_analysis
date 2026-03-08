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
        """确保 stock_watchlist 表存在（自动建表及迁移）。"""
        conn = _get_connection()
        if conn is None:
            return False
        try:
            with conn:
                with conn.cursor() as cur:
                    # 1. 基础建表 (兼容旧结构)
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS stock_watchlist (
                            id SERIAL PRIMARY KEY,
                            stock_code VARCHAR(20) NOT NULL,
                            stock_name VARCHAR(100),
                            notes TEXT,
                            is_active BOOLEAN DEFAULT TRUE,
                            created_at TIMESTAMPTZ DEFAULT NOW(),
                            updated_at TIMESTAMPTZ DEFAULT NOW()
                        );
                    """)
                    
                    # 2. 添加 user_id 列
                    cur.execute("""
                        ALTER TABLE stock_watchlist 
                        ADD COLUMN IF NOT EXISTS user_id VARCHAR(100) DEFAULT 'admin';
                    """)
                    
                    # 3. 索引
                    cur.execute("CREATE INDEX IF NOT EXISTS idx_stock_watchlist_active ON stock_watchlist(is_active);")
                    cur.execute("CREATE INDEX IF NOT EXISTS idx_stock_watchlist_user ON stock_watchlist(user_id);")

                    # 4. 迁移唯一约束: 从 stock_code -> (user_id, stock_code)
                    # 检查旧约束是否存在
                    cur.execute("""
                        SELECT constraint_name 
                        FROM information_schema.table_constraints 
                        WHERE table_name = 'stock_watchlist' 
                        AND constraint_type = 'UNIQUE'
                        AND constraint_name = 'stock_watchlist_stock_code_key';
                    """)
                    if cur.fetchone():
                        logger.info("正在迁移 stock_watchlist 唯一约束...")
                        # 确保现有数据 user_id 不为空 (默认为 'admin')
                        cur.execute("UPDATE stock_watchlist SET user_id = 'admin' WHERE user_id IS NULL;")
                        # 删除旧约束
                        cur.execute("ALTER TABLE stock_watchlist DROP CONSTRAINT stock_watchlist_stock_code_key;")
                    
                    # 检查并添加新约束
                    cur.execute("""
                        SELECT 1 FROM pg_constraint WHERE conname = 'unique_user_stock'
                    """)
                    if not cur.fetchone():
                        # 如果没有数据重复，则添加约束
                        # 先清理可能的重复数据 (保留最新的) - 简单起见，这里假设数据已清理或依靠用户处理
                        try:
                            cur.execute("""
                                ALTER TABLE stock_watchlist 
                                ADD CONSTRAINT unique_user_stock UNIQUE (user_id, stock_code);
                            """)
                        except Exception as e:
                            logger.warning(f"添加唯一约束失败 (可能存在重复数据): {e}")

            return True
        except Exception as e:
            logger.error(f"建表/迁移失败: {e}")
            return False
        finally:
            conn.close()

    @staticmethod
    def list_active(user_id: Optional[str] = None) -> List[str]:
        """获取活跃股票代码列表。如果不指定 user_id，返回所有用户的去重股票代码。"""
        conn = _get_connection()
        if conn is None:
            return []
        try:
            with conn.cursor() as cur:
                if user_id:
                    cur.execute(
                        "SELECT stock_code FROM stock_watchlist WHERE is_active = TRUE AND user_id = %s ORDER BY id",
                        (user_id,)
                    )
                else:
                    cur.execute(
                        "SELECT DISTINCT stock_code FROM stock_watchlist WHERE is_active = TRUE"
                    )
                return [row[0] for row in cur.fetchall()]
        except Exception as e:
            logger.error(f"查询自选股失败: {e}")
            return []
        finally:
            conn.close()

    @staticmethod
    def list_all(user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取自选股详情。"""
        conn = _get_connection()
        if conn is None:
            return []
        try:
            with conn.cursor() as cur:
                sql = "SELECT id, stock_code, stock_name, notes, is_active, user_id, created_at, updated_at FROM stock_watchlist"
                params = []
                if user_id:
                    sql += " WHERE user_id = %s"
                    params.append(user_id)
                sql += " ORDER BY id"
                
                cur.execute(sql, tuple(params))
                columns = [desc[0] for desc in cur.description]
                return [dict(zip(columns, row)) for row in cur.fetchall()]
        except Exception as e:
            logger.error(f"查询自选股失败: {e}")
            return []
        finally:
            conn.close()

    @staticmethod
    def add(stock_code: str, stock_name: Optional[str] = None, notes: Optional[str] = None, user_id: str = 'admin') -> bool:
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
                        INSERT INTO stock_watchlist (stock_code, stock_name, notes, user_id)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (user_id, stock_code) DO UPDATE SET
                            stock_name = COALESCE(EXCLUDED.stock_name, stock_watchlist.stock_name),
                            notes = COALESCE(EXCLUDED.notes, stock_watchlist.notes),
                            is_active = TRUE,
                            updated_at = NOW()
                        """,
                        (code, stock_name, notes, user_id),
                    )
            logger.info(f"股票 {code} 已添加到用户 {user_id} 自选")
            return True
        except Exception as e:
            logger.error(f"添加股票失败: {e}")
            return False
        finally:
            conn.close()

    @staticmethod
    def remove(stock_code: str, user_id: str = 'admin') -> bool:
        """从自选列表删除股票（物理删除）。"""
        conn = _get_connection()
        if conn is None:
            return False
        try:
            code = stock_code.strip().upper()
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM stock_watchlist WHERE stock_code = %s AND user_id = %s",
                        (code, user_id),
                    )
                    if cur.rowcount == 0:
                        logger.warning(f"股票 {code} 不在用户 {user_id} 自选列表中")
                        return False
            logger.info(f"股票 {code} 已从用户 {user_id} 自选删除")
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
        user_id: str = 'admin'
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
            
            sql = f"UPDATE stock_watchlist SET {', '.join(updates)} WHERE stock_code = %s AND user_id = %s"
            params.append(code)
            params.append(user_id)

            with conn:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
                    if cur.rowcount == 0:
                        logger.warning(f"股票 {code} 不在用户 {user_id} 自选列表中")
                        return False
            logger.info(f"股票 {code} 信息已更新")
            return True
        except Exception as e:
            logger.error(f"更新股票失败: {e}")
            return False
        finally:
            conn.close()

    @staticmethod
    def get(stock_code: str, user_id: str = 'admin') -> Optional[Dict[str, Any]]:
        """获取单只股票详情。"""
        conn = _get_connection()
        if conn is None:
            return None
        try:
            code = stock_code.strip().upper()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, stock_code, stock_name, notes, is_active, user_id, "
                    "created_at, updated_at FROM stock_watchlist WHERE stock_code = %s AND user_id = %s",
                    (code, user_id),
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
