#!/usr/bin/env python3
"""
Command Line Interface for the Standalone Resume Builder Agent.
Allows running the tailoring and LaTeX compilation pipeline directly from the terminal.
"""

import os
import sys
import argparse
import asyncio
import json
import logging
import uuid
from dotenv import load_dotenv

# Ensure the agent directory is in the import path
sys.path.append(os.path.join(os.path.dirname(__file__), "agent"))

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("resume-cli")

async def run_tailoring(args):
    # Load environment variables from root .env and agent/.env
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
    load_dotenv(os.path.join(os.path.dirname(__file__), "agent", ".env"))

    # Set up input and output paths in the environment for tools.py
    os.environ["MASTER_PROFILE_PATH"] = os.path.abspath(args.profile)
    os.environ["JOB_DESCRIPTION_PATH"] = os.path.abspath(args.jd)
    os.environ["PREFERENCES_PATH"] = os.path.abspath(args.prefs)
    os.environ["RESUME_TEMPLATE_PATH"] = os.path.abspath(args.template)
    os.environ["OUTPUT_DIR"] = os.path.abspath(args.output_dir)

    # Force local mode by clearing FRONTEND_URL in environment
    if "FRONTEND_URL" in os.environ:
        del os.environ["FRONTEND_URL"]

    # Verify input files exist
    for label, path_env in [
        ("Master Profile", "MASTER_PROFILE_PATH"),
        ("Job Description", "JOB_DESCRIPTION_PATH"),
        ("Preferences", "PREFERENCES_PATH"),
        ("Resume Template", "RESUME_TEMPLATE_PATH"),
    ]:
        path = os.environ[path_env]
        if not os.path.exists(path):
            logger.error("%s file not found at: %s", label, path)
            sys.exit(1)

    print("=" * 60)
    print("STARTING RESUME TAILORING PIPELINE")
    print("=" * 60)
    print(f"Profile:   {os.environ['MASTER_PROFILE_PATH']}")
    print(f"JD:        {os.environ['JOB_DESCRIPTION_PATH']}")
    print(f"Prefs:     {os.environ['PREFERENCES_PATH']}")
    print(f"Template:  {os.environ['RESUME_TEMPLATE_PATH']}")
    print(f"Output:    {os.environ['OUTPUT_DIR']}")
    print("-" * 60)

    # Import agent modules
    try:
        from agent import create_agent
        from google.adk import Runner
        from google.adk.sessions.in_memory_session_service import InMemorySessionService
        from google.genai import types
    except ImportError as e:
        logger.error("Failed to import agent dependencies. Make sure virtualenv is activated or dependencies are installed: %s", e)
        sys.exit(1)

    agent = create_agent()
    session_service = InMemorySessionService()

    runner = Runner(
        app_name="resume_builder_agent",
        agent=agent,
        session_service=session_service,
        auto_create_session=True,
    )

    session_id = str(uuid.uuid4())
    user_id = f"thread_user_{session_id}"

    # Construct the instruction content for the runner
    # This instructs the Orchestrator Gemma model to run both tailoring and compilation
    instruction_text = (
        "Tailor my resume for the job description and compile the final PDF. "
        "Run the full pipeline (tailoring via tailoring_agent, then compile PDF via compilation_agent)."
    )
    new_message = types.Content(parts=[types.Part.from_text(text=instruction_text)])

    print("Running AI agent pipeline (this may take 1-2 minutes)...")
    
    try:
        async for event in runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=new_message,
        ):
            event_type = type(event).__name__
            # Clean logging of events
            if "ToolCallStart" in event_type or "ToolCall" in event_type:
                tool_name = getattr(event, "name", None) or getattr(event, "tool_name", "tool")
                print(f"⚙️  Executing tool: {tool_name}...")
            elif "Message" in event_type and hasattr(event, "content"):
                print(f"💬 Agent: {event.content}")
            elif "Error" in event_type:
                print(f"❌ Error: {event}")
            else:
                logger.debug("Event: %s", event_type)

        # Retrieve the final tailored resume from state
        session = await session_service.get_session(
            app_name="resume_builder_agent",
            user_id=user_id,
            session_id=session_id,
        )
        tailored_resume = session.state.get("tailored_resume") if session else None

        if not tailored_resume:
            # Check if files were created anyway
            tex_file = os.path.join(args.output_dir, "resume.tex")
            if os.path.exists(tex_file):
                print("-" * 60)
                print("🎉 SUCCESS: Resume files generated successfully (from fallback state)!")
                print(f"Source LaTeX: {tex_file}")
                pdf_file = os.path.join(args.output_dir, "resume.pdf")
                if os.path.exists(pdf_file):
                    print(f"Compiled PDF: {pdf_file}")
                sys.exit(0)
            else:
                print("❌ ERROR: Tailoring completed but no tailored resume payload was found in the session state.")
                sys.exit(1)

        print("-" * 60)
        print("🎉 SUCCESS: Resume tailored successfully!")
        
        # Verify output files
        tex_file = os.path.join(args.output_dir, "resume.tex")
        pdf_file = os.path.join(args.output_dir, "resume.pdf")
        
        # If the compilation agent failed or was skipped, render it now manually
        if not os.path.exists(tex_file):
            print("LaTeX output not found. Performing manual render...")
            from latex_bridge import render_resume, compile_pdf
            from schemas import TailoredResume
            
            validated_resume = TailoredResume(**tailored_resume)
            with open(os.environ["RESUME_TEMPLATE_PATH"], "r", encoding="utf-8") as f:
                template_content = f.read()
                
            rendered_tex = render_resume(template_content, validated_resume)
            os.makedirs(args.output_dir, exist_ok=True)
            with open(tex_file, "w", encoding="utf-8", newline="") as f:
                f.write(rendered_tex)
            print(f"Saved LaTeX: {tex_file}")
            
            try:
                pdf_file = compile_pdf(rendered_tex, args.output_dir)
                print(f"Compiled PDF: {pdf_file}")
            except Exception as e:
                print(f"⚠️  PDF compilation skipped/failed: {e}")
                print("Make sure 'pdflatex' is installed and available in your system path.")
        else:
            print(f"Source LaTeX: {tex_file}")
            if os.path.exists(pdf_file):
                print(f"Compiled PDF: {pdf_file}")
            else:
                print("⚠️  PDF file not found. Ensure 'pdflatex' is installed to compile PDF.")

        # Save tailored JSON payload as well
        json_file = os.path.join(args.output_dir, "tailored_resume.json")
        with open(json_file, "w", encoding="utf-8") as f:
            json.dump(tailored_resume, f, indent=2)
        print(f"JSON Data:    {json_file}")
        
    except Exception as e:
        logger.exception("An error occurred during agent run: %s", e)
        sys.exit(1)

def main():
    parser = argparse.ArgumentParser(description="Standalone Resume Builder CLI")
    parser.add_argument(
        "--profile",
        default="data/master_profile.json",
        help="Path to master_profile.json (default: data/master_profile.json)",
    )
    parser.add_argument(
        "--jd",
        default="data/job_description.txt",
        help="Path to job_description.txt (default: data/job_description.txt)",
    )
    parser.add_argument(
        "--prefs",
        default="data/preferences.json",
        help="Path to preferences.json (default: data/preferences.json)",
    )
    parser.add_argument(
        "--template",
        default="data/resume_template.tex",
        help="Path to resume_template.tex (default: data/resume_template.tex)",
    )
    parser.add_argument(
        "--output-dir",
        default="output",
        help="Directory to save output files (default: output)",
    )

    args = parser.parse_args()
    asyncio.run(run_tailoring(args))

if __name__ == "__main__":
    main()
