"""Indonesian ISP / TrustPositif / Komdigi block checker (run from local PC in Indonesia)."""

from __future__ import annotations

import asyncio
import socket
from typing import Any

import httpx

from app.db import Database
from app.verifier import BROWSER_HEADERS

# Known Indonesian ISP DNS sinkhole / TrustPositif block IPs
TRUSTPOSITIF_SINKHOLE_IPS = {
    "36.86.63.185",     # Telkom / TrustPositif
    "118.97.115.30",    # Telkom Mercusuar
    "114.6.128.4",      # Indosat
    "180.131.144.144",  # Nawala / DNS Nawala
    "180.131.145.145",
    "103.28.148.200",
    "127.0.0.1",
    "0.0.0.0",
}

# Signatures of Indonesian ISP / Komdigi block pages
BLOCK_PAGE_SIGNATURES = (
    "trustpositif",
    "internetpositif",
    "internet positif",
    "internetbaik",
    "internet baik",
    "block.uzone.id",
    "mercusuar.uzone.id",
    "positif.telkomsel",
    "situs ini diblokir",
    "situs yang anda tuju diblokir",
    "akses diblokir",
    "kementerian komunikasi",
    "komdigi",
    "kominfo",
    "undang-undang nomor 11 tahun 2008",
    "peraturan menteri komunikasi",
)


def _resolve_ips(domain: str) -> list[str]:
    """Resolve IPv4 addresses for a domain using the local Indonesian ISP DNS."""
    infos = socket.getaddrinfo(domain, None, family=socket.AF_INET)
    return sorted({info[4][0] for info in infos})


async def _check_one_isp(
    row: dict[str, Any],
    *,
    client: httpx.AsyncClient,
    db: Database,
    semaphore: asyncio.Semaphore,
    stats: dict[str, int],
) -> None:
    async with semaphore:
        candidate_id = int(row["id"])
        domain = str(row["domain"])
        url = str(row["original_url"])

        # 1. DNS sinkhole check
        try:
            ips = await asyncio.to_thread(_resolve_ips, domain)
        except socket.gaierror as exc:
            await db.update_isp_check(
                candidate_id,
                accessible=False,
                note=f"DNS resolution failed (likely NXDOMAIN/blocked): {exc}",
            )
            stats["blocked"] += 1
            return

        sinkholed = [ip for ip in ips if ip in TRUSTPOSITIF_SINKHOLE_IPS]
        if sinkholed:
            await db.update_isp_check(
                candidate_id,
                accessible=False,
                note=f"DNS sinkhole detected: {', '.join(sinkholed)}",
            )
            stats["blocked"] += 1
            return

        # 2. HTTP / DPI / Redirect block-page check
        try:
            resp = await client.get(url)
            final_url_lower = str(resp.url).lower()
            body_lower = resp.text[:4000].lower()

            for sig in BLOCK_PAGE_SIGNATURES:
                if sig in final_url_lower or sig in body_lower:
                    await db.update_isp_check(
                        candidate_id,
                        accessible=False,
                        note=f"ISP block page detected ('{sig}')",
                    )
                    stats["blocked"] += 1
                    return

            if resp.status_code < 400:
                await db.update_isp_check(
                    candidate_id,
                    accessible=True,
                    note=f"Accessible from ID ISP (HTTP {resp.status_code})",
                )
                stats["accessible"] += 1
            else:
                await db.update_isp_check(
                    candidate_id,
                    accessible=False,
                    note=f"HTTP error {resp.status_code} from ID ISP",
                )
                stats["blocked"] += 1
        except httpx.HTTPError as exc:
            await db.update_isp_check(
                candidate_id,
                accessible=False,
                note=f"Connection/DPI reset from ID ISP: {type(exc).__name__}",
            )
            stats["blocked"] += 1


async def check_isp_accessibility(
    db: Database,
    *,
    limit: int = 100,
    recheck: bool = False,
    concurrency: int = 10,
) -> dict[str, int]:
    """Check VERIFIED_ILLEGAL candidates from the local Indonesian network."""
    rows = await db.get_candidates_for_isp_check(limit=limit, recheck=recheck)
    stats = {"checked": len(rows), "accessible": 0, "blocked": 0}
    if not rows:
        return stats

    semaphore = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(
        headers=BROWSER_HEADERS,
        timeout=10.0,
        follow_redirects=True,
        verify=False,
    ) as client:
        tasks = [
            _check_one_isp(
                row,
                client=client,
                db=db,
                semaphore=semaphore,
                stats=stats,
            )
            for row in rows
        ]
        await asyncio.gather(*tasks)

    return stats
