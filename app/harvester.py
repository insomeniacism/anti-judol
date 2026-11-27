"""OSINT Harvester: crt.sh, URLScan.io, and real-time Certstream."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import httpx
import websockets

from app.config import Settings
from app.db import CandidateInput, Database
from app.keywords import CERTSTREAM_PATTERN, DEFAULT_HARVEST_KEYWORDS
from app.url_utils import InvalidURLError, domain_to_url, validate_and_normalize

logger = logging.getLogger(__name__)

USER_AGENT = "anti-judol-hunter/0.1 (OSINT Research; Passive)"


def _try_make_candidate(raw_url_or_domain: str, source: str) -> CandidateInput | None:
    """Convert raw domain/URL into a validated CandidateInput, or None if invalid."""
    try:
        url = domain_to_url(raw_url_or_domain)
        norm = validate_and_normalize(url)
        return CandidateInput(
            normalized_url=norm.normalized_url,
            original_url=norm.original_url,
            domain=norm.domain,
            source=source,
        )
    except InvalidURLError:
        return None


async def harvest_crtsh(
    keywords: list[str],
    limit_per_keyword: int = 80,
    timeout: float = 20.0,
) -> list[CandidateInput]:
    """Query crt.sh Certificate Transparency logs for domains matching keywords."""
    results: dict[str, CandidateInput] = {}
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}

    async with httpx.AsyncClient(headers=headers, timeout=timeout, follow_redirects=True) as client:
        for kw in keywords:
            url = f"https://crt.sh/?q=%25{kw}%25&output=json"
            for attempt in range(2):
                try:
                    resp = await client.get(url)
                    if resp.status_code in (429, 502, 503, 504):
                        await asyncio.sleep(2.0 * (attempt + 1))
                        continue
                    if resp.status_code != 200:
                        break
                    data = resp.json()
                    if not isinstance(data, list):
                        break
                    count = 0
                    for entry in data:
                        if count >= limit_per_keyword:
                            break
                        name_val = entry.get("name_value") or entry.get("common_name") or ""
                        for line in str(name_val).splitlines():
                            cand = _try_make_candidate(line, source="crtsh")
                            if cand and cand.normalized_url not in results:
                                results[cand.normalized_url] = cand
                                count += 1
                    break
                except (httpx.HTTPError, ValueError) as exc:
                    logger.debug("crt.sh error for keyword '%s': %s", kw, exc)
                    await asyncio.sleep(1.5)
            # Polite pause between keywords to avoid crt.sh rate limits
            await asyncio.sleep(1.0)

    return list(results.values())


async def harvest_urlscan(
    keywords: list[str],
    api_key: str = "",
    size_per_keyword: int = 40,
    timeout: float = 15.0,
) -> list[CandidateInput]:
    """Query URLScan.io public search API for Indonesian illegal site keywords."""
    results: dict[str, CandidateInput] = {}
    headers = {"User-Agent": USER_AGENT}
    if api_key:
        headers["API-Key"] = api_key

    async with httpx.AsyncClient(headers=headers, timeout=timeout, follow_redirects=True) as client:
        for kw in keywords:
            # Anonymous URLScan queries disallow leading wildcards (*kw*).
            query = (
                f"domain:*{kw}* OR page.title:{kw}"
                if api_key
                else f"domain:{kw}* OR page.title:{kw}"
            )
            params = {"q": query, "size": size_per_keyword}
            try:
                resp = await client.get("https://urlscan.io/api/v1/search/", params=params)
                if resp.status_code == 429:
                    logger.warning("URLScan rate limit hit; pausing 5s")
                    await asyncio.sleep(5.0)
                    continue
                if resp.status_code != 200:
                    logger.warning(
                        "URLScan returned HTTP %s for keyword '%s'",
                        resp.status_code,
                        kw,
                    )
                    continue
                payload = resp.json()
                for hit in payload.get("results", []):
                    task_url = (hit.get("task") or {}).get("url")
                    page_url = (hit.get("page") or {}).get("url")
                    page_domain = (hit.get("page") or {}).get("domain")
                    for raw in (task_url, page_url, page_domain):
                        if not raw:
                            continue
                        cand = _try_make_candidate(str(raw), source="urlscan")
                        if cand and cand.normalized_url not in results:
                            results[cand.normalized_url] = cand
                            break
            except (httpx.HTTPError, ValueError) as exc:
                logger.debug("urlscan error for keyword '%s': %s", kw, exc)
            await asyncio.sleep(0.8)

    return list(results.values())


async def run_certstream(
    db: Database,
    duration_seconds: int = 120,
    flush_size: int = 20,
) -> int:
    """Listen to Certstream real-time CT logs and insert matching domains."""
    ws_url = "wss://certstream.calidog.io/"
    buffer: dict[str, CandidateInput] = {}
    total_inserted = 0
    deadline = time.monotonic() + duration_seconds

    async with websockets.connect(ws_url, ping_interval=20, ping_timeout=20) as ws:
        while time.monotonic() < deadline:
            remaining = max(0.5, deadline - time.monotonic())
            try:
                raw_msg = await asyncio.wait_for(ws.recv(), timeout=min(remaining, 10.0))
            except asyncio.TimeoutError:
                continue

            try:
                msg: dict[str, Any] = json.loads(raw_msg)
            except json.JSONDecodeError:
                continue

            if msg.get("message_type") != "certificate_update":
                continue

            domains = (
                msg.get("data", {})
                .get("leaf_cert", {})
                .get("all_domains", [])
            )
            for dom in domains:
                if not isinstance(dom, str) or not CERTSTREAM_PATTERN.search(dom):
                    continue
                cand = _try_make_candidate(dom, source="certstream")
                if cand:
                    buffer[cand.normalized_url] = cand

            if len(buffer) >= flush_size:
                total_inserted += await db.insert_candidates(list(buffer.values()))
                buffer.clear()

    if buffer:
        total_inserted += await db.insert_candidates(list(buffer.values()))

    return total_inserted


async def harvest_once(
    db: Database,
    settings: Settings,
    sources: list[str] | None = None,
    keywords: list[str] | None = None,
) -> dict[str, int]:
    """Run selected OSINT harvesters and store unique candidates in Supabase."""
    active_sources = sources or ["crtsh", "urlscan"]
    kw_list = keywords or DEFAULT_HARVEST_KEYWORDS
    stats: dict[str, int] = {}

    if "crtsh" in active_sources:
        crt_candidates = await harvest_crtsh(kw_list)
        inserted = await db.insert_candidates(crt_candidates)
        stats["crtsh_found"] = len(crt_candidates)
        stats["crtsh_inserted"] = inserted

    if "urlscan" in active_sources:
        urlscan_candidates = await harvest_urlscan(
            kw_list,
            api_key=settings.urlscan_api_key,
        )
        inserted = await db.insert_candidates(urlscan_candidates)
        stats["urlscan_found"] = len(urlscan_candidates)
        stats["urlscan_inserted"] = inserted

    return stats
