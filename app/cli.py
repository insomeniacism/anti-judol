"""Typer CLI entrypoint for anti-judol-hunter."""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from app.config import get_settings
from app.db import CandidateInput, Database
from app.harvester import harvest_once, run_certstream
from app.isp_checker import check_isp_accessibility
from app.submitter import run_submitter
from app.url_utils import InvalidURLError, domain_to_url, validate_and_normalize
from app.verifier import verify_batch

app = typer.Typer(
    name="anti-judol",
    help="OSINT harvester, async AI verifier, ISP checker, and solo submitter CLI.",
    no_args_is_help=True,
)
console = Console()


def _get_db() -> Database:
    settings = get_settings()
    return Database(settings.database_url)


@app.command()
def harvest(
    source: str = typer.Option(
        "crtsh,urlscan",
        help="Comma-separated OSINT sources: crtsh, urlscan",
    ),
    keyword: Optional[list[str]] = typer.Option(
        None,
        "--keyword",
        "-k",
        help="Custom keyword(s) instead of default Indonesian illegal-site list",
    ),
) -> None:
    """Harvest fresh candidate domains from passive OSINT sources."""
    settings = get_settings()
    db = Database(settings.database_url)
    sources = [s.strip().lower() for s in source.split(",") if s.strip()]
    console.print(f"[cyan]Memulai panen OSINT dari: {', '.join(sources)}...[/cyan]")
    stats = asyncio.run(harvest_once(db, settings, sources=sources, keywords=keyword or None))
    console.print("[green]Panen selesai:[/green]", stats)


@app.command()
def certstream(
    duration: int = typer.Option(120, help="Listen duration in seconds"),
) -> None:
    """Listen to real-time Certstream CT logs (best run on local PC)."""
    db = _get_db()
    console.print(f"[cyan]Mendengarkan Certstream selama {duration} detik...[/cyan]")
    inserted = asyncio.run(run_certstream(db, duration_seconds=duration))
    console.print(f"[green]Selesai! Domain baru tersimpan: {inserted}[/green]")


@app.command()
def verify(
    batch_size: Optional[int] = typer.Option(None, help="Candidates per batch"),
    loops: int = typer.Option(1, help="Number of sequential batches to process"),
) -> None:
    """Claim PENDING candidates atomically, check HTTP liveness, and classify with AI."""
    settings = get_settings()
    db = Database(settings.database_url)

    async def _run() -> None:
        for i in range(1, loops + 1):
            res = await verify_batch(db, settings, batch_size=batch_size)
            console.print(f"[cyan]Batch {i}/{loops}:[/cyan] {res}")
            if res.get("claimed", 0) == 0:
                console.print("[yellow]Antrean PENDING kosong.[/yellow]")
                break

    asyncio.run(_run())


@app.command("check-isp")
def check_isp(
    limit: int = typer.Option(100, help="Max VERIFIED_ILLEGAL candidates to check"),
    recheck: bool = typer.Option(False, help="Re-check already checked candidates"),
    concurrency: int = typer.Option(10, help="Concurrent checks"),
) -> None:
    """Check which verified sites are still unblocked by Indonesian ISPs (run in Indonesia)."""
    db = _get_db()
    console.print(f"[cyan]Mengecek aksesibilitas ISP Indonesia (limit={limit})...[/cyan]")
    stats = asyncio.run(
        check_isp_accessibility(db, limit=limit, recheck=recheck, concurrency=concurrency)
    )
    console.print("[green]Hasil cek ISP ID:[/green]", stats)


@app.command()
def submit(
    limit: int = typer.Option(50, help="Max candidates to load in this session"),
    only_isp_ok: bool = typer.Option(
        False,
        "--only-isp-ok",
        help="Only show links confirmed accessible from Indonesian ISP",
    ),
) -> None:
    """Interactive rapid-fire CLI: auto-copy best links & mark SUBMITTED on Enter."""
    db = _get_db()
    run_submitter(db, limit=limit, only_isp_accessible=only_isp_ok)


@app.command()
def add(
    url: str = typer.Argument(..., help="URL or domain to validate, normalize, and enqueue"),
    source: str = typer.Option("manual", help="Source label"),
) -> None:
    """Manually validate, normalize, and enqueue a single URL/domain."""
    db = _get_db()
    try:
        norm = validate_and_normalize(domain_to_url(url))
    except InvalidURLError as exc:
        console.print(f"[red]URL tidak valid:[/red] {exc}")
        raise typer.Exit(code=1)

    inserted = asyncio.run(
        db.insert_candidates(
            [
                CandidateInput(
                    normalized_url=norm.normalized_url,
                    original_url=norm.original_url,
                    domain=norm.domain,
                    source=source,
                )
            ]
        )
    )
    if inserted:
        console.print(f"[green]Ditambahkan:[/green] {norm.normalized_url}")
    else:
        console.print(f"[yellow]Duplikat (sudah ada di DB):[/yellow] {norm.normalized_url}")


@app.command()
def stats() -> None:
    """Show database queue & submission statistics."""
    db = _get_db()
    data = db.get_stats()

    table = Table(title="anti-judol-hunter Status")
    table.add_column("Status", style="bold")
    table.add_column("Jumlah", justify="right")
    for k, v in data["by_status"].items():
        table.add_row(k, str(v))
    console.print(table)

    if data["by_category"]:
        cat_table = Table(title="Kategori (VERIFIED_ILLEGAL + SUBMITTED)")
        cat_table.add_column("Kategori", style="bold")
        cat_table.add_column("Jumlah", justify="right")
        for k, v in data["by_category"].items():
            cat_table.add_row(str(k), str(v))
        console.print(cat_table)

    isp = data["isp_summary"]
    console.print(
        f"[bold]ISP ID Status (VERIFIED_ILLEGAL):[/bold] "
        f"[green]Tembus: {isp.get('isp_ok', 0)}[/green] | "
        f"[red]Diblokir: {isp.get('isp_blocked', 0)}[/red] | "
        f"[yellow]Belum Cek: {isp.get('isp_unchecked', 0)}[/yellow]"
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    app()
