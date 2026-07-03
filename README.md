# AI Resume Builder Agent (Python Standalone)

This is a standalone Python application that uses Google's [ADK (Agent Development Kit)](https://google.github.io/adk-docs/) to automatically tailor a user's resume for a specific job description and compile it to a PDF using Jinja2 LaTeX.

All frontend (Next.js/Node.js) dependencies have been removed. The application runs locally via a command-line interface (CLI) or as a lightweight backend API using FastAPI.

## Prerequisites

- **Python 3.12+**
- **pdflatex** (Optional, required for PDF compilation. If not present, the system will output the tailored LaTeX `.tex` file but skip PDF compilation).
  - *Windows*: Install [MiKTeX](https://miktex.org/) or TeX Live.
  - *Mac*: `brew install --cask mactex-no-gui`
  - *Linux*: `sudo apt-get install texlive-latex-base texlive-latex-extra`
- **Google API Key**: Needed to run the underlying Gemini model for tailoring. Get one from [Google AI Studio](https://makersuite.google.com/app/apikey).

## Installation

We recommend using `uv` (a fast Python package installer) or a standard Python virtual environment.

### Set up Virtual Environment & Install Dependencies

```bash
# Navigate to agent directory
cd agent

# Create virtual environment
python -m venv .venv

# Activate virtual environment
# On Windows (cmd):
.venv\Scripts\activate.bat
# On Windows (PowerShell):
.venv\Scripts\Activate.ps1
# On Linux/macOS:
source .venv/bin/activate

# Install dependencies
pip install -r pyproject.toml
```

### Set up Environment Variables

Create or update the `.env` file in the root directory (or in the `agent/` directory):

```env
GOOGLE_API_KEY="your-google-api-key-here"
```

## Running the CLI

You can run the pipeline directly from the command line using the root-level `cli.py` script:

```bash
# Run with default files (loaded from the data/ folder)
python cli.py

# Run with custom input and output paths
python cli.py \
  --profile data/master_profile.json \
  --jd data/job_description.txt \
  --prefs data/preferences.json \
  --template data/resume_template.tex \
  --output-dir output/
```

This runs the ADK Orchestrator agent loop, performs structured tailoring, validates the results, and writes `resume.tex`, `resume.pdf`, and `tailored_resume.json` to the output folder.

## Running the API Server

You can also run the agent as a standalone backend server:

```bash
# Run the FastAPI server (starts on http://localhost:8000)
python agent/main.py
```

### Stateless API Endpoint: `POST /api/tailor`

This endpoint processes the resume tailoring and PDF compilation in a single, stateless request.

- **URL**: `http://localhost:8000/api/tailor`
- **Payload**:
  ```json
  {
    "master_profile": { ... },
    "job_description": "Paste JD text here",
    "preferences": {
      "experience_count": 3,
      "project_count": 2,
      "include_summary": true,
      "include_skills": true
    },
    "template_content": "LaTeX Jinja2 template string..."
  }
  ```
  *(Note: All fields are optional; if not provided, the server will load default fallbacks from the `data/` directory).*
- **Response**:
  ```json
  {
    "tailored_resume": { ... },
    "rendered_tex": "LaTeX document string",
    "compiled_pdf_base64": "Base64 encoded PDF bytes...",
    "success": true
  }
  ```

## Input File Formats

- **data/master_profile.json**: Your complete career history (education, experiences, projects, skills).
- **data/job_description.txt**: The plain text description of the target job.
- **data/preferences.json**: Controls the count of items in each section and any specific prompts (e.g. `"Focus on backend development"`).
- **data/resume_template.tex**: The LaTeX resume template using Jinja2 syntax (supports standard `{{ }}` and LaTeX-safe `\VAR{}`).
