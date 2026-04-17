"""
Resume Builder Multi-Agent System with ADK.

Architecture:
  - Orchestrator (Gemma-4-31B-it): Content strategist that coordinates the
    tailoring workflow. Fetches data, delegates to sub-agents, communicates
    with the user.
  - Tailoring Agent (Nemotron-3-Super-120B): Specialist that drives the
    structured generation engine to produce a Pydantic-validated tailored
    resume from the Master Profile + JD.
  - Compilation Agent: Handles rendering the Jinja2 LaTeX template with
    the tailored data and compiling to PDF.

The LLM never touches raw LaTeX. All formatting is handled by the Python
Jinja2 bridge.
"""

import logging
import json
from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_response import LlmResponse
from google.adk.models.lite_llm import LiteLlm
from google.genai import types
from pydantic import ValidationError
from schemas import TailoredResume, SectionPreferences
import os


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def create_agent():
    # --- Models -----------------------------------------------------------
    # Orchestrator: Gemma-4-31B-it — frontier reasoning, great tool use
    orchestrator_model = os.getenv("ORCHESTRATOR_MODEL", "gemini/gemma-4-31b-it")
    gemma_brain = LiteLlm(
        model=orchestrator_model,
        api_key=os.getenv("GOOGLE_API_KEY"),
        num_retries=3,
    )

    generator_provider = os.getenv("GENERATOR_PROVIDER", "gemini")
    generator_model = os.getenv("GENERATOR_MODEL", "gemini/gemma-4-26b-a4b-it")

    api_key = os.getenv("GOOGLE_API_KEY") if generator_provider == "gemini" else os.getenv("NVIDIA_API_KEY")

    tailoring_brain = LiteLlm(
        model=generator_model,
        api_key=api_key,
        num_retries=3,
    )

    # --- Tools ------------------------------------------------------------
    from tools import (
        render_latex,
        read_state,
        get_all_context,
        submit_tailored_resume,
        capture_tailored_output,
        enforce_preferences,
    )

    # The tailoring_agent uses its own tools to fetch context when needed.


    # --- Sub-Agent: Tailoring Engine --------------------------------------
    tailoring_instructions = """\
        You are an expert resume tailoring engine. Your task is to produce a structured resume payload and submit it using the provided tool.

        ## Workflow

        1. **Fetch Context**: Call `get_all_context` ONCE to load the master profile, job description, and user preferences.
        2. **Analyze and Draft**: Analyze the job description against the master profile. Identify the most relevant experiences, projects, and skills based on the user's quantity preferences.
        3. **Format**: Draft the resume content according to these rules:
            - Always highlight numbers in bullet points.
            - Start each bullet point with a 2-4 word high-level description in bold, e.g., "**Design and Implementation:** ...".
            - Wrap key phrases in **double asterisks**.
            - Do NOT fabricate any experience or skills.
        4. **Submit**: Call the `submit_tailored_resume` tool with the finalized data. 
        - If the tool returns a validation error, analyze the error message, fix the specific fields, and call the tool again with the FULL corrected payload.

        ## Constraints
        - Do NOT output raw JSON in your response text. ONLY use the `submit_tailored_resume` tool to submit the result.
        - Ensure all required fields (summary, experience, skills) are present in the payload.
        - Adhere strictly to the quantity limits provided in the context.
        """

    # --- Sub-Agent: Tailoring Engine --------------------------------------
    tailoring_agent = Agent(
        model=tailoring_brain,
        name="tailoring_agent",
        description=(
            "A specialist sub-agent for ALL resume content generation and updates. "
            "Delegate to this agent when you need to produce a tailored resume, "
            "make changes to existing content, update bullet points, or re-generate "
            "portions of the resume. It uses a validation-based submission tool."
        ),
        instruction=tailoring_instructions,
        # Force SingleFlow: prevents ADK from injecting transfer_to_agent tool.
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
        after_model_callback=capture_tailored_output,
        after_agent_callback=enforce_preferences,
        generate_content_config=types.GenerateContentConfig(
            temperature=0.8
        ),
        tools=[get_all_context, submit_tailored_resume],
    )

    # --- Sub-Agent: LaTeX Rendering Agent -----------------------------------
    compilation_agent = Agent(
        model=gemma_brain,
        name="compilation_agent",
        description=(
            "A specialist sub-agent for rendering the final LaTeX resume ONLY. "
            "Delegate to this agent when the content is finalized and the user wants "
            "to prepare the resume for download. It renders the Jinja2 LaTeX template "
            "and stores the .tex file — PDF compilation happens automatically when the "
            "user clicks the Download PDF button."
        ),
        instruction="""\
        You are a LaTeX rendering specialist. Your ONLY job is to render the resume to LaTeX.

        ## Workflow
        1. Call `read_state` first to verify that `tailored_resume` is present in state.
        - If `tailored_resume` is missing or null, respond:
            "Tailored resume data is not in state. Tailoring must be run first."
            Do NOT call `render_latex`. Return immediately.
        - If `tailored_resume` is present, proceed.
        2. Call `render_latex` to render the Jinja2 template and store the .tex file.
        3. Report the result: success with the hash, or any errors.

        ## Constraints
        - You do NOT modify any content or template.
        - You do NOT compile PDFs — that happens automatically when the user downloads.
        - You ONLY call the tools and report results.
        - If rendering fails, report the error with details.
        """,
        generate_content_config=types.GenerateContentConfig(
            temperature=0.1,
        ),
        tools=[read_state, render_latex],
    )

    # --- Root Agent: Orchestrator ----------------------------------------
    root_agent = Agent(
        model=gemma_brain,
        name="resume_builder_agent",
        description=(
            "An expert AI agent that tailors resumes to job descriptions using "
            "a structured data pipeline."
        ),
        instruction="""\
        ## PRIME DIRECTIVE
        You are an Expert Resume Strategist and Orchestrator. You help users tailor their
        resume for specific job descriptions using a structured pipeline.

        **You never write LaTeX code.** All formatting is handled automatically by the
        template engine.

        ## How the System Works
        1. The user has a **Master Profile** (comprehensive career data) stored in the database.
        2. The user provides a **Job Description** in the context panel.
        3. The user configures **Section Preferences** (how many experiences, projects, etc.) in the UI.
        4. You orchestrate the pipeline: analyze data → generate tailored content → compile PDF.

        ## Workflow

        ### 1. Analyze & Preview
        The tailoring agent will automatically fetch the master profile, job description, and user preferences when you delegate to it.
        You do NOT need to run context fetching tools yourself. 
        Just ask the user what kind of role they want to target, and let them know you'll delegate to the sub-agent to use their master profile and job description to tailor the payload.

        Use `read_state` only if you want to inspect what was saved in the state after tools have been run.

        ### 2. Generate via Sub-Agent
        Delegate to `tailoring_agent`.
        The sub-agent will natively generate and return a complete `TailoredResume` JSON.
        ADK will automatically place the result securely into state.

        **CRITICAL — After delegation, ALWAYS call `read_state` to verify the result.**
        If `tailored_resume` is present in state, generation succeeded.
        - How many experiences, projects, achievements were selected
        - Key highlights from the tailored summary
        - Ask if they want to review or adjust anything or you can compile the resume

        If not present tell user to retry

        ### 4. Render LaTeX via Sub-Agent
        When the user specifically says "Compile PDF", "Looks good", "Generate PDF", "Download", or "Prepare my resume":
        1. FIRST call `read_state` to verify `tailored_resume` is present in state.
        - If it is missing: do NOT delegate to `compilation_agent`. Instead, tell the user
            "I need to generate your tailored resume first" and delegate to `tailoring_agent`.
        - If it is present: proceed to step 2.
        2. Delegate to `compilation_agent` to render the Jinja2 template and store the .tex.
        3. Tell the user the resume is ready — they can click **Download PDF** or **Download LaTeX** from the header.

        ### 5. Iterate & Update
        If the user wants ANY changes to the content (e.g., "update the summary", "change the bullet points", "more/less detail"):
        1. Delegate to `tailoring_agent`. 
        2. The `tailoring_agent` will fetch fresh context and user preferences and re-generate the payload.
        3. NEVER delegte to `compilation_agent` for content updates.

        ## Constraints
        1. **NO INTERNAL MONOLOGUE** — Never expose reasoning or chain of thought.
        2. **NO LaTeX** — Never write or display LaTeX code.
        3. **NO FABRICATION** — Do not invent experiences or skills not in the Master Profile.
        4. **DOMAIN DELEGATION** — Use `tailoring_agent` for all content changes and `compilation_agent` ONLY for PDF compilation.
        5. **PLAIN TEXT** — Present all information in clean, readable plain text.
        """,
        generate_content_config=types.GenerateContentConfig(
            temperature=0.7,
        ),
        tools=[read_state],
        sub_agents=[tailoring_agent, compilation_agent],
    )

    return root_agent