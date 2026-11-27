"""PostgreSQL (Supabase) database layer with atomic queue locking."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg.rows import dict_row


@dataclass(frozen=True, slots=True)
class CandidateInput:
    """Validated candidate ready to be inserted into the database."""

    normalized_url: str
    original_url: str
    domain: str
    source: str


class Database:
    """Thin async & sync wrapper around Supabase PostgreSQL using psycopg3."""

    def __init__(self, database_url: str) -> None:
        if not database_url:
            raise ValueError(
                "DATABASE_URL is not configured. Set it in your .env file or environment."
            )
        self.database_url = database_url

    async def _aconnect(self) -> psycopg.AsyncConnection[dict[str, Any]]:
        # prepare_threshold=None ensures compatibility with Supabase PgBouncer transaction pooling
        return await psycopg.AsyncConnection.connect(
            self.database_url,
            row_factory=dict_row,
            prepare_threshold=None,
        )

    def _connect(self) -> psycopg.Connection[dict[str, Any]]:
        return psycopg.connect(
            self.database_url,
            row_factory=dict_row,
            prepare_threshold=None,
        )

    async def insert_candidates(self, candidates: list[CandidateInput]) -> int:
        """Bulk-insert candidates, ignoring duplicates by normalized_url.

        Returns the number of newly inserted rows.
        """
        if not candidates:
            return 0

        # Deduplicate within the incoming batch first
        unique_map: dict[str, CandidateInput] = {}
        for item in candidates:
            unique_map.setdefault(item.normalized_url, item)
        deduped = list(unique_map.values())

        query = """
            INSERT INTO candidates (normalized_url, original_url, domain, source, status)
            VALUES (%s, %s, %s, %s, 'PENDING')
            ON CONFLICT (normalized_url) DO NOTHING
            RETURNING id
        """
        inserted = 0
        async with await self._aconnect() as conn:
            async with conn.cursor() as cur:
                for item in deduped:
                    await cur.execute(
                        query,
                        (item.normalized_url, item.original_url, item.domain, item.source),
                    )
                    if await cur.fetchone() is not None:
                        inserted += 1
            await conn.commit()
        return inserted

    async def claim_pending(
        self,
        worker_id: str,
        batch_size: int = 30,
        lock_timeout_minutes: int = 15,
    ) -> list[dict[str, Any]]:
        """Atomically claim a batch of PENDING candidates using FOR UPDATE SKIP LOCKED."""
        async with await self._aconnect() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT * FROM claim_pending_candidates(%s, %s, %s)",
                    (worker_id, batch_size, lock_timeout_minutes),
                )
                rows = await cur.fetchall()
            await conn.commit()
        return list(rows)

    async def update_verification_result(
        self,
        candidate_id: int,
        *,
        status: str,
        http_status: int | None = None,
        final_url: str | None = None,
        page_title: str | None = None,
        meta_description: str | None = None,
        is_parked: bool = False,
        category: str | None = None,
        confidence: float | None = None,
        ai_reason: str | None = None,
    ) -> None:
        """Persist HTTP check and AI classification results and release the worker lock."""
        query = """
            UPDATE candidates
            SET status = %s,
                http_status = %s,
                final_url = %s,
                page_title = %s,
                meta_description = %s,
                is_parked = %s,
                category = %s,
                confidence = %s,
                ai_reason = %s,
                locked_by = NULL,
                locked_at = NULL
            WHERE id = %s
        """
        async with await self._aconnect() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    query,
                    (
                        status,
                        http_status,
                        final_url,
                        page_title,
                        meta_description,
                        is_parked,
                        category,
                        confidence,
                        ai_reason,
                        candidate_id,
                    ),
                )
            await conn.commit()

    async def get_candidates_for_isp_check(
        self,
        limit: int = 100,
        recheck: bool = False,
    ) -> list[dict[str, Any]]:
        """Fetch VERIFIED_ILLEGAL candidates for local Indonesian ISP accessibility checking."""
        if recheck:
            query = """
                SELECT id, original_url, normalized_url, domain, category, confidence
                FROM candidates
                WHERE status = 'VERIFIED_ILLEGAL'
                ORDER BY confidence DESC NULLS LAST, created_at DESC
                LIMIT %s
            """
        else:
            query = """
                SELECT id, original_url, normalized_url, domain, category, confidence
                FROM candidates
                WHERE status = 'VERIFIED_ILLEGAL'
                  AND id_isp_accessible IS NULL
                ORDER BY confidence DESC NULLS LAST, created_at DESC
                LIMIT %s
            """
        async with await self._aconnect() as conn:
            async with conn.cursor() as cur:
                await cur.execute(query, (limit,))
                return list(await cur.fetchall())

    async def update_isp_check(
        self,
        candidate_id: int,
        *,
        accessible: bool,
        note: str,
    ) -> None:
        """Record Indonesian ISP accessibility check result."""
        query = """
            UPDATE candidates
            SET id_isp_accessible = %s,
                id_isp_note = %s,
                id_isp_checked_at = NOW()
            WHERE id = %s
        """
        async with await self._aconnect() as conn:
            async with conn.cursor() as cur:
                await cur.execute(query, (accessible, note[:500], candidate_id))
            await conn.commit()

    def get_submit_queue(
        self,
        limit: int = 50,
        only_isp_accessible: bool = False,
    ) -> list[dict[str, Any]]:
        """Fetch best VERIFIED_ILLEGAL candidates prioritized for solo submission."""
        isp_filter = "AND id_isp_accessible IS TRUE" if only_isp_accessible else ""
        query = f"""
            SELECT id, original_url, normalized_url, domain, category, confidence,
                   page_title, ai_reason, id_isp_accessible, id_isp_note, source, created_at
            FROM candidates
            WHERE status = 'VERIFIED_ILLEGAL'
              {isp_filter}
            ORDER BY id_isp_accessible DESC NULLS LAST,
                     confidence DESC NULLS LAST,
                     created_at DESC
            LIMIT %s
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (limit,))
                return list(cur.fetchall())

    def mark_status(self, candidate_id: int, status: str) -> None:
        """Update candidate status (e.g. SUBMITTED, SKIPPED, FALSE_POSITIVE)."""
        if status == "SUBMITTED":
            query = """
                UPDATE candidates
                SET status = %s, submitted_at = NOW()
                WHERE id = %s
            """
        else:
            query = """
                UPDATE candidates
                SET status = %s
                WHERE id = %s
            """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (status, candidate_id))
            conn.commit()

    def get_stats(self) -> dict[str, Any]:
        """Return summary counts for dashboard / status command."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT status, COUNT(*) AS cnt
                    FROM candidates
                    GROUP BY status
                    ORDER BY cnt DESC
                    """
                )
                by_status = {row["status"]: row["cnt"] for row in cur.fetchall()}

                cur.execute(
                    """
                    SELECT category, COUNT(*) AS cnt
                    FROM candidates
                    WHERE status IN ('VERIFIED_ILLEGAL', 'SUBMITTED')
                    GROUP BY category
                    ORDER BY cnt DESC
                    """
                )
                by_category = {row["category"]: row["cnt"] for row in cur.fetchall()}

                cur.execute(
                    """
                    SELECT
                        COUNT(*) FILTER (WHERE status = 'VERIFIED_ILLEGAL' AND id_isp_accessible IS TRUE) AS isp_ok,
                        COUNT(*) FILTER (WHERE status = 'VERIFIED_ILLEGAL' AND id_isp_accessible IS FALSE) AS isp_blocked,
                        COUNT(*) FILTER (WHERE status = 'VERIFIED_ILLEGAL' AND id_isp_accessible IS NULL) AS isp_unchecked
                    FROM candidates
                    """
                )
                isp_summary = dict(cur.fetchone() or {})

        return {
            "by_status": by_status,
            "by_category": by_category,
            "isp_summary": isp_summary,
        }
