#!/usr/bin/env python3
"""
cli.py — Terminal-first CLI for the Resume Builder Agent v2.

Usage:
  python cli.py                              # Run full pipeline (interactive)
  python cli.py --jd path/to/jd.txt         # Set JD from file and tailor
  python cli.py --no-interactive             # Non-interactive mode (CI/scripts)

Subcommands:
  python cli.py profile show                 # Display current master profile
  python cli.py profile update --file F      # Import profile JSON into SQLite
  python cli.py jd set --file F             # Set job description from file
  python cli.py jd set --paste              # Paste JD interactively
  python cli.py jd show                     # Display current job description
  python cli.py template list               # List template versions
  python cli.py template add --file F       # Add a new template version
  python cli.py template activate --version N  # Set active template version

Design goals:
  - Show ONLY the final agent response — suppress all intermediate events.
  - Use rich for all output (spinners, tables, ATS score).
  - Agent interaction is fully terminal-based.
"""

import asyncio
import json
import logging
import os
import sys
import uuid
from pathlib import Path

import click
from dotenv import load_dotenv
from rich.console import Console
from rich.prompt import Confirm, Prompt

console = Console()

# Ensure the agent directory is in the import path
sys.path.insert(0, str(Path(__file__).parent / "agent"))

# Silence all library loggers — only show our own warnings/errors
logging.basicConfig(level=logging.WARNING)
logging.getLogger("google").setLevel(logging.ERROR)
logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("httpcore").setLevel(logging.ERROR)
logging.getLogger("opentelemetry").setLevel(logging.ERROR)
logging.getLogger("LiteLLM").setLevel(logging.ERROR)

# Load environment variables
load_dotenv(Path(__file__).parent / ".env")
load_dotenv(Path(__file__).parent / "agent" / ".env")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_storage():
    """Return the storage client singleton."""
    from storage_client import storage
    return storage()


def _extract_final_response(session) -> str:
    """
    Extract only the final user-facing text from the agent session events.

    Filters out tool calls, function responses, thoughts, and partial events.
    Returns the last non-empty model response text.
    """
    events = getattr(session, "events", []) or []
    final_text = ""

    # Phrases that indicate internal reasoning — skip these
    SKIP_PREFIXES = (
        "The user wants", "According to", "Following my", "Plan:",
        "Step 1:", "I will now", "I must:", "My workflow is:",
        "Looking at the context", "I will call", "I encountered",
        "Corrected Payload",
    )

    for event in reversed(events):
        author = getattr(event, "author", None)
        if author in ("tailoring_agent", "compilation_agent", "user", None):
            continue
        if getattr(event, "partial", False) or getattr(event, "thought", False):
            continue

        content = getattr(event, "content", None)
        if not content:
            continue

        # Extract text parts
        parts = getattr(content, "parts", None) or []
        text_bits = []
        for p in parts:
            if getattr(p, "thought", None) or getattr(p, "function_call", None) or getattr(p, "function_response", None):
                continue
            text = getattr(p, "text", None)
            if text and text.strip():
                if not any(text.strip().startswith(ph) for ph in SKIP_PREFIXES):
                    text_bits.append(text.strip())

        if text_bits:
            final_text = "\n".join(text_bits)
            break

    return final_text


# ---------------------------------------------------------------------------
# Core agent runner
# ---------------------------------------------------------------------------

async def _run_agent(instruction: str, session_id: str | None = None) -> dict:
    """
    Run the resume builder agent by calling the native ADK API server.
    """
    import httpx
    
    if not session_id:
        session_id = str(uuid.uuid4())
        
    base_url = os.getenv("AGENT_SERVICE_URL", "http://localhost:8000")
    user_id = "cli_user"
    
    # 1. Run the agent step
    url_run = f"{base_url}/run"
    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            response = await client.post(
                url_run,
                json={
                    "appName": "agent",
                    "userId": user_id,
                    "sessionId": session_id,
                    "newMessage": {
                        "role": "user",
                        "parts": [{"text": instruction}]
                    }
                }
            )
            response.raise_for_status()
            events = response.json()
            
            # Extract final text response from events array
            final_response = ""
            SKIP_PREFIXES = (
                "The user wants", "According to", "Following my", "Plan:",
                "Step 1:", "I will now", "I must:", "My workflow is:",
                "Looking at the context", "I will call", "I encountered",
                "Corrected Payload",
            )
            
            # Read events in reverse to find the last orchestrator turn complete response
            for event in reversed(events):
                author = event.get("author")
                if author not in ("resume_builder_agent", "tailoring_agent", "compilation_agent"):
                    continue
                if event.get("partial") or event.get("thought"):
                    continue
                    
                content = event.get("content")
                if not content:
                    continue
                    
                parts = content.get("parts") or []
                text_bits = []
                for p in parts:
                    if p.get("thought") or p.get("functionCall") or p.get("functionResponse"):
                        continue
                    text = p.get("text")
                    if text and text.strip():
                        if not any(text.strip().startswith(ph) for ph in SKIP_PREFIXES):
                            text_bits.append(text.strip())
                            
                if text_bits:
                    final_response = "\n".join(text_bits)
                    break
            
            # 2. Fetch session state to retrieve tailored sections
            url_session = f"{base_url}/apps/agent/users/{user_id}/sessions/{session_id}"
            state_resp = await client.get(url_session)
            state_resp.raise_for_status()
            session_data = state_resp.json()
            state = session_data.get("state", {})
            tailored = state.get("tailored_sections") or state.get("tailored_resume")
            
            # Output file checking
            output_dir = os.getenv("OUTPUT_DIR", "output")
            tex_path = os.path.join(output_dir, "resume.tex") if os.path.exists(os.path.join(output_dir, "resume.tex")) else None
            pdf_path = os.path.join(output_dir, "resume.pdf") if os.path.exists(os.path.join(output_dir, "resume.pdf")) else None
            
            return {
                "response": final_response,
                "tailored": tailored,
                "tex_path": tex_path,
                "pdf_path": pdf_path,
                "session_id": session_id,
            }
    except httpx.ConnectError:
        console.print(
            "\n[red]Error: Cannot connect to the ADK API server.[/red]\n"
            "Please make sure the ADK API server is running on port 8000.\n"
            "Run [cyan]uv run adk api_server --session_service_uri=sqlite:///data/sessions.db ..[/cyan] in the [cyan]agent/[/cyan] directory."
        )
        sys.exit(1)
    except httpx.HTTPStatusError as e:
        console.print(f"\n[red]Error: ADK API server returned status {e.response.status_code}[/red]")
        try:
            err_json = e.response.json()
            if "error" in err_json:
                console.print(f"[red]Details:[/red] {err_json['error']}")
            elif "detail" in err_json:
                console.print(f"[red]Details:[/red] {err_json['detail']}")
        except Exception:
            console.print(f"[red]Details:[/red] {e.response.text}")
        sys.exit(1)
    except Exception as e:
        console.print(f"\n[red]Error occurred while communicating with the agent server:[/red] {e}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

@click.group(invoke_without_command=True)
@click.option("--jd", "jd_file", default=None, help="Path to a job description file.")
@click.option("--output-dir", default="output", show_default=True, help="Output directory for .tex and .pdf files.")
@click.option("--no-interactive", is_flag=True, default=False, help="Run non-interactively (exits after first tailor).")
@click.option("--compile-pdf", is_flag=True, default=False, help="Compile PDF after tailoring.")
@click.pass_context
def cli(ctx, jd_file, output_dir, no_interactive, compile_pdf):
    """Resume Builder Agent v2 — Tailor your resume for any job description."""
    # If a subcommand is invoked, don't run the main pipeline
    if ctx.invoked_subcommand is not None:
        return

    os.environ["OUTPUT_DIR"] = os.path.abspath(output_dir)
    os.makedirs(output_dir, exist_ok=True)

    store = _get_storage()

    # Load JD from file if provided
    if jd_file:
        jd_path = Path(jd_file)
        if not jd_path.exists():
            console.print(f"[red]Error:[/red] JD file not found: {jd_file}")
            sys.exit(1)
        jd_content = jd_path.read_text(encoding="utf-8").strip()
        store.save_job_description(jd_content)
        console.print(f"[dim]Job description loaded from {jd_file}[/dim]")

    # Verify profile and JD are set
    profile = store.load_profile()
    jd = store.load_job_description()

    if not profile:
        console.print(
            "[red]No master profile found.[/red]\n"
            "Add your profile with: [cyan]python cli.py profile update --file data/master_profile.json[/cyan]"
        )
        sys.exit(1)

    if not jd:
        if not no_interactive:
            console.print("[yellow]No job description set.[/yellow]")
            jd_input = Prompt.ask("Paste the job description (or press Enter to exit)")
            if not jd_input.strip():
                sys.exit(0)
            store.save_job_description(jd_input.strip())
        else:
            console.print(
                "[red]No job description set.[/red]\n"
                "Set one with: [cyan]python cli.py jd set --file path/to/jd.txt[/cyan]"
            )
            sys.exit(1)

    _run_pipeline(profile, no_interactive=no_interactive, compile_pdf=compile_pdf)


def _run_pipeline(profile: dict, no_interactive: bool = False, compile_pdf: bool = False):
    """Run the full tailoring + ATS scoring pipeline."""
    from cli_formatter import spinner, format_resume_preview, format_ats_score, format_output_summary

    candidate_name = profile.get("name", "")
    session_id = str(uuid.uuid4())

    # Step 1: Tailor
    tailor_instruction = (
        "Analyze the job description, then tailor my resume for it. "
        "Run the full pipeline: analyze JD, tailor content, score ATS match."
    )
    if compile_pdf:
        tailor_instruction += " Then compile the PDF."

    with spinner(f"Tailoring resume for {candidate_name or 'candidate'}..."):
        result = asyncio.run(_run_agent(tailor_instruction, session_id=session_id))

    tailored = result.get("tailored")
    agent_response = result.get("response", "")

    if not tailored:
        console.print("[red]Tailoring failed.[/red] The agent did not produce a tailored resume.")
        if agent_response:
            console.print(f"\n[dim]Agent response:[/dim]\n{agent_response}")
        sys.exit(1)

    # Show resume preview
    format_resume_preview(tailored, name=candidate_name)

    # Step 2: ATS Scoring feedback loop
    _run_ats_feedback_loop(tailored, session_id=session_id, no_interactive=no_interactive)

    # Step 3: Compile (if requested or user confirms)
    if not compile_pdf and not no_interactive:
        compile_pdf = Confirm.ask("\nCompile PDF?", default=True)

    if compile_pdf:
        with spinner("Compiling PDF..."):
            compile_result = asyncio.run(_run_agent(
                "Compile the PDF from the current tailored resume.",
                session_id=session_id
            ))
        format_output_summary(
            compile_result.get("tex_path", "output/resume.tex"),
            compile_result.get("pdf_path"),
        )
    else:
        console.print("\n[dim]PDF compilation skipped. Run with --compile-pdf to generate.[/dim]")

    # Show final agent response
    if agent_response:
        console.print(f"\n[bold cyan]Agent:[/bold cyan] {agent_response}")


def _run_ats_feedback_loop(tailored: dict, session_id: str, no_interactive: bool = False, max_iterations: int = 2):
    """Run ATS scoring and optionally trigger re-tailoring if score is below threshold."""
    from cli_formatter import spinner, format_ats_score, format_resume_preview
    from storage_client import storage as _storage

    ats_threshold = float(os.getenv("ATS_PASS_THRESHOLD", "75"))
    profile = _storage().load_profile() or {}

    for iteration in range(max_iterations + 1):
        # Score current resume
        with spinner("Scoring ATS keyword match..."):
            score_result = asyncio.run(_run_agent(
                "Score my current tailored resume against the job description.",
                session_id=session_id
            ))

        # Extract ATS score from agent response (parse JSON from response or use fallback)
        score_data: dict = {}
        try:
            import re
            raw_response = score_result.get("response", "")
            json_match = re.search(r"\{.*\}", raw_response, re.DOTALL)
            if json_match:
                score_data = json.loads(json_match.group())
        except Exception:
            pass

        if not score_data:
            console.print("[dim]ATS scoring not available for this run.[/dim]")
            break

        format_ats_score(score_data)

        overall = score_data.get("overall_score", 0.0)
        passed = score_data.get("passed_threshold", overall >= ats_threshold)

        if passed or iteration >= max_iterations:
            break

        # Feedback loop: ask to improve (or auto-improve in non-interactive mode)
        missing = score_data.get("missing_keywords", [])[:5]
        suggestions = score_data.get("suggestions", [])

        if no_interactive:
            console.print(
                f"\n[yellow]Auto-improving coverage for missing keywords: {missing}[/yellow]"
            )
        else:
            improve = Confirm.ask(
                f"\nATS score is [yellow]{overall:.1f}%[/yellow] (target {ats_threshold:.0f}%). "
                "Improve coverage automatically?",
                default=True,
            )
            if not improve:
                break

        # Build targeted re-tailoring instruction
        gap_instruction = (
            f"The current ATS score is {overall:.1f}%. "
            f"Improve the resume to better cover these missing keywords: {missing}. "
            "Focus specifically on the summary and experience bullets. "
            "Do NOT regenerate sections that already have good coverage. "
            "Keep all existing experience and project entries."
        )

        with spinner(f"Improving coverage (iteration {iteration + 1}/{max_iterations})..."):
            improve_result = asyncio.run(_run_agent(gap_instruction, session_id=session_id))

        new_tailored = improve_result.get("tailored")
        if new_tailored:
            format_resume_preview(new_tailored, name=profile.get("name", ""))


# ---------------------------------------------------------------------------
# Subcommands: profile
# ---------------------------------------------------------------------------

@cli.group()
def profile():
    """Manage the master profile."""
    pass


@profile.command("show")
def profile_show():
    """Display the current master profile."""
    from cli_formatter import format_profile_summary

    store = _get_storage()
    data = store.load_profile()
    if not data:
        console.print("[yellow]No profile set.[/yellow] Add one with: python cli.py profile update --file path/to/profile.json")
        return
    format_profile_summary(data)


@profile.command("update")
@click.option("--file", "profile_file", required=True, help="Path to master_profile.json")
def profile_update(profile_file):
    """Import a JSON profile into the database."""
    path = Path(profile_file)
    if not path.exists():
        console.print(f"[red]File not found:[/red] {profile_file}")
        sys.exit(1)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        console.print(f"[red]Invalid JSON:[/red] {e}")
        sys.exit(1)

    store = _get_storage()
    store.save_profile(data)
    console.print(f"[green]✓[/green] Profile saved to database. ({path.name})")


# ---------------------------------------------------------------------------
# Subcommands: jd (job description)
# ---------------------------------------------------------------------------

@cli.group()
def jd():
    """Manage the job description."""
    pass


@jd.command("set")
@click.option("--file", "jd_file", default=None, help="Path to job description file.")
@click.option("--paste", is_flag=True, default=False, help="Paste job description interactively.")
def jd_set(jd_file, paste):
    """Set the job description from a file or by pasting."""
    store = _get_storage()

    if jd_file:
        path = Path(jd_file)
        if not path.exists():
            console.print(f"[red]File not found:[/red] {jd_file}")
            sys.exit(1)
        content = path.read_text(encoding="utf-8").strip()
    elif paste:
        console.print("[bold]Paste the job description below. Press [Enter] twice to finish:[/bold]")
        lines = []
        blank_count = 0
        while blank_count < 2:
            line = input()
            if not line:
                blank_count += 1
            else:
                blank_count = 0
                lines.append(line)
        content = "\n".join(lines).strip()
    else:
        console.print("[red]Provide --file or --paste[/red]")
        sys.exit(1)

    if not content:
        console.print("[red]Empty job description.[/red]")
        sys.exit(1)

    store.save_job_description(content)
    console.print(f"[green]✓[/green] Job description saved ({len(content)} chars).")


@jd.command("show")
def jd_show():
    """Display the current job description."""
    store = _get_storage()
    content = store.load_job_description()
    if not content:
        console.print("[yellow]No job description set.[/yellow]")
        return
    console.print(f"[bold]Job Description[/bold] ({len(content)} chars):\n")
    console.print(content[:2000] + ("\n[dim]...[truncated][/dim]" if len(content) > 2000 else ""))


# ---------------------------------------------------------------------------
# Subcommands: template
# ---------------------------------------------------------------------------

@cli.group()
def template():
    """Manage LaTeX resume templates."""
    pass


@template.command("list")
def template_list():
    """List all template versions."""
    from rich.table import Table
    from rich import box as rich_box

    store = _get_storage()
    versions = store.list_templates()
    if not versions:
        console.print("[yellow]No templates in database.[/yellow] Add one with: python cli.py template add --file template.tex")
        return

    table = Table(box=rich_box.SIMPLE, show_header=True, header_style="bold")
    table.add_column("Version")
    table.add_column("Label")
    table.add_column("Created")
    table.add_column("Active")

    import datetime
    for v in versions:
        created = datetime.datetime.fromtimestamp(v.get("created_at", 0)).strftime("%Y-%m-%d %H:%M")
        active = "[green]✓ ACTIVE[/green]" if v.get("is_active") else ""
        table.add_row(str(v["version"]), v.get("label", ""), created, active)

    console.print(table)


@template.command("add")
@click.option("--file", "tex_file", required=True, help="Path to .tex template file.")
@click.option("--label", default="", help="Human-readable label for this version.")
def template_add(tex_file, label):
    """Add a new template version to the database."""
    path = Path(tex_file)
    if not path.exists():
        console.print(f"[red]File not found:[/red] {tex_file}")
        sys.exit(1)
    content = path.read_text(encoding="utf-8")
    store = _get_storage()
    version = store.save_template(content, label=label or path.stem)
    console.print(f"[green]✓[/green] Template saved as version {version} (now active).")


@template.command("activate")
@click.option("--version", "version_num", required=True, type=int, help="Version number to activate.")
def template_activate(version_num):
    """Set the active template version."""
    store = _get_storage()
    try:
        store.set_active_template(version_num)
        console.print(f"[green]✓[/green] Template version {version_num} is now active.")
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    cli()
