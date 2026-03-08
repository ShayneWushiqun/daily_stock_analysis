# -*- coding: utf-8 -*-
"""
===================================
定时任务仓库 (Supabase PostgreSQL)
===================================

职责：
1. cron_jobs 表的 CRUD 操作
2. 支持基于用户的定时任务存储
"""

import logging
import os
import json
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

def _get_connection():
    """获取 PostgreSQL 连接（每次调用新建连接，保持简单）。"""
    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor
    except ImportError:
        logger.error("psycopg2 未安装，请运行: pip install psycopg2-binary")
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

class CronJobRepository:
    """定时任务仓库 — Supabase PostgreSQL 实现。"""

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
        """确保 cron_jobs 表存在（自动建表）。"""
        conn = _get_connection()
        if conn is None:
            return False
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS cron_jobs (
                            id SERIAL PRIMARY KEY,
                            user_id VARCHAR(100) NOT NULL,
                            job_type VARCHAR(50) NOT NULL,
                            schedule_expression VARCHAR(100) NOT NULL,
                            parameters JSONB,
                            is_active BOOLEAN DEFAULT TRUE,
                            last_run_at TIMESTAMPTZ,
                            next_run_at TIMESTAMPTZ,
                            created_at TIMESTAMPTZ DEFAULT NOW(),
                            updated_at TIMESTAMPTZ DEFAULT NOW()
                        );
                        CREATE INDEX IF NOT EXISTS idx_cron_jobs_user ON cron_jobs(user_id);
                        CREATE INDEX IF NOT EXISTS idx_cron_jobs_active ON cron_jobs(is_active);
                    """)
            return True
        except Exception as e:
            logger.error(f"建表失败: {e}")
            return False
        finally:
            conn.close()

    @staticmethod
    def list_by_user(user_id: str) -> List[Dict[str, Any]]:
        """获取指定用户的定时任务。"""
        conn = _get_connection()
        if conn is None:
            return []
        try:
            from psycopg2.extras import RealDictCursor
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT * FROM cron_jobs 
                    WHERE user_id = %s 
                    ORDER BY created_at DESC
                    """,
                    (user_id,)
                )
                return [dict(row) for row in cur.fetchall()]
        except Exception as e:
            logger.error(f"查询定时任务失败: {e}")
            return []
        finally:
            conn.close()

    @staticmethod
    def create(
        user_id: str,
        job_type: str,
        schedule_expression: str,
        parameters: Optional[Dict[str, Any]] = None,
        is_active: bool = True
    ) -> Optional[int]:
        """创建定时任务。返回新任务 ID。"""
        conn = _get_connection()
        if conn is None:
            return None
        try:
            params_json = json.dumps(parameters) if parameters else None
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO cron_jobs (user_id, job_type, schedule_expression, parameters, is_active)
                        VALUES (%s, %s, %s, %s, %s)
                        RETURNING id
                        """,
                        (user_id, job_type, schedule_expression, params_json, is_active),
                    )
                    new_id = cur.fetchone()[0]
            logger.info(f"定时任务已创建: ID={new_id}, User={user_id}")
            return new_id
        except Exception as e:
            logger.error(f"创建定时任务失败: {e}")
            return None
        finally:
            conn.close()

    @staticmethod
    def update(
        job_id: int,
        schedule_expression: Optional[str] = None,
        parameters: Optional[Dict[str, Any]] = None,
        is_active: Optional[bool] = None,
        user_id: Optional[str] = None # Optional verify ownership
    ) -> bool:
        """更新定时任务。"""
        conn = _get_connection()
        if conn is None:
            return False
        try:
            updates = []
            params = []
            
            if schedule_expression is not None:
                updates.append("schedule_expression = %s")
                params.append(schedule_expression)
            if parameters is not None:
                updates.append("parameters = %s")
                params.append(json.dumps(parameters))
            if is_active is not None:
                updates.append("is_active = %s")
                params.append(is_active)
            
            if not updates:
                return True

            updates.append("updated_at = NOW()")
            
            sql = f"UPDATE cron_jobs SET {', '.join(updates)} WHERE id = %s"
            params.append(job_id)

            if user_id:
                sql += " AND user_id = %s"
                params.append(user_id)

            with conn:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
                    if cur.rowcount == 0:
                        return False
            return True
        except Exception as e:
            logger.error(f"更新定时任务失败: {e}")
            return False
        finally:
            conn.close()

    @staticmethod
    def delete(job_id: int, user_id: Optional[str] = None) -> bool:
        """删除定时任务。"""
        conn = _get_connection()
        if conn is None:
            return False
        try:
            sql = "DELETE FROM cron_jobs WHERE id = %s"
            params = [job_id]
            if user_id:
                sql += " AND user_id = %s"
                params.append(user_id)

            with conn:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
                    return cur.rowcount > 0
        except Exception as e:
            logger.error(f"删除定时任务失败: {e}")
            return False
        finally:
            conn.close()

    @staticmethod
    def get(job_id: int) -> Optional[Dict[str, Any]]:
        """获取单个任务详情。"""
        conn = _get_connection()
        if conn is None:
            return None
        try:
            from psycopg2.extras import RealDictCursor
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM cron_jobs WHERE id = %s", (job_id,))
                row = cur.fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"查询任务失败: {e}")
            return None
        finally:
            conn.close()
