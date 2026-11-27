"""Fast interactive Solo Submitter CLI with automatic clipboard copy."""

from __future__ import annotations

import shutil
import subprocess
import webbrowser

import pyperclip
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from app.db import Database

console = Console()


def copy_to_clipboard(text: str) -> bool:
    """Copy text to system clipboard using pyperclip with OS tool fallbacks."""
    try:
        pyperclip.copy(text)
        return True
    except pyperclip.PyperclipException:
        pass

    for cmd in (
        ["wl-copy"],
        ["xclip", "-selection", "clipboard"],
        ["xsel", "--clipboard", "--input"],
        ["pbcopy"],
        ["clip"],
    ):
        if shutil.which(cmd[0]):
            try:
                subprocess.run(cmd, input=text.encode("utf-8"), check=True)
                return True
            except subprocess.SubprocessError:
                continue
    return False


def run_submitter(
    db: Database,
    *,
    limit: int = 50,
    only_isp_accessible: bool = False,
) -> None:
    """Interactive rapid-fire submission loop for solo competitors."""
    queue = db.get_submit_queue(limit=limit, only_isp_accessible=only_isp_accessible)
    if not queue:
        console.print(
            "[yellow]Tidak ada kandidat VERIFIED_ILLEGAL yang siap submit.[/yellow]\n"
            "Jalankan `anti-judol harvest` dan `anti-judol verify` terlebih dahulu."
        )
        return

    total = len(queue)
    submitted_count = 0
    skipped_count = 0

    console.print(
        f"\n[bold green]Siap submit {total} link terverifikasi![/bold green] "
        "[dim](Tekan Enter = tandai SUBMITTED & lanjut, s = skip, f = false positive, o = buka browser, q = keluar)[/dim]\n"
    )

    for idx, row in enumerate(queue, start=1):
        url = str(row["original_url"])
        copied = copy_to_clipboard(url)
        clip_badge = "[green]COPIED TO CLIPBOARD[/green]" if copied else "[red]CLIPBOARD FAILED[/red]"

        isp_val = row.get("id_isp_accessible")
        if isp_val is True:
            isp_badge = "[bold green]TEMBUS ISP ID[/bold green]"
        elif isp_val is False:
            isp_badge = "[red]TERBLOKIR ISP ID[/red]"
        else:
            isp_badge = "[yellow]BELUM CEK ISP[/yellow]"

        conf = row.get("confidence")
        conf_str = f"{conf:.2f}" if isinstance(conf, (int, float)) else "-"

        body = Text()
        body.append(f"URL      : ", style="bold")
        body.append(f"{url}\n", style="bold cyan underline")
        body.append(f"Kategori : ", style="bold")
        body.append(f"{row.get('category') or '-'} (conf: {conf_str})\n")
        body.append(f"Title    : ", style="bold")
        body.append(f"{row.get('page_title') or '(kosong)'}\n")
        body.append(f"Alasan AI: ", style="bold")
        body.append(f"{row.get('ai_reason') or '-'}")

        console.print(
            Panel(
                body,
                title=f"[{idx}/{total}] {isp_badge} | {clip_badge}",
                border_style="bright_blue",
            )
        )

        while True:
            try:
                choice = console.input(
                    "[bold]Aksi[/bold] ([green]Enter=Submitted[/green] / s=Skip / f=FalsePos / o=Open / q=Quit): "
                ).strip().lower()
            except (EOFError, KeyboardInterrupt):
                choice = "q"

            if choice in ("", "y"):
                db.mark_status(int(row["id"]), "SUBMITTED")
                submitted_count += 1
                console.print(f"[green]✔ Ditandai SUBMITTED[/green]\n")
                break
            if choice == "s":
                db.mark_status(int(row["id"]), "SKIPPED")
                skipped_count += 1
                console.print(f"[yellow]↷ Dilewati (SKIPPED)[/yellow]\n")
                break
            if choice == "f":
                db.mark_status(int(row["id"]), "FALSE_POSITIVE")
                skipped_count += 1
                console.print(f"[red]✘ Ditandai FALSE_POSITIVE[/red]\n")
                break
            if choice == "o":
                webbrowser.open(url)
                console.print("[dim]Membuka di browser...[/dim]")
                continue
            if choice == "q":
                console.print(
                    f"\n[bold]Sesi selesai.[/bold] Submitted: [green]{submitted_count}[/green] | "
                    f"Skipped/Rejected: [yellow]{skipped_count}[/yellow]"
                )
                return
            console.print("[red]Pilihan tidak dikenal. Gunakan Enter, s, f, o, atau q.[/red]")

    console.print(
        f"\n[bold green]Semua {total} antrean selesai![/bold green] "
        f"Submitted: [green]{submitted_count}[/green] | Skipped/Rejected: [yellow]{skipped_count}[/yellow]"
    )
