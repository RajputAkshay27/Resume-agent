#!/usr/bin/env python3
"""
profile_manager.py — Tkinter desktop GUI for the Resume Builder Agent v2.

A lightweight 4-tab desktop application for managing:
  Tab 1: Profile Editor  — Edit master profile (identity, experience, projects, skills).
  Tab 2: Job Description — Set and preview the current job description.
  Tab 3: Templates       — View, add, and activate LaTeX template versions.
  Tab 4: Quick Tailor    — Run the tailoring pipeline and view results.

All data is persisted to SQLite via storage_client. No filesystem access required
for normal operation — use "Import from JSON" for one-time migration.

Security:
  - No eval() or exec() on user-provided data.
  - Input validation before saving to SQLite.
  - Agent pipeline runs in a background thread to keep the GUI responsive.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk
from typing import Optional

# Ensure agent directory is importable
sys.path.insert(0, str(Path(__file__).parent / "agent"))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")
load_dotenv(Path(__file__).parent / "agent" / ".env")

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.WARNING)


# ---------------------------------------------------------------------------
# GUI Bridge — connects GUI to storage_client and agent pipeline
# ---------------------------------------------------------------------------

class GUIBridge:
    """
    Bridge between the Tkinter GUI and the storage + agent pipeline.

    All storage operations are synchronous (SQLite).
    Agent pipeline runs in a background thread via run_pipeline_async().
    """

    def __init__(self):
        from storage_client import storage
        self._store = storage()

    # ── Profile ─────────────────────────────────────────────────────────────

    def load_profile(self) -> dict:
        return self._store.load_profile() or {}

    def save_profile(self, profile: dict) -> None:
        self._store.save_profile(profile)

    # ── Job Description ──────────────────────────────────────────────────────

    def load_jd(self) -> str:
        return self._store.load_job_description() or ""

    def save_jd(self, jd: str) -> None:
        self._store.save_job_description(jd)

    # ── Templates ─────────────────────────────────────────────────────────────

    def list_templates(self) -> list[dict]:
        return self._store.list_templates()

    def save_template(self, content: str, label: str = "") -> int:
        return self._store.save_template(content, label=label)

    def set_active_template(self, version: int) -> None:
        self._store.set_active_template(version)

    def load_template(self, version: Optional[int] = None) -> Optional[str]:
        return self._store.load_template(version)

    # ── Agent Pipeline ────────────────────────────────────────────────────────

    def run_pipeline_async(self, instruction: str, on_done, on_error) -> None:
        """
        Run the agent pipeline in a background thread.

        Args:
            instruction: The natural language instruction to send to the agent.
            on_done:     Callback(result: dict) called on success.
            on_error:    Callback(error: str) called on failure.
        """
        def _run():
            try:
                import asyncio
                import uuid
                from agent import create_agent
                from google.adk import Runner
                from google.adk.sessions.in_memory_session_service import InMemorySessionService
                from google.genai import types

                agent = create_agent()
                session_service = InMemorySessionService()
                runner = Runner(
                    app_name="resume_builder_agent",
                    agent=agent,
                    session_service=session_service,
                    auto_create_session=True,
                )
                session_id = str(uuid.uuid4())
                user_id = f"gui_user_{session_id}"

                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

                async def _async_run():
                    async for _ in runner.run_async(
                        user_id=user_id,
                        session_id=session_id,
                        new_message=types.Content(parts=[types.Part.from_text(text=instruction)]),
                    ):
                        pass
                    return await session_service.get_session(
                        app_name="resume_builder_agent",
                        user_id=user_id,
                        session_id=session_id,
                    )

                session = loop.run_until_complete(_async_run())
                loop.close()

                tailored = None
                if session:
                    tailored = session.state.get("tailored_sections") or session.state.get("tailored_resume")

                output_dir = os.getenv("OUTPUT_DIR", "output")
                tex_path = os.path.join(output_dir, "resume.tex") if os.path.exists(os.path.join(output_dir, "resume.tex")) else None
                pdf_path = os.path.join(output_dir, "resume.pdf") if os.path.exists(os.path.join(output_dir, "resume.pdf")) else None

                on_done({
                    "tailored": tailored,
                    "tex_path": tex_path,
                    "pdf_path": pdf_path,
                })
            except Exception as e:
                logger.exception("Pipeline error: %s", e)
                on_error(str(e))

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()


# ---------------------------------------------------------------------------
# Main Application Window
# ---------------------------------------------------------------------------

class ProfileManagerApp(tk.Tk):
    """Main Tkinter application with a 4-tab notebook."""

    def __init__(self):
        super().__init__()
        self.title("Resume Builder — Profile Manager")
        self.geometry("1024x700")
        self.minsize(800, 500)
        self.configure(bg="#1e1e2e")

        self._bridge = GUIBridge()
        self._setup_style()
        self._build_ui()
        self._load_initial_data()

    # ── Style ─────────────────────────────────────────────────────────────────

    def _setup_style(self):
        style = ttk.Style(self)
        style.theme_use("clam")

        BG = "#1e1e2e"
        FG = "#cdd6f4"
        ACCENT = "#89b4fa"
        PANEL_BG = "#313244"
        ENTRY_BG = "#45475a"

        style.configure(".", background=BG, foreground=FG, font=("Segoe UI", 10))
        style.configure("TNotebook", background=BG, tabmargins=[4, 4, 0, 0])
        style.configure("TNotebook.Tab", background=PANEL_BG, foreground=FG,
                         padding=[14, 6], font=("Segoe UI", 10, "bold"))
        style.map("TNotebook.Tab",
                  background=[("selected", ACCENT)],
                  foreground=[("selected", "#1e1e2e")])
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=FG, font=("Segoe UI", 10))
        style.configure("TButton", background=ACCENT, foreground="#1e1e2e",
                         font=("Segoe UI", 10, "bold"), relief="flat", padding=[10, 5])
        style.map("TButton",
                  background=[("active", "#b4befe"), ("disabled", PANEL_BG)],
                  foreground=[("disabled", "#6c7086")])
        style.configure("TEntry", fieldbackground=ENTRY_BG, foreground=FG,
                         insertcolor=FG, relief="flat")
        style.configure("Treeview", background=PANEL_BG, foreground=FG,
                         fieldbackground=PANEL_BG, rowheight=26)
        style.configure("Treeview.Heading", background=ACCENT, foreground="#1e1e2e",
                         font=("Segoe UI", 10, "bold"))
        style.map("Treeview", background=[("selected", ACCENT)])
        style.configure("TLabelframe", background=BG, foreground=FG)
        style.configure("TLabelframe.Label", background=BG, foreground=ACCENT,
                         font=("Segoe UI", 10, "bold"))

        self.BG = BG
        self.FG = FG
        self.ACCENT = ACCENT
        self.PANEL_BG = PANEL_BG
        self.ENTRY_BG = ENTRY_BG

    # ── UI Layout ─────────────────────────────────────────────────────────────

    def _build_ui(self):
        # Header
        header = tk.Frame(self, bg="#181825", pady=10)
        header.pack(fill="x")
        tk.Label(
            header,
            text="⚡ Resume Builder — Profile Manager",
            bg="#181825", fg=self.ACCENT,
            font=("Segoe UI", 14, "bold"),
        ).pack(side="left", padx=20)

        # Notebook
        self._notebook = ttk.Notebook(self)
        self._notebook.pack(fill="both", expand=True, padx=12, pady=10)

        self._tab_profile = ttk.Frame(self._notebook)
        self._tab_jd = ttk.Frame(self._notebook)
        self._tab_templates = ttk.Frame(self._notebook)
        self._tab_tailor = ttk.Frame(self._notebook)

        self._notebook.add(self._tab_profile, text="  📋 Profile  ")
        self._notebook.add(self._tab_jd, text="  💼 Job Description  ")
        self._notebook.add(self._tab_templates, text="  📄 Templates  ")
        self._notebook.add(self._tab_tailor, text="  🚀 Quick Tailor  ")

        self._build_profile_tab()
        self._build_jd_tab()
        self._build_templates_tab()
        self._build_tailor_tab()

    # ── Tab 1: Profile Editor ─────────────────────────────────────────────────

    def _build_profile_tab(self):
        frame = self._tab_profile
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)

        # Action bar
        actions = ttk.Frame(frame)
        actions.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 0))
        ttk.Button(actions, text="Import JSON", command=self._import_profile).pack(side="left", padx=4)
        ttk.Button(actions, text="Export JSON", command=self._export_profile).pack(side="left", padx=4)
        ttk.Button(actions, text="Save to Database", command=self._save_profile, style="TButton").pack(side="right", padx=4)

        # Scrollable form area
        canvas = tk.Canvas(frame, bg=self.BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=canvas.yview)
        self._profile_form = ttk.Frame(canvas)
        self._profile_form.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self._profile_form, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.grid(row=1, column=0, sticky="nsew")
        scrollbar.grid(row=1, column=1, sticky="ns")

        self._profile_vars = {}
        self._build_profile_form()

    def _labeled_entry(self, parent, row, label, var_key, colspan=1) -> ttk.Entry:
        """Helper to add a labeled entry field to the profile form."""
        ttk.Label(parent, text=label + ":").grid(row=row, column=0, sticky="w", padx=8, pady=3)
        var = tk.StringVar()
        entry = ttk.Entry(parent, textvariable=var, width=60)
        entry.grid(row=row, column=1, sticky="ew", padx=8, pady=3, columnspan=colspan)
        self._profile_vars[var_key] = var
        return entry

    def _build_profile_form(self):
        form = self._profile_form
        form.columnconfigure(1, weight=1)

        # Identity section
        id_frame = ttk.LabelFrame(form, text="Identity")
        id_frame.grid(row=0, column=0, columnspan=2, sticky="ew", padx=8, pady=8)
        id_frame.columnconfigure(1, weight=1)
        self._labeled_entry(id_frame, 0, "Full Name", "name")
        self._labeled_entry(id_frame, 1, "Email", "email")
        self._labeled_entry(id_frame, 2, "Phone", "phone")
        self._labeled_entry(id_frame, 3, "LinkedIn", "linkedin")
        self._labeled_entry(id_frame, 4, "GitHub", "github")
        self._labeled_entry(id_frame, 5, "Website", "website")

        # Raw JSON editor for complex sections
        json_frame = ttk.LabelFrame(form, text="Full Profile JSON (Experience, Education, Projects, Skills)")
        json_frame.grid(row=1, column=0, columnspan=2, sticky="ew", padx=8, pady=8)
        self._profile_json_editor = scrolledtext.ScrolledText(
            json_frame, height=20, font=("Consolas", 9),
            bg=self.ENTRY_BG, fg=self.FG, insertbackground=self.FG,
            relief="flat", wrap="none",
        )
        self._profile_json_editor.pack(fill="both", expand=True, padx=4, pady=4)

    def _load_profile_into_form(self, profile: dict):
        for key in ("name", "email", "phone", "linkedin", "github", "website"):
            if key in self._profile_vars:
                self._profile_vars[key].set(profile.get(key, ""))
        self._profile_json_editor.delete("1.0", "end")
        self._profile_json_editor.insert("1.0", json.dumps(profile, indent=2))

    def _collect_profile_from_form(self) -> dict:
        try:
            raw = self._profile_json_editor.get("1.0", "end").strip()
            profile = json.loads(raw) if raw else {}
        except json.JSONDecodeError as e:
            messagebox.showerror("JSON Error", f"Invalid JSON in profile editor:\n{e}")
            return {}
        for key in ("name", "email", "phone", "linkedin", "github", "website"):
            if key in self._profile_vars:
                val = self._profile_vars[key].get().strip()
                if val:
                    profile[key] = val
        return profile

    def _import_profile(self):
        path = filedialog.askopenfilename(
            title="Import Master Profile",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            self._load_profile_into_form(data)
            self._status("Profile imported from file. Click 'Save to Database' to persist.")
        except Exception as e:
            messagebox.showerror("Import Error", str(e))

    def _export_profile(self):
        profile = self._collect_profile_from_form()
        if not profile:
            return
        path = filedialog.asksaveasfilename(
            title="Export Profile",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json")],
        )
        if path:
            Path(path).write_text(json.dumps(profile, indent=2), encoding="utf-8")
            self._status(f"Profile exported to {path}")

    def _save_profile(self):
        profile = self._collect_profile_from_form()
        if not profile:
            return
        try:
            self._bridge.save_profile(profile)
            self._status("✓ Profile saved to database.")
            messagebox.showinfo("Saved", "Profile saved to database successfully.")
        except Exception as e:
            messagebox.showerror("Save Error", str(e))

    # ── Tab 2: Job Description ─────────────────────────────────────────────────

    def _build_jd_tab(self):
        frame = self._tab_jd
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)

        # Action bar
        actions = ttk.Frame(frame)
        actions.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 0))
        ttk.Button(actions, text="Load from File", command=self._load_jd_from_file).pack(side="left", padx=4)
        ttk.Button(actions, text="Clear", command=self._clear_jd).pack(side="left", padx=4)
        ttk.Button(actions, text="Save", command=self._save_jd).pack(side="right", padx=4)

        # Text area
        self._jd_text = scrolledtext.ScrolledText(
            frame, font=("Segoe UI", 10),
            bg=self.ENTRY_BG, fg=self.FG, insertbackground=self.FG,
            relief="flat", wrap="word",
        )
        self._jd_text.grid(row=1, column=0, sticky="nsew", padx=8, pady=8)

        # Char count label
        self._jd_char_label = ttk.Label(frame, text="0 characters")
        self._jd_char_label.grid(row=2, column=0, sticky="e", padx=12)
        self._jd_text.bind("<KeyRelease>", self._update_jd_char_count)

    def _load_jd_from_file(self):
        path = filedialog.askopenfilename(
            title="Load Job Description",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if path:
            content = Path(path).read_text(encoding="utf-8")
            self._jd_text.delete("1.0", "end")
            self._jd_text.insert("1.0", content)
            self._update_jd_char_count()

    def _save_jd(self):
        content = self._jd_text.get("1.0", "end").strip()
        if not content:
            messagebox.showwarning("Empty", "Job description is empty.")
            return
        try:
            self._bridge.save_jd(content)
            self._status(f"✓ Job description saved ({len(content)} chars).")
            messagebox.showinfo("Saved", "Job description saved to database.")
        except Exception as e:
            messagebox.showerror("Save Error", str(e))

    def _clear_jd(self):
        if messagebox.askyesno("Clear", "Clear the job description?"):
            self._jd_text.delete("1.0", "end")
            self._update_jd_char_count()

    def _update_jd_char_count(self, event=None):
        n = len(self._jd_text.get("1.0", "end").strip())
        self._jd_char_label.config(text=f"{n:,} characters")

    # ── Tab 3: Templates ──────────────────────────────────────────────────────

    def _build_templates_tab(self):
        frame = self._tab_templates
        frame.columnconfigure(0, weight=2)
        frame.columnconfigure(1, weight=3)
        frame.rowconfigure(1, weight=1)

        # Action bar
        actions = ttk.Frame(frame)
        actions.grid(row=0, column=0, columnspan=2, sticky="ew", padx=8, pady=(8, 0))
        ttk.Button(actions, text="Add Template", command=self._add_template).pack(side="left", padx=4)
        ttk.Button(actions, text="Set Active", command=self._activate_template).pack(side="left", padx=4)
        ttk.Button(actions, text="Refresh", command=self._refresh_templates).pack(side="left", padx=4)
        ttk.Button(actions, text="Export", command=self._export_template).pack(side="right", padx=4)

        # Version list
        list_frame = ttk.LabelFrame(frame, text="Template Versions")
        list_frame.grid(row=1, column=0, sticky="nsew", padx=8, pady=8)
        self._template_tree = ttk.Treeview(
            list_frame,
            columns=("version", "label", "created", "active"),
            show="headings", selectmode="browse",
        )
        for col, w, text in [("version", 60, "Ver"), ("label", 120, "Label"),
                               ("created", 130, "Created"), ("active", 60, "Active")]:
            self._template_tree.heading(col, text=text)
            self._template_tree.column(col, width=w, anchor="center")
        self._template_tree.pack(fill="both", expand=True, padx=4, pady=4)
        self._template_tree.bind("<<TreeviewSelect>>", self._on_template_select)

        # Preview area
        preview_frame = ttk.LabelFrame(frame, text="Template Preview")
        preview_frame.grid(row=1, column=1, sticky="nsew", padx=8, pady=8)
        self._template_preview = scrolledtext.ScrolledText(
            preview_frame, font=("Consolas", 9),
            bg=self.ENTRY_BG, fg=self.FG, insertbackground=self.FG,
            relief="flat", state="disabled",
        )
        self._template_preview.pack(fill="both", expand=True, padx=4, pady=4)

    def _refresh_templates(self):
        self._template_tree.delete(*self._template_tree.get_children())
        import datetime
        for v in self._bridge.list_templates():
            created = datetime.datetime.fromtimestamp(v.get("created_at", 0)).strftime("%Y-%m-%d %H:%M")
            active = "✓" if v.get("is_active") else ""
            self._template_tree.insert("", "end", iid=str(v["version"]), values=(
                v["version"], v.get("label", ""), created, active
            ))

    def _on_template_select(self, event=None):
        selected = self._template_tree.selection()
        if not selected:
            return
        version = int(selected[0])
        content = self._bridge.load_template(version) or ""
        self._template_preview.configure(state="normal")
        self._template_preview.delete("1.0", "end")
        self._template_preview.insert("1.0", content)
        self._template_preview.configure(state="disabled")

    def _add_template(self):
        path = filedialog.askopenfilename(
            title="Add LaTeX Template",
            filetypes=[("TeX files", "*.tex"), ("All files", "*.*")],
        )
        if not path:
            return
        label = Path(path).stem
        content = Path(path).read_text(encoding="utf-8")
        try:
            version = self._bridge.save_template(content, label=label)
            self._refresh_templates()
            self._status(f"✓ Template version {version} added and set as active.")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _activate_template(self):
        selected = self._template_tree.selection()
        if not selected:
            messagebox.showwarning("Select", "Please select a template version first.")
            return
        version = int(selected[0])
        try:
            self._bridge.set_active_template(version)
            self._refresh_templates()
            self._status(f"✓ Template version {version} is now active.")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _export_template(self):
        selected = self._template_tree.selection()
        if not selected:
            messagebox.showwarning("Select", "Please select a template version first.")
            return
        version = int(selected[0])
        content = self._bridge.load_template(version) or ""
        path = filedialog.asksaveasfilename(
            title="Export Template",
            defaultextension=".tex",
            initialfile=f"template_v{version}.tex",
            filetypes=[("TeX files", "*.tex")],
        )
        if path:
            Path(path).write_text(content, encoding="utf-8")
            self._status(f"Template version {version} exported to {path}")

    # ── Tab 4: Quick Tailor ───────────────────────────────────────────────────

    def _build_tailor_tab(self):
        frame = self._tab_tailor
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(2, weight=1)

        # Controls
        controls = ttk.LabelFrame(frame, text="Controls")
        controls.grid(row=0, column=0, sticky="ew", padx=8, pady=8)
        controls.columnconfigure(1, weight=1)

        ttk.Button(controls, text="▶  Run Tailoring", command=self._run_tailoring).grid(
            row=0, column=0, padx=8, pady=6)
        ttk.Button(controls, text="📄  Compile PDF", command=self._compile_pdf).grid(
            row=0, column=1, padx=8, pady=6, sticky="w")

        self._tailor_open_btn = ttk.Button(controls, text="🔍  Open PDF", command=self._open_pdf, state="disabled")
        self._tailor_open_btn.grid(row=0, column=2, padx=8, pady=6)

        self._tailor_progress = ttk.Label(controls, text="Ready", foreground=self.ACCENT)
        self._tailor_progress.grid(row=1, column=0, columnspan=3, padx=8, pady=(0, 6), sticky="w")

        # Output area
        output_frame = ttk.LabelFrame(frame, text="Agent Output")
        output_frame.grid(row=2, column=0, sticky="nsew", padx=8, pady=8)
        self._tailor_output = scrolledtext.ScrolledText(
            output_frame, font=("Segoe UI", 10),
            bg=self.ENTRY_BG, fg=self.FG, insertbackground=self.FG,
            relief="flat", state="disabled", wrap="word",
        )
        self._tailor_output.pack(fill="both", expand=True, padx=4, pady=4)

        self._last_pdf_path: Optional[str] = None

    def _set_tailor_output(self, text: str):
        self._tailor_output.configure(state="normal")
        self._tailor_output.delete("1.0", "end")
        self._tailor_output.insert("1.0", text)
        self._tailor_output.configure(state="disabled")

    def _run_tailoring(self):
        self._tailor_progress.config(text="⏳ Tailoring... (this may take 1-2 minutes)")
        self._set_tailor_output("Running agent pipeline...")

        instruction = (
            "Analyze the job description, then tailor my resume for it. "
            "Run the full pipeline: analyze JD, tailor content, score ATS match."
        )

        def on_done(result: dict):
            tailored = result.get("tailored")
            if tailored:
                preview = f"✓ Tailoring complete!\n\nSummary:\n{tailored.get('summary', '')[:300]}\n\n"
                preview += f"Experience entries: {len(tailored.get('experience', []))}\n"
                preview += f"Projects: {len(tailored.get('projects', []))}\n"
                preview += f"Skills categories: {len(tailored.get('skills', {}))}\n"
            else:
                preview = "Tailoring completed but no tailored sections were found in state."
            self.after(0, lambda: self._tailor_progress.config(text="✓ Tailoring complete."))
            self.after(0, lambda: self._set_tailor_output(preview))

        def on_error(error: str):
            self.after(0, lambda: self._tailor_progress.config(text=f"✗ Error: {error[:80]}"))
            self.after(0, lambda: self._set_tailor_output(f"Error:\n{error}"))

        self._bridge.run_pipeline_async(instruction, on_done, on_error)

    def _compile_pdf(self):
        self._tailor_progress.config(text="⏳ Compiling PDF...")

        def on_done(result: dict):
            pdf_path = result.get("pdf_path")
            if pdf_path:
                self._last_pdf_path = pdf_path
                self.after(0, lambda: self._tailor_open_btn.config(state="normal"))
                self.after(0, lambda: self._tailor_progress.config(
                    text=f"✓ PDF compiled: {pdf_path}"))
                self.after(0, lambda: self._set_tailor_output(
                    f"PDF compiled successfully!\n\nPath: {pdf_path}"))
            else:
                self.after(0, lambda: self._tailor_progress.config(
                    text="⚠ PDF compilation failed. Is pdflatex installed?"))

        def on_error(error: str):
            self.after(0, lambda: self._tailor_progress.config(text=f"✗ Error: {error[:80]}"))

        self._bridge.run_pipeline_async(
            "Compile the PDF from the current tailored resume.",
            on_done, on_error,
        )

    def _open_pdf(self):
        if self._last_pdf_path and Path(self._last_pdf_path).exists():
            import subprocess
            subprocess.Popen(["start", self._last_pdf_path], shell=True)

    # ── Status bar ────────────────────────────────────────────────────────────

    def _status(self, msg: str):
        """Update status (use the tailor progress label or a future status bar)."""
        self._tailor_progress.config(text=msg)

    # ── Initial data load ─────────────────────────────────────────────────────

    def _load_initial_data(self):
        profile = self._bridge.load_profile()
        if profile:
            self._load_profile_into_form(profile)

        jd = self._bridge.load_jd()
        if jd:
            self._jd_text.insert("1.0", jd)
            self._update_jd_char_count()

        self._refresh_templates()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    app = ProfileManagerApp()
    app.mainloop()


if __name__ == "__main__":
    main()
