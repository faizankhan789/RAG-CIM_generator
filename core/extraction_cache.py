"""Saved per-document findings, so regenerating a CIM doesn't pay Claude to re-read
documents that haven't changed.

Every document type (PDF, Word, PowerPoint, spreadsheet, image) is read through
core/llm.py:extract_from_content. Its result depends only on the content it sends,
the listing context, the extraction prompt and the model — so a SHA-256 of exactly
those is the key: an unchanged file under an unchanged listing gets the same
findings back from the DB with no Claude call, and the CIM step receives the exact
same input. Any change to any of the four is a new key and the file is read again.

Best-effort: a DB problem never breaks extraction — it just reads the file as before.
Self-provisioning table (CREATE TABLE IF NOT EXISTS on first write), same pattern and
shared connection as core/template_store.py.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Any

import pymysql

from core.template_store import _TABLE_MISSING_ERRNO, _now, _run_db

log = logging.getLogger(__name__)

_CREATE_SQL = """\
CREATE TABLE IF NOT EXISTS extraction_cache (
    cache_key CHAR(64) PRIMARY KEY,
    findings LONGTEXT NOT NULL,
    model VARCHAR(100) NOT NULL,
    created_at DATETIME NOT NULL
)"""

_UPSERT_SQL = """\
INSERT INTO extraction_cache (cache_key, findings, model, created_at)
VALUES (%s, %s, %s, %s)
ON DUPLICATE KEY UPDATE findings = VALUES(findings), model = VALUES(model), created_at = VALUES(created_at)"""


def cache_key(content_blocks: list[dict[str, Any]], listing_xml: str, prompt: str, model: str) -> str:
    payload = json.dumps([content_blocks, listing_xml or "", prompt, model], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _db_get(key: str) -> str | None:
    def _op(conn):
        with conn.cursor() as cur:
            cur.execute("SELECT findings FROM extraction_cache WHERE cache_key=%s", (key,))
            row = cur.fetchone()
        return row["findings"] if row else None
    try:
        return _run_db(_op)
    except pymysql.err.ProgrammingError as exc:
        if exc.args and exc.args[0] == _TABLE_MISSING_ERRNO:
            return None          # nothing saved yet — the first put creates the table
        raise


def _db_put(key: str, findings: str, model: str) -> None:
    params = (key, findings, model, _now())

    def _op(conn):
        try:
            with conn.cursor() as cur:
                cur.execute(_UPSERT_SQL, params)
        except pymysql.err.ProgrammingError as exc:
            if not (exc.args and exc.args[0] == _TABLE_MISSING_ERRNO):
                raise
            with conn.cursor() as cur:
                cur.execute(_CREATE_SQL)
                cur.execute(_UPSERT_SQL, params)
    _run_db(_op)


async def get(key: str) -> str | None:
    try:
        return await asyncio.get_event_loop().run_in_executor(None, _db_get, key)
    except Exception as exc:
        log.warning("extraction_cache get failed (reading the file instead): %s", exc)
        return None


async def put(key: str, findings: str, model: str) -> None:
    try:
        await asyncio.get_event_loop().run_in_executor(None, _db_put, key, findings, model)
    except Exception as exc:
        log.warning("extraction_cache put failed (non-fatal): %s", exc)
