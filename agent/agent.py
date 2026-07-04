"""
agent.py — Resume Builder Multi-Agent System (v2).

Architecture:
  - Orchestrator (Gemma-4-31B-it): Coordinates the tailoring workflow.
    Runs JD analysis, delegates to tailoring/compilation sub-agents,
    runs ATS scoring feedback loop, communicates with the user.
  - Tailoring Agent (Gemma-4-26B-A4B-it): Specialist for content generation.
    Produces TailoredSections (summary, experience, projects, skills) only.
    Never sees or produces static identity fields.
  - Compilation Agent: Renders the Jinja2 LaTeX template and compiles to PDF.

Token optimization:
  - Agent instructions are loaded once from .md files at startup.
  - get_all_context sends compressed, preference-pruned context to the LLM.
  - TailoredSections schema excludes static fields (~800 tokens saved per run).

Security:
  - Instructions are loaded from versioned .md files with guardrail sections.
  - Instruction validation at startup — fails fast if files are missing.
"""

import logging
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Ensure local imports (tools, schemas, adk_telemetry) can be resolved
agent_dir = str(Path(__file__).parent.resolve())
if agent_dir not in sys.path:
    sys.path.append(agent_dir)

# Load environment variables at startup
load_dotenv(Path(__file__).parent / ".env")
load_dotenv(Path(__file__).parent.parent / ".env")

from google.adk.agents import Agent
from google.adk.models.lite_llm import LiteLlm
from google.genai import types
from pydantic import ValidationError

from adk_telemetry import adk_before_tool, adk_after_tool

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Instruction loading — cached at module level (load once, not per-request)
# ---------------------------------------------------------------------------

_INSTRUCTIONS_DIR = Path(__file__).parent / "instructions"


def _load_instruction(filename: str) -> str:
    """
    Load an agent instruction from a versioned .md file.

    Args:
        filename: The .md file name (e.g. 'orchestrator.md').

    Returns:
        The instruction text.

    Raises:
        FileNotFoundError: If the instruction file does not exist.
        ValueError:        If the file is empty.
    """
    path = _INSTRUCTIONS_DIR / filename
    if not path.exists():
        raise FileNotFoundError(
            f"Agent instruction file not found: {path}. "
            "Ensure the agent/instructions/ directory is present and complete."
        )
    content = path.read_text(encoding="utf-8").strip()
    if not content:
        raise ValueError(
            f"Agent instruction file is empty: {path}. "
            "This is a configuration error — the agent cannot start without instructions."
        )
    logger.info("Loaded instruction file: %s (%d chars)", filename, len(content))
    return content


# Load all instructions at import time — fails fast if any are missing
try:
    _ORCHESTRATOR_INSTRUCTION = _load_instruction("orchestrator.md")
    _TAILORING_INSTRUCTION = _load_instruction("tailoring.md")
    _COMPILATION_INSTRUCTION = _load_instruction("compilation.md")
except (FileNotFoundError, ValueError) as _inst_err:
    logger.critical("FATAL: Failed to load agent instructions: %s", _inst_err)
    raise


# ---------------------------------------------------------------------------
# Agent factory
# ---------------------------------------------------------------------------

def create_agent() -> Agent:
    """
    Build and return the root orchestrator agent with all sub-agents configured.

    Models are configured via environment variables with sensible defaults.
    All instructions are loaded from .md files with security guardrails.
    """
    # ── Models ────────────────────────────────────────────────────────────
    orchestrator_model_id = os.getenv("ORCHESTRATOR_MODEL", "gemini/gemma-4-31b-it")
    tailoring_model_id = os.getenv("GENERATOR_MODEL", "gemini/gemma-4-26b-a4b-it")

    google_api_key = os.getenv("GOOGLE_API_KEY")
    if not google_api_key:
        raise EnvironmentError(
            "GOOGLE_API_KEY environment variable is not set. "
            "Please set it in your .env file."
        )

    generator_provider = os.getenv("GENERATOR_PROVIDER", "gemini")
    tailoring_api_key = (
        google_api_key if generator_provider == "gemini"
        else os.getenv("NVIDIA_API_KEY", "")
    )

    orchestrator_brain = LiteLlm(
        model=orchestrator_model_id,
        api_key=google_api_key,
        num_retries=3,
    )

    tailoring_brain = LiteLlm(
        model=tailoring_model_id,
        api_key=tailoring_api_key,
        num_retries=3,
    )

    # ── Import tools ──────────────────────────────────────────────────────
    from tools import (
        read_state,
        get_all_context,
        submit_tailored_sections,
        render_latex,
        capture_tailored_output,
        enforce_preferences,
    )
    from tools_analysis import (
        analyze_jd,
        score_ats_match,
        diff_resume,
    )

    # ── Sub-Agent: Tailoring Engine ───────────────────────────────────────
    tailoring_agent = Agent(
        model=tailoring_brain,
        name="tailoring_agent",
        description=(
            "Specialist sub-agent for ALL resume content generation and updates. "
            "Produces TailoredSections (summary, experience, projects, skills, achievements) ONLY. "
            "Delegate here for: new tailoring, bullet updates, summary changes, re-tailoring with gap-fill instructions."
        ),
        instruction=_TAILORING_INSTRUCTION,
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
        after_model_callback=capture_tailored_output,
        after_agent_callback=enforce_preferences,
        generate_content_config=types.GenerateContentConfig(
            temperature=0.7,
            max_output_tokens=2048,
        ),
        tools=[get_all_context, submit_tailored_sections],
        before_tool_callback=adk_before_tool,
        after_tool_callback=adk_after_tool,
    )

    # ── Sub-Agent: Compilation Engine ─────────────────────────────────────
    compilation_agent = Agent(
        model=orchestrator_brain,
        name="compilation_agent",
        description=(
            "Specialist sub-agent for rendering the LaTeX resume and compiling to PDF. "
            "Delegate here ONLY when tailored_sections is confirmed in state and the user "
            "wants to produce the final document."
        ),
        instruction=_COMPILATION_INSTRUCTION,
        generate_content_config=types.GenerateContentConfig(
            temperature=0.1,
            max_output_tokens=512,
        ),
        tools=[read_state, render_latex],
        before_tool_callback=adk_before_tool,
        after_tool_callback=adk_after_tool,
    )

    # ── Root Agent: Orchestrator ──────────────────────────────────────────
    root_agent = Agent(
        model=orchestrator_brain,
        name="resume_builder_agent",
        description=(
            "Expert AI agent that tailors resumes to job descriptions using a "
            "multi-agent pipeline with ATS scoring and iterative improvement."
        ),
        instruction=_ORCHESTRATOR_INSTRUCTION,
        generate_content_config=types.GenerateContentConfig(
            temperature=0.5,
            max_output_tokens=512,  # Orchestrator responses are concise
        ),
        tools=[read_state, analyze_jd, score_ats_match, diff_resume],
        sub_agents=[tailoring_agent, compilation_agent],
        before_tool_callback=adk_before_tool,
        after_tool_callback=adk_after_tool,
    )

    logger.info(
        "Agent created: orchestrator=%s, tailoring=%s",
        orchestrator_model_id, tailoring_model_id,
    )

    return root_agent


# Expose root_agent at module level for ADK API server loader
root_agent = create_agent()