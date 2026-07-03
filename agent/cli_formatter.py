"""
cli_formatter.py — Rich-based terminal output formatting for the Resume Agent v2 CLI.

Provides clean, coloured terminal output functions used by cli.py:
  - format_resume_preview()   — Formatted table of the tailored resume sections.
  - format_ats_score()        — Colour-coded ATS keyword match score.
  - format_diff()             — What changed between the last two tailored outputs.
  - format_profile_summary()  — Display the current master profile.
  - spinner()                 — Context manager for a phase-labelled spinner.
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table
from rich.text import Text
from rich import box

console = Console(stderr=False)


# ---------------------------------------------------------------------------
# Spinner context manager
# ---------------------------------------------------------------------------

@contextmanager
def spinner(label: str):
    """
    Display a spinner with a phase label during a long-running operation.

    Usage:
        with spinner("Tailoring resume..."):
            await run_agent()
    """
    with Progress(
        SpinnerColumn(spinner_name="dots"),
        TextColumn("[bold cyan]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        progress.add_task(label)
        yield


# ---------------------------------------------------------------------------
# Resume preview
# ---------------------------------------------------------------------------

def format_resume_preview(tailored: dict, name: str = "") -> None:
    """
    Print a formatted preview of the tailored resume sections.

    Args:
        tailored: TailoredSections dict.
        name:     Candidate name for the panel title.
    """
    title = f"[bold white]Tailored Resume Preview[/bold white]"
    if name:
        title += f" — [cyan]{name}[/cyan]"

    # Summary
    summary = tailored.get("summary", "")
    if summary:
        console.print(Panel(
            f"[italic]{summary}[/italic]",
            title="[bold]Summary[/bold]",
            border_style="blue",
            padding=(0, 1),
        ))

    # Experience
    experiences = tailored.get("experience", [])
    if experiences:
        table = Table(
            show_header=True,
            header_style="bold magenta",
            box=box.SIMPLE_HEAD,
            padding=(0, 1),
            expand=True,
        )
        table.add_column("Role", style="bold", no_wrap=True, min_width=30)
        table.add_column("Dates", style="dim", no_wrap=True)
        table.add_column("Bullets", style="white")

        for exp in experiences:
            bullets_text = "\n".join(
                f"• {b[:120]}{'…' if len(b) > 120 else ''}"
                for b in exp.get("bullets", [])
            )
            table.add_row(
                f"{exp.get('title', '')} @ {exp.get('company', '')}",
                f"{exp.get('start_date', '')}–{exp.get('end_date', '')}",
                bullets_text,
            )

        console.print(Panel(table, title="[bold]Work Experience[/bold]", border_style="blue"))

    # Projects
    projects = tailored.get("projects", [])
    if projects:
        proj_lines = []
        for proj in projects:
            proj_lines.append(
                f"[bold]{proj.get('name', '')}[/bold] "
                f"[dim]({proj.get('technologies', '')})[/dim]"
            )
            for b in proj.get("bullets", []):
                proj_lines.append(f"  • {b[:120]}{'…' if len(b) > 120 else ''}")
        console.print(Panel(
            "\n".join(proj_lines),
            title="[bold]Projects[/bold]",
            border_style="blue",
        ))

    # Skills summary
    skills = tailored.get("skills", {})
    if skills:
        skill_lines = [
            f"[bold]{cat}:[/bold] {', '.join(items)}"
            for cat, items in skills.items()
        ]
        console.print(Panel(
            "\n".join(skill_lines),
            title="[bold]Skills[/bold]",
            border_style="blue",
        ))

    # Stats bar
    exp_count = len(experiences)
    proj_count = len(projects)
    ach_count = len(tailored.get("achievements", []))
    console.print(
        f"\n[dim]Sections: {exp_count} experience(s) | {proj_count} project(s) | {ach_count} achievement(s)[/dim]"
    )


# ---------------------------------------------------------------------------
# ATS score display
# ---------------------------------------------------------------------------

def format_ats_score(score_data: dict) -> None:
    """
    Print a colour-coded ATS keyword match score.

    Args:
        score_data: ATSScore dict from score_ats_match tool.
    """
    if "error" in score_data:
        console.print(f"[red]ATS Scoring Error:[/red] {score_data['error']}")
        return

    overall = score_data.get("overall_score", 0.0)
    passed = score_data.get("passed_threshold", False)
    missing = score_data.get("missing_keywords", [])
    suggestions = score_data.get("suggestions", [])

    # Overall score badge
    if overall >= 80:
        badge_colour = "green"
        badge_label = "EXCELLENT"
    elif overall >= 65:
        badge_colour = "yellow"
        badge_label = "GOOD"
    else:
        badge_colour = "red"
        badge_label = "NEEDS IMPROVEMENT"

    console.print(
        f"\n[bold]ATS Score:[/bold] "
        f"[bold {badge_colour}]{overall:.1f}%[/bold {badge_colour}] "
        f"[{badge_colour}]({badge_label})[/{badge_colour}]"
    )

    # Per-section scores
    section_scores = score_data.get("section_scores", [])
    if section_scores:
        table = Table(box=box.SIMPLE, show_header=True, header_style="bold")
        table.add_column("Section")
        table.add_column("Coverage")
        table.add_column("Top Missing Keywords")

        for ss in section_scores:
            pct = ss.get("coverage_pct", 0.0)
            colour = "green" if pct >= 70 else ("yellow" if pct >= 45 else "red")
            missing_preview = ", ".join(ss.get("missing_keywords", [])[:4])
            table.add_row(
                ss.get("section_name", ""),
                f"[{colour}]{pct:.1f}%[/{colour}]",
                f"[dim]{missing_preview}[/dim]",
            )

        console.print(table)

    # Missing keywords
    if missing:
        console.print(
            f"[bold]Global Missing Keywords:[/bold] [dim]{', '.join(missing[:8])}[/dim]"
        )

    # Suggestions
    if suggestions and not passed:
        console.print("\n[bold yellow]Suggestions to improve coverage:[/bold yellow]")
        for s in suggestions:
            console.print(f"  [yellow]→[/yellow] {s}")


# ---------------------------------------------------------------------------
# Resume diff display
# ---------------------------------------------------------------------------

def format_diff(diff_data: dict) -> None:
    """
    Print a human-readable diff between the last two tailored resume versions.

    Args:
        diff_data: Output from the diff_resume tool.
    """
    if "error" in diff_data:
        console.print(f"[red]Diff Error:[/red] {diff_data['error']}")
        return

    if "message" in diff_data and not diff_data.get("changed_sections"):
        console.print(f"[dim]{diff_data['message']}[/dim]")
        return

    changed = diff_data.get("changed_sections", [])
    console.print(f"\n[bold]Changes detected in:[/bold] {', '.join(changed) or 'none'}")

    for section in changed:
        section_diff = diff_data.get(section, {})
        if section == "summary":
            console.print(f"\n[bold blue]Summary changed[/bold blue]")
            prev = section_diff.get("previous", "")
            curr = section_diff.get("current", "")
            if prev:
                console.print(f"  [red]- {prev}[/red]")
            if curr:
                console.print(f"  [green]+ {curr}[/green]")
        else:
            added = section_diff.get("added", [])
            removed = section_diff.get("removed", [])
            if added or removed:
                console.print(f"\n[bold blue]{section.title()} changes[/bold blue]")
                for item in removed:
                    console.print(f"  [red]- {item}[/red]")
                for item in added:
                    console.print(f"  [green]+ {item}[/green]")


# ---------------------------------------------------------------------------
# Profile summary display
# ---------------------------------------------------------------------------

def format_profile_summary(profile: dict) -> None:
    """Display the current master profile in a structured summary."""
    name = profile.get("name", "(no name)")
    console.print(Panel(
        f"[bold]{name}[/bold]\n"
        f"Email: {profile.get('email', '')}\n"
        f"Phone: {profile.get('phone', '')}\n"
        f"LinkedIn: {profile.get('linkedin', '')}\n"
        f"GitHub: {profile.get('github', '')}",
        title="[bold]Master Profile — Identity[/bold]",
        border_style="cyan",
    ))

    exp = profile.get("experience", [])
    if exp:
        table = Table(box=box.SIMPLE, show_header=True, header_style="bold")
        table.add_column("Company")
        table.add_column("Title")
        table.add_column("Dates")
        table.add_column("Bullets")
        for e in exp:
            table.add_row(
                e.get("company", ""),
                e.get("title", ""),
                f"{e.get('start_date', '')}–{e.get('end_date', '')}",
                str(len(e.get("bullets", []))),
            )
        console.print(Panel(table, title=f"[bold]Experience[/bold] ({len(exp)} entries)", border_style="cyan"))

    projs = profile.get("projects", [])
    if projs:
        console.print(
            Panel(
                "\n".join(f"• {p.get('name', '')} [{p.get('technologies', '')}]" for p in projs),
                title=f"[bold]Projects[/bold] ({len(projs)})",
                border_style="cyan",
            )
        )

    skills = profile.get("skills", {})
    if skills:
        console.print(
            Panel(
                "\n".join(f"[bold]{cat}:[/bold] {', '.join(items)}" for cat, items in skills.items()),
                title="[bold]Skills[/bold]",
                border_style="cyan",
            )
        )


# ---------------------------------------------------------------------------
# Output path summary
# ---------------------------------------------------------------------------

def format_output_summary(tex_path: str, pdf_path: Optional[str]) -> None:
    """Print a summary of output file locations."""
    console.print("\n[bold green]✓ Resume generated successfully![/bold green]")
    console.print(f"  LaTeX source → [cyan]{tex_path}[/cyan]")
    if pdf_path:
        console.print(f"  PDF output   → [cyan]{pdf_path}[/cyan]")
    else:
        console.print(
            "  [yellow]PDF compilation skipped.[/yellow] "
            "Install pdflatex (MiKTeX/TeX Live) to enable automatic PDF generation."
        )
