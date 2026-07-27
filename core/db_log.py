"""Async-safe logging of CIM generation requests to rag_cim_db."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import pymysql
import pymysql.cursors

log = logging.getLogger(__name__)

_DB_CONFIG = {
    "host":     os.getenv("RAG_CIM_DB_HOST", ""),
    "port":     int(os.getenv("RAG_CIM_DB_PORT", "25060")),
    "user":     os.getenv("RAG_CIM_DB_USER", ""),
    "password": os.getenv("RAG_CIM_DB_PASS", ""),
    "database": os.getenv("RAG_CIM_DB_NAME", "rag_cim_db"),
    "ssl":    {"ca": ""},   # DO requires SSL; empty ca = accept any cert
    "autocommit": True,
    "charset": "utf8mb4",
    "cursorclass": pymysql.cursors.DictCursor,
}


def _connect() -> pymysql.connections.Connection:
    cfg = dict(_DB_CONFIG)
    return pymysql.connect(**cfg)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


async def log_start(
    username: str,
    crm_url: str,
    listing_id: str,
    listing_name: str,
) -> Optional[int]:
    """INSERT a new row at request start. Returns the row id (used to UPDATE later)."""
    def _insert():
        try:
            conn = _connect()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO cim_generation_log
                        (username, crm_url, listing_id, listing_name, requested_at, status)
                    VALUES (%s, %s, %s, %s, %s, 'started')
                    """,
                    (username, crm_url, str(listing_id), listing_name, _now()),
                )
                row_id = cur.lastrowid
            conn.close()
            return row_id
        except Exception as exc:
            log.warning("db_log insert failed: %s", exc)
            return None

    return await asyncio.get_event_loop().run_in_executor(None, _insert)


async def log_complete(
    row_id: int,
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> None:
    """UPDATE row on successful completion."""
    def _update():
        try:
            conn = _connect()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE cim_generation_log
                    SET status='completed', completed_at=%s,
                        model=%s, input_tokens=%s, output_tokens=%s,
                        total_tokens=%s
                    WHERE id=%s
                    """,
                    (_now(), model, input_tokens, output_tokens,
                     input_tokens + output_tokens, row_id),
                )
            conn.close()
        except Exception as exc:
            log.warning("db_log update (complete) failed: %s", exc)

    await asyncio.get_event_loop().run_in_executor(None, _update)


async def log_error(row_id: int) -> None:
    """UPDATE row on pipeline failure."""
    def _update():
        try:
            conn = _connect()
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE cim_generation_log SET status='failed', completed_at=%s WHERE id=%s",
                    (_now(), row_id),
                )
            conn.close()
        except Exception as exc:
            log.warning("db_log update (error) failed: %s", exc)

    await asyncio.get_event_loop().run_in_executor(None, _update)
