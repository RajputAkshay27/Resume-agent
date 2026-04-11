"use client";

import React, { useState, useEffect, useRef } from "react";
import { CopilotKit, useCopilotAction, useCopilotChatInternal } from "@copilotkit/react-core";
import { CopilotChat } from "@copilotkit/react-ui";
import { signOut } from "next-auth/react";
import { useRouter } from "next/navigation";

interface ChatSession {
  id: string;
  title: string;
  updatedAt: number;
  templateKey?: string | null;
  jobDescription?: string | null;
  jobUrl?: string | null;
  pdfFilename?: string | null;
  sectionPrefs?: SectionPrefs | null;
}

interface SectionPrefs {
  experience_count: number | null;
  project_count: number | null;
  achievement_count: number | null;
  certification_count: number | null;
  include_summary: boolean;
  include_skills: boolean;
  custom_instructions: string | null;
}

const DEFAULT_PREFS: SectionPrefs = {
  experience_count: null,
  project_count: null,
  achievement_count: null,
  certification_count: null,
  include_summary: true,
  include_skills: true,
  custom_instructions: null,
};

// ── Settings Modal (Template Upload) ─────────────────────────────────────

function SettingsModal({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
  const [isUploading, setIsUploading] = useState(false);
  const [templateKey, setTemplateKey] = useState<string | null>(null);

  useEffect(() => {
    if (isOpen) {
      fetch("/api/user").then(r => r.json()).then(d => {
        if (d.templateKey) setTemplateKey(d.templateKey);
      });
    }
  }, [isOpen]);

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setIsUploading(true);
    const formData = new FormData();
    formData.append("file", file);
    try {
      const res = await fetch("/api/upload", { method: "POST", body: formData });
      if (res.ok) {
        const data = await res.json();
        await fetch("/api/user", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ templateKey: data.key })
        });
        setTemplateKey(data.key);
      }
    } catch (err) { console.error(err); }
    finally { setIsUploading(false); }
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-slate-800 p-6 rounded-xl shadow-xl w-96 text-slate-100">
        <h2 className="text-xl font-semibold mb-4">Settings</h2>
        <div className="mb-4">
          <label className="block text-sm font-medium mb-1">Jinja2 LaTeX Template (.tex)</label>
          <p className="text-xs text-slate-400 mb-2">
            Upload a .tex file using Jinja2 syntax. Supports both standard {"{{ }}"} and LaTeX-safe \VAR{"{}"} delimiters.
          </p>
          <div className="flex flex-col gap-2">
            <input type="file" accept=".tex" onChange={handleUpload} disabled={isUploading} className="text-sm" />
            {isUploading && <span className="text-sm text-slate-400">Uploading...</span>}
            {templateKey && <span className="text-sm text-green-400 truncate">Template: {templateKey.split('/').pop()}</span>}
          </div>
        </div>
        <button onClick={onClose} className="w-full bg-slate-700 py-2 rounded-lg hover:bg-slate-600 transition-colors">Close</button>
      </div>
    </div>
  );
}

// ── Section Preferences Panel ────────────────────────────────────────────

function CountSelector({ label, value, onChange }: {
  label: string;
  value: number | null;
  onChange: (v: number | null) => void;
}) {
  const mode = value === null ? "ai" : value === 0 ? "skip" : "exact";

  return (
    <div className="flex items-center gap-2">
      <span className="text-xs text-slate-300 w-24 shrink-0">{label}</span>
      <select
        value={mode}
        onChange={(e) => {
          const m = e.target.value;
          if (m === "ai") onChange(null);
          else if (m === "skip") onChange(0);
          else onChange(1);
        }}
        className="bg-slate-800 border border-slate-700 rounded px-2 py-1 text-xs text-slate-200 outline-none focus:border-indigo-500"
      >
        <option value="ai">AI Decides</option>
        <option value="skip">Skip</option>
        <option value="exact">Exact #</option>
      </select>
      {mode === "exact" && (
        <input
          type="number"
          min={1}
          max={20}
          value={value || 1}
          onChange={(e) => onChange(Math.max(1, parseInt(e.target.value) || 1))}
          className="w-14 bg-slate-800 border border-slate-700 rounded px-2 py-1 text-xs text-slate-200 outline-none focus:border-indigo-500"
        />
      )}
    </div>
  );
}

function SectionPrefsPanel({ prefs, onChange, onSave, saveStatus }: {
  prefs: SectionPrefs;
  onChange: (p: SectionPrefs) => void;
  onSave: () => void;
  saveStatus: "idle" | "saving" | "saved" | "error";
}) {
  const safePrefs = {
    ...DEFAULT_PREFS,
    ...prefs
  };

  return (
    <div className="bg-slate-800/70 p-4 border-b border-slate-700 space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-bold text-slate-300 uppercase tracking-wider">Section Preferences</h3>
        <button onClick={onSave} disabled={saveStatus === "saving"}
          className={`text-xs px-3 py-1 rounded font-medium transition-all duration-200 ${saveStatus === "saving" ? "bg-slate-600 text-slate-400 cursor-wait" :
            saveStatus === "saved" ? "bg-emerald-600 text-white" :
              saveStatus === "error" ? "bg-red-600 text-white" :
                "bg-indigo-600 hover:bg-indigo-500 text-white"
            }`}>
          {saveStatus === "saving" ? "Saving..." : saveStatus === "saved" ? "✓ Saved" : saveStatus === "error" ? "Error" : "Save"}
        </button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
        <CountSelector label="Experience" value={safePrefs.experience_count}
          onChange={(v) => onChange({ ...safePrefs, experience_count: v })} />
        <CountSelector label="Projects" value={safePrefs.project_count}
          onChange={(v) => onChange({ ...safePrefs, project_count: v })} />
        <CountSelector label="Achievements" value={safePrefs.achievement_count}
          onChange={(v) => onChange({ ...safePrefs, achievement_count: v })} />
        <CountSelector label="Certifications" value={safePrefs.certification_count}
          onChange={(v) => onChange({ ...safePrefs, certification_count: v })} />
      </div>

      <div className="flex gap-4">
        <label className="flex items-center gap-1.5 text-xs text-slate-300 cursor-pointer">
          <input type="checkbox" checked={safePrefs.include_summary ?? true}
            onChange={(e) => onChange({ ...safePrefs, include_summary: e.target.checked })}
            className="rounded border-slate-600 bg-slate-800 text-indigo-500 focus:ring-indigo-500" />
          Summary
        </label>
        <label className="flex items-center gap-1.5 text-xs text-slate-300 cursor-pointer">
          <input type="checkbox" checked={safePrefs.include_skills ?? true}
            onChange={(e) => onChange({ ...safePrefs, include_skills: e.target.checked })}
            className="rounded border-slate-600 bg-slate-800 text-indigo-500 focus:ring-indigo-500" />
          Skills
        </label>
      </div>

      <div>
        <label className="block text-xs text-slate-400 mb-1">Custom Instructions</label>
        <input
          type="text"
          value={safePrefs.custom_instructions || ""}
          onChange={(e) => onChange({ ...safePrefs, custom_instructions: e.target.value || null })}
          placeholder="e.g. emphasize backend work, highlight leadership..."
          className="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1 text-xs text-slate-200 focus:border-indigo-500 outline-none"
        />
      </div>
    </div>
  );
}

// ── Chat Area ────────────────────────────────────────────────────────────

function ChatArea({ session, updateSession }: { session: ChatSession; updateSession: (id: string, partial: Partial<ChatSession>) => void }) {
  // DIAGNOSTIC LOG
  const { setMessages, isLoading, isAvailable, messages } = useCopilotChatInternal();
  console.log(`[rename-debug] Render: id=${session.id}, title="${session.title}", msgs=${messages.length}, loading=${isLoading}, available=${isAvailable}`);

  const parsePrefs = (p: any): SectionPrefs => {
    if (!p) return DEFAULT_PREFS;
    if (typeof p === "string") {
      try { return { ...DEFAULT_PREFS, ...JSON.parse(p) }; }
      catch { return DEFAULT_PREFS; }
    }
    return { ...DEFAULT_PREFS, ...p };
  };

  const [jdText, setJdText] = useState(session.jobDescription || "");
  const [jobUrl, setJobUrl] = useState(session.jobUrl || "");
  const [showContext, setShowContext] = useState(false);
  const [showPrefs, setShowPrefs] = useState(false);
  const [prefs, setPrefs] = useState<SectionPrefs>(parsePrefs(session.sectionPrefs));
  const [isUploadingTemplate, setIsUploadingTemplate] = useState(false);
  const [isDownloadingPdf, setIsDownloadingPdf] = useState(false);
  const [isDownloadingTex, setIsDownloadingTex] = useState(false);
  const [downloadError, setDownloadError] = useState<string | null>(null);

  useEffect(() => {
    setPrefs(parsePrefs(session.sectionPrefs));
    setJdText(session.jobDescription || "");
    setJobUrl(session.jobUrl || "");
  }, [session.id, session.sectionPrefs, session.jobDescription, session.jobUrl]);

  const handleTemplateUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setIsUploadingTemplate(true);
    const formData = new FormData();
    formData.append("file", file);
    formData.append("threadId", session.id);
    try {
      const res = await fetch("/api/chats/upload", { method: "POST", body: formData });
      if (res.ok) {
        const data = await res.json();
        updateSession(session.id, { templateKey: data.key });
      }
    } catch (err) { console.error(err); }
    finally { setIsUploadingTemplate(false); }
  };

  // Register backend tools for progress display
  useCopilotAction({
    name: "fetch_master_profile",
    available: "disabled",
    render: () => <div className="text-sm font-medium text-slate-400 p-2 border-l-2 border-indigo-400 opacity-80">Loading Master Profile...</div>,
  });
  useCopilotAction({
    name: "fetch_job_description",
    available: "disabled",
    render: () => <div className="text-sm font-medium text-slate-400 p-2 border-l-2 border-indigo-400 opacity-80">Loading Job Description &amp; Preferences...</div>,
  });
  useCopilotAction({
    name: "read_state",
    available: "disabled",
    render: () => <div className="text-sm font-medium text-slate-400 p-2 border-l-2 border-indigo-400 opacity-80">Reading internal context...</div>,
  });
  useCopilotAction({
    name: "generate_tailored_resume",
    available: "disabled",
    render: () => <div className="text-sm font-medium text-slate-400 p-2 border-l-2 border-emerald-400 opacity-80">Generating tailored resume (structured output)...</div>,
  });
  useCopilotAction({
    name: "render_latex",
    available: "disabled",
    render: () => <div className="text-sm font-medium text-slate-400 p-2 border-l-2 border-amber-400 opacity-80">Rendering LaTeX template...</div>,
  });

  const historyLoadedRef = useRef(false);
  const hasRenamedRef = useRef(false);

  useEffect(() => { hasRenamedRef.current = false; }, [session.id]);

  useEffect(() => {
    // Determine if we need to rename this chat
    const DEFAULT_TITLE = "New Resume Chat";
    const needsRename = session.title?.trim().toLowerCase() === DEFAULT_TITLE.toLowerCase();

    // Check if we have at least one user message
    const userMessages = messages.filter(m => String(m.role).toLowerCase() === "user");
    const firstUserMsg = userMessages[0];

    console.log(`[rename] Status check: id=${session.id}, title="${session.title}", needsRename=${needsRename}, msgs=${messages.length}, userMsgs=${userMessages.length}, loading=${isLoading}, renamed=${hasRenamedRef.current}`);

    // Trigger rename if needed and possible
    if (needsRename && firstUserMsg && !hasRenamedRef.current) {
      console.log(`[rename] Triggering rename for "${session.id}" using first message...`);
      hasRenamedRef.current = true;

      const doRename = async () => {
        try {
          // Robust text extraction from first user message
          let content = firstUserMsg.content;
          let textToSummarize = "";

          if (typeof content === "string") {
            textToSummarize = content;
          } else if (Array.isArray(content)) {
            textToSummarize = content
              .map(part => {
                if (typeof part === "string") return part;
                if (part && typeof part === "object") return (part as any).text || (part as any).content || "";
                return "";
              })
              .join(" ");
          } else if (content && typeof content === "object") {
            textToSummarize = (content as any).text || (content as any).content || JSON.stringify(content);
          }

          if (!textToSummarize.trim()) {
            console.warn("[rename] Skipping: No usable text found in first message", firstUserMsg);
            hasRenamedRef.current = false; // Allow retry if it's just empty for now
            return;
          }

          console.log(`[rename] Calling API with text: "${textToSummarize.substring(0, 40)}..."`);

          const res = await fetch("/api/chats/rename", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              threadId: session.id,
              message: textToSummarize
            })
          });

          if (res.ok) {
            const data = await res.json();
            console.log(`[rename] Successfully renamed to: "${data.title}"`);
            updateSession(session.id, { title: data.title });
          } else {
            console.error("[rename] API call failed", await res.text());
            hasRenamedRef.current = false; // Allow retry on failure
          }
        } catch (e) {
          console.error("[rename] Request exception", e);
          hasRenamedRef.current = false;
        }
      };
      doRename();
    }
  }, [messages.length, session.id, session.title, isLoading, isAvailable]);

  const [historicalMessages, setHistoricalMessages] = useState<any[] | null>(null);

  // 1. Reset specifically when the session ID changes
  useEffect(() => {
    historyLoadedRef.current = false;
    setHistoricalMessages(null); // Clear pending history for new session
  }, [session.id]);

  // 2. Fetch history immediately when session.id changes (don't wait for isAvailable)
  useEffect(() => {
    const fetchHistory = async () => {
      try {
        console.log(`[history] ⚡ Fetching history for ${session.id} (available=${isAvailable})`);
        const res = await fetch(`http://localhost:8000/history?threadId=${session.id}`);
        if (!res.ok) {
          console.warn(`[history] Backend returned ${res.status}`);
          return;
        }
        const data = await res.json();
        const msgs = data.messages ?? [];
        console.log(`[history] ✅ Got ${msgs.length} messages for ${session.id}`);
        setHistoricalMessages(msgs);
      } catch (e) {
        console.warn("[history] Fetch failed:", e);
      }
    };
    fetchHistory();
  }, [session.id]);

  // 3. Synchronize with CopilotKit (Aggressive sync: don't wait for isAvailable)
  useEffect(() => {
    if (!historicalMessages || historyLoadedRef.current) return;

    console.log(`[history] 🔄 Attempting to apply ${historicalMessages.length} messages (available=${isAvailable}, currentMsgs=${messages.length})`);

    const applyMessages = () => {
      // Final guard within the timeout
      if (historyLoadedRef.current) return;
      
      console.log(`[history] 📥 Applying ${historicalMessages.length} messages to store`);
      historyLoadedRef.current = true;
      
      if (historicalMessages.length > 0) {
        setMessages(historicalMessages.map(m => ({
          id: m.id,
          role: m.role as "user" | "assistant",
          content: m.content,
        })));
      }
    };

    // Use a slightly longer timeout to ensure CopilotKit's internal state is fully settled
    const timer = setTimeout(applyMessages, 300);
    return () => clearTimeout(timer);
  }, [historicalMessages, isAvailable, setMessages]);

  const [prefsSaveStatus, setPrefsSaveStatus] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [jdSaveStatus, setJdSaveStatus] = useState<"idle" | "saving" | "saved" | "error">("idle");

  const handleSavePrefs = async () => {
    setPrefsSaveStatus("saving");
    try {
      const res = await fetch("/api/chats", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: session.id, title: session.title, sectionPrefs: prefs })
      });
      if (res.ok) {
        updateSession(session.id, { sectionPrefs: prefs });
        setPrefsSaveStatus("saved");
        setTimeout(() => setPrefsSaveStatus("idle"), 2000);
      } else {
        setPrefsSaveStatus("error");
        setTimeout(() => setPrefsSaveStatus("idle"), 3000);
      }
    } catch {
      setPrefsSaveStatus("error");
      setTimeout(() => setPrefsSaveStatus("idle"), 3000);
    }
  };

  const handleSaveJD = async () => {
    setJdSaveStatus("saving");
    try {
      const res = await fetch("/api/chats", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: session.id, title: session.title, jobDescription: jdText, jobUrl })
      });
      if (res.ok) {
        updateSession(session.id, { jobDescription: jdText, jobUrl });
        setJdSaveStatus("saved");
        setTimeout(() => setJdSaveStatus("idle"), 2000);
      } else {
        setJdSaveStatus("error");
        setTimeout(() => setJdSaveStatus("idle"), 3000);
      }
    } catch {
      setJdSaveStatus("error");
      setTimeout(() => setJdSaveStatus("idle"), 3000);
    }
  };

  return (
    <div className="w-full max-w-4xl h-full flex flex-col rounded-xl overflow-hidden shadow-[0_25px_60px_rgba(0,0,0,0.5)] bg-slate-900 border border-slate-700">
      {/* Header bar */}
      <div className="bg-slate-800 p-3 border-b border-slate-700 flex justify-between items-center z-10 shrink-0">
        <h2 className="text-slate-200 font-semibold truncate flex-1">{session.title}</h2>
        <div className="flex gap-2 items-center">
          <div className="flex items-center bg-slate-900 border border-slate-700 rounded overflow-hidden mr-2">
            <input
              type="text"
              placeholder="Filename..."
              value={session.pdfFilename || ""}
              onChange={(e) => updateSession(session.id, { pdfFilename: e.target.value })}
              className="bg-transparent text-[10px] text-slate-300 px-2 py-1 outline-none w-24 md:w-32"
            />
            <span className="text-[10px] text-slate-500 pr-2">.pdf</span>
          </div>
          {/* Download PDF button */}
          <button
            id="download-pdf-btn"
            onClick={async () => {
              setIsDownloadingPdf(true);
              setDownloadError(null);
              try {
                const filename = session.pdfFilename || "resume";
                const url = `/api/resume/download?threadId=${session.id}&format=pdf&filename=${encodeURIComponent(filename)}`;
                const res = await fetch(url);
                if (!res.ok) {
                  const err = await res.json().catch(() => ({ error: res.statusText }));
                  setDownloadError(err.error || "Download failed");
                  return;
                }
                const blob = await res.blob();
                const link = document.createElement("a");
                link.href = URL.createObjectURL(blob);
                const safeName = filename.endsWith(".pdf") ? filename : `${filename}.pdf`;
                link.download = safeName;
                link.click();
                URL.revokeObjectURL(link.href);
              } catch (e: unknown) {
                setDownloadError(e instanceof Error ? e.message : "Download failed");
              } finally {
                setIsDownloadingPdf(false);
              }
            }}
            disabled={isDownloadingPdf || isDownloadingTex}
            className={`relative flex items-center gap-1.5 text-white text-xs py-1.5 px-3 rounded transition-all duration-200 shadow font-medium whitespace-nowrap ${isDownloadingPdf
              ? "bg-emerald-700 cursor-wait"
              : "bg-emerald-600 hover:bg-emerald-500"
              }`}
          >
            {isDownloadingPdf ? (
              <>
                <svg className="animate-spin" xmlns="http://www.w3.org/2000/svg" width="14" height="14" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                </svg>
                Compiling...
              </>
            ) : (
              <>
                <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><polyline points="7 10 12 15 17 10" /><line x1="12" y1="15" x2="12" y2="3" /></svg>
                PDF
              </>
            )}
          </button>

          {/* Download LaTeX button */}
          <button
            id="download-tex-btn"
            onClick={async () => {
              setIsDownloadingTex(true);
              setDownloadError(null);
              try {
                const filename = session.pdfFilename || "resume";
                const url = `/api/resume/download?threadId=${session.id}&format=tex&filename=${encodeURIComponent(filename)}`;
                const res = await fetch(url);
                if (!res.ok) {
                  const err = await res.json().catch(() => ({ error: res.statusText }));
                  setDownloadError(err.error || "Download failed");
                  return;
                }
                const blob = await res.blob();
                const link = document.createElement("a");
                link.href = URL.createObjectURL(blob);
                const safeName = filename.endsWith(".tex") ? filename : `${filename}.tex`;
                link.download = safeName;
                link.click();
                URL.revokeObjectURL(link.href);
              } catch (e: unknown) {
                setDownloadError(e instanceof Error ? e.message : "Download failed");
              } finally {
                setIsDownloadingTex(false);
              }
            }}
            disabled={isDownloadingPdf || isDownloadingTex}
            className={`flex items-center gap-1.5 text-white text-xs py-1.5 px-3 rounded transition-all duration-200 shadow font-medium whitespace-nowrap ${isDownloadingTex
              ? "bg-violet-700 cursor-wait"
              : "bg-violet-600 hover:bg-violet-500"
              }`}
          >
            {isDownloadingTex ? (
              <>
                <svg className="animate-spin" xmlns="http://www.w3.org/2000/svg" width="14" height="14" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                </svg>
                Fetching...
              </>
            ) : (
              <>
                <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><polyline points="14 2 14 8 20 8" /></svg>
                LaTeX
              </>
            )}
          </button>
          <button
            onClick={() => setShowPrefs(!showPrefs)}
            className="bg-amber-600 hover:bg-amber-500 text-white text-xs py-1.5 px-3 rounded transition-colors duration-200 shadow font-medium"
          >
            {showPrefs ? "Hide Prefs" : "Sections"}
          </button>
          <button
            onClick={() => setShowContext(!showContext)}
            className="bg-indigo-600 hover:bg-indigo-500 text-white text-xs py-1.5 px-3 rounded transition-colors duration-200 shadow font-medium"
          >
            {showContext ? "Hide JD" : "Edit JD"}
          </button>
        </div>
      </div>

      {/* Download error banner */}
      {downloadError && (
        <div className="bg-red-900/80 border-b border-red-700 px-4 py-2 flex items-center justify-between shrink-0">
          <span className="text-red-300 text-xs">⚠ {downloadError}</span>
          <button onClick={() => setDownloadError(null)} className="text-red-400 hover:text-red-200 text-xs ml-3">✕</button>
        </div>
      )}

      {/* Section Preferences Panel */}
      {showPrefs && (
        <SectionPrefsPanel prefs={prefs} onChange={setPrefs} onSave={handleSavePrefs} saveStatus={prefsSaveStatus} />
      )}

      {/* JD Context Panel */}
      {showContext && (
        <div className="flex flex-col bg-slate-800 p-4 border-b border-slate-700 gap-4 shrink-0">
          <div>
            <h3 className="text-xs font-bold text-slate-300 uppercase tracking-wider mb-2">Job Details</h3>
            <div className="space-y-3">
              <div>
                <label className="block text-xs font-semibold text-slate-400 mb-1">Job Link</label>
                <input
                  type="text"
                  value={jobUrl}
                  onChange={(e) => setJobUrl(e.target.value)}
                  placeholder="https://..."
                  className="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1 text-xs text-slate-200 focus:border-indigo-500 outline-none"
                />
              </div>
              <div>
                <label className="block text-xs font-semibold text-slate-400 mb-1">Job Description</label>
                <textarea
                  value={jdText}
                  onChange={(e) => setJdText(e.target.value)}
                  placeholder="Paste JD here..."
                  className="w-full h-24 bg-slate-900 border border-slate-700 rounded px-2 py-1 text-xs text-slate-200 focus:border-indigo-500 outline-none resize-none"
                />
              </div>
            </div>
          </div>

          <div className="border-t border-slate-700 pt-3">
            <h3 className="text-xs font-bold text-slate-300 uppercase tracking-wider mb-2">Chat Template</h3>
            <div className="flex items-center gap-3">
              <input
                type="file"
                accept=".tex"
                onChange={handleTemplateUpload}
                disabled={isUploadingTemplate}
                className="text-xs text-slate-300 file:mr-2 file:py-1 file:px-2 file:rounded file:border-0 file:text-xs file:font-semibold file:bg-indigo-600 file:text-white hover:file:bg-indigo-500"
              />
              {isUploadingTemplate && <span className="text-xs text-slate-400">Uploading...</span>}
              {!isUploadingTemplate && session.templateKey && (
                <span className="text-xs font-medium text-emerald-400 flex items-center gap-1 bg-emerald-900/30 px-2 py-1 rounded">
                  <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>
                  Template Loaded
                </span>
              )}
              {!isUploadingTemplate && !session.templateKey && (
                <span className="text-xs font-medium text-amber-400 flex items-center gap-1 bg-amber-900/30 px-2 py-1 rounded">
                  <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="12" y1="9" x2="12" y2="13"></line><line x1="12" y1="17" x2="12.01" y2="17"></line><circle cx="12" cy="12" r="10"></circle></svg>
                  No Template
                </span>
              )}
            </div>
          </div>

          <button
            onClick={handleSaveJD}
            disabled={jdSaveStatus === "saving"}
            className={`self-end text-xs py-1.5 px-4 rounded font-medium transition-all duration-200 ${jdSaveStatus === "saving" ? "bg-slate-600 text-slate-400 cursor-wait" :
              jdSaveStatus === "saved" ? "bg-emerald-600 text-white" :
                jdSaveStatus === "error" ? "bg-red-600 text-white" :
                  "bg-slate-700 hover:bg-slate-600 text-white"
              }`}
          >
            {jdSaveStatus === "saving" ? "Saving..." : jdSaveStatus === "saved" ? "✓ Saved" : jdSaveStatus === "error" ? "Error" : "Save Job Info"}
          </button>
        </div>
      )}

      {/* Chat */}
      <div className="flex-1 min-h-0 relative">
        <div className="absolute inset-0">
          <CopilotChat
            className="copilot-chat h-full flex flex-col"
            labels={{
              title: "Resume Builder",
              initial: "Hi! I'm your resume tailoring assistant. I'll use your Master Profile and Job Description to create a tailored resume. Tell me to get started!",
              placeholder: "Type a message...",
            }}
          />
        </div>
      </div>
    </div>
  );
}

// ── Dashboard ────────────────────────────────────────────────────────────

export default function Dashboard() {
  const router = useRouter();
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [currentSessionId, setCurrentSessionId] = useState<string>("");
  const [isMounted, setIsMounted] = useState(false);
  const [editingChatId, setEditingChatId] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState("");
  const [showSettings, setShowSettings] = useState(false);
  const [isCreatingChat, setIsCreatingChat] = useState(false);
  const [isLoadingSessions, setIsLoadingSessions] = useState(true);
  const [hasMasterProfile, setHasMasterProfile] = useState<boolean | null>(null);
  const initialized = useRef(false);
  const creatingRef = useRef(false);
  const redirectingRef = useRef(false);

  useEffect(() => {
    setIsMounted(true);
    if (initialized.current) return;
    initialized.current = true;

    const init = async () => {
      try {
        // 1. Check user profile status
        const userRes = await fetch("/api/user");
        if (userRes.ok) {
          const userData = await userRes.json();
          setHasMasterProfile(userData.hasMasterProfile);
          if (!userData.hasMasterProfile) {
            alert("Please fill in your Master Profile before using the builder! Redirecting...");
            redirectingRef.current = true;
            router.push("/profile");
            return;
          }
        }

        // 2. Fetch chats
        const res = await fetch("/api/chats");
        if (res.ok) {
          const loadedSessions = await res.json();
          const sessionMap = new Map();
          loadedSessions.forEach((s: any) => {
            if (s.sectionPrefs && typeof s.sectionPrefs === "string") {
              try { s.sectionPrefs = JSON.parse(s.sectionPrefs); } catch (e) { console.warn("Failed to parse sectionPrefs", e); }
            }
            sessionMap.set(s.id, s);
          });
          const uniqueSessions = Array.from(sessionMap.values()) as ChatSession[];
          if (uniqueSessions.length > 0) {
            setSessions(uniqueSessions);
            setCurrentSessionId(uniqueSessions[0].id);
          }
        }
      } catch (e) {
        console.error("Failed to initialize dashboard", e);
      } finally {
        setIsLoadingSessions(false);
      }
    };
    init();
  }, []);

  const createNewChatInternal = async (id: string, title: string) => {
    if (creatingRef.current || isCreatingChat) return;
    creatingRef.current = true;
    setIsCreatingChat(true);

    const newSession: ChatSession = { id, title, updatedAt: Date.now() };
    setSessions((prev) => {
      if (prev.some(s => s.id === id)) return prev;
      return [newSession, ...prev];
    });
    setCurrentSessionId(id);

    try {
      const res = await fetch("/api/chats", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id, title })
      });
      if (res.ok) {
        const data = await res.json();
        if (data.templateKey) {
          setSessions(prev => prev.map(s => s.id === id ? { ...s, templateKey: data.templateKey } : s));
        }
      } else {
        const data = await res.json();
        if (data.error === "MISSING_MASTER_PROFILE") {
          alert("Please fill in your Master Profile before creating a chat! Redirecting...");
          setSessions(prev => prev.filter(s => s.id !== id));
          redirectingRef.current = true;
          router.push("/profile");
        }
      }
    } catch (e) { console.error(e); }
    finally { setIsCreatingChat(false); creatingRef.current = false; }
  };

  const createNewChat = () => {
    const newId = crypto.randomUUID();
    createNewChatInternal(newId, "New Resume Chat");
  };

  const switchChat = async (id: string) => {
    setCurrentSessionId(id);
    const session = sessions.find(s => s.id === id);
    if (!session) return;
    setSessions((prev) =>
      prev.map(s => s.id === id ? { ...s, updatedAt: Date.now() } : s)
        .sort((a, b) => b.updatedAt - a.updatedAt)
    );
    try {
      await fetch("/api/chats", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id, title: session.title })
      });
    } catch (e) { console.error(e); }
  };

  const updateSessionState = (id: string, partial: Partial<ChatSession>) => {
    setSessions(prev => prev.map(s => s.id === id ? { ...s, ...partial } : s));
  };

  const deleteChat = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!confirm("Are you sure you want to delete this chat?")) return;
    setSessions(prev => prev.filter(s => s.id !== id));
    try { await fetch(`/api/chats?id=${id}`, { method: "DELETE" }); }
    catch (error) { console.error("Failed to delete chat:", error); }
  };

  useEffect(() => {
    if (sessions.length > 0 && (!currentSessionId || !sessions.some(s => s.id === currentSessionId))) {
      setCurrentSessionId(sessions[0].id);
    }
  }, [sessions, currentSessionId]);

  useEffect(() => {
    if (isMounted && initialized.current && !isLoadingSessions && sessions.length === 0 && !isCreatingChat && !creatingRef.current && hasMasterProfile === true && !redirectingRef.current) {
      createNewChat();
    }
  }, [sessions.length, isMounted, isCreatingChat, isLoadingSessions, hasMasterProfile]);

  const startEditing = (id: string, title: string, e: React.MouseEvent) => {
    e.stopPropagation();
    setEditingChatId(id);
    setEditingTitle(title);
  };

  const saveRename = async (id: string, e?: React.FormEvent | React.FocusEvent) => {
    if (e) { e.stopPropagation(); if ("preventDefault" in e) e.preventDefault(); }
    if (editingTitle.trim() === "") { setEditingChatId(null); return; }
    setEditingChatId(null);
    setSessions(prev =>
      prev.map(s => s.id === id ? { ...s, title: editingTitle, updatedAt: Date.now() } : s)
        .sort((a, b) => b.updatedAt - a.updatedAt)
    );
    try {
      await fetch("/api/chats", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id, title: editingTitle })
      });
    } catch (error) { console.error("Failed to rename chat", error); }
  };

  const handleKeyDown = (id: string, e: React.KeyboardEvent) => {
    if (e.key === "Enter") saveRename(id, e);
    else if (e.key === "Escape") setEditingChatId(null);
  };

  if (!isMounted) return null;

  const currentSession = sessions.find(s => s.id === currentSessionId);

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-slate-900 text-slate-100 font-sans">
      <SettingsModal isOpen={showSettings} onClose={() => setShowSettings(false)} />

      {/* Sidebar */}
      <div className="w-64 bg-slate-950 flex flex-col border-r border-slate-800 shrink-0">
        <div className="p-4 flex gap-2">
          <button
            onClick={createNewChat}
            disabled={isCreatingChat}
            className={`flex-1 flex items-center justify-center gap-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg py-2.5 px-4 font-medium transition-colors cursor-pointer ${isCreatingChat ? "opacity-50 cursor-not-allowed" : ""}`}
          >
            <span className="text-xl leading-none">{isCreatingChat ? "..." : "+"}</span>
            <span>New</span>
          </button>
          <button
            onClick={() => setShowSettings(true)}
            className="bg-slate-800 hover:bg-slate-700 p-2.5 rounded-lg transition-colors flex items-center justify-center"
            title="Settings"
          >
            <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-slate-300"><circle cx="12" cy="12" r="3"></circle><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"></path></svg>
          </button>
        </div>

        {/* Master Profile button */}
        <div className="px-4 pb-2">
          <button
            onClick={() => router.push("/profile")}
            className="w-full flex items-center gap-2 bg-slate-800/60 hover:bg-slate-800 text-slate-300 hover:text-white rounded-lg py-2 px-3 text-sm font-medium transition-colors border border-slate-800"
          >
            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" /><circle cx="12" cy="7" r="4" /></svg>
            Master Profile
          </button>
        </div>

        <div className="flex-1 overflow-y-auto w-full px-2 pb-4">
          {sessions.map((session) => (
            <div
              key={session.id}
              onClick={() => { if (editingChatId !== session.id) switchChat(session.id); }}
              className={`group flex items-center justify-between w-full px-3 py-3 my-1 rounded-lg transition-colors cursor-pointer ${currentSessionId === session.id
                ? "bg-slate-800 text-indigo-400 font-medium"
                : "hover:bg-slate-800/50 text-slate-300 hover:text-slate-100"
                }`}
            >
              {editingChatId === session.id ? (
                <input
                  type="text"
                  value={editingTitle}
                  onChange={(e) => setEditingTitle(e.target.value)}
                  onBlur={(e) => saveRename(session.id, e)}
                  onKeyDown={(e) => handleKeyDown(session.id, e)}
                  onClick={(e) => e.stopPropagation()}
                  autoFocus
                  className="w-full bg-slate-950 border border-indigo-500 rounded px-2 py-1 text-sm text-slate-100 outline-none"
                />
              ) : (
                <>
                  <span className="truncate text-sm pr-2">{session.title}</span>
                  <div className="flex gap-1 shrink-0">
                    <button
                      onClick={(e) => startEditing(session.id, session.title, e)}
                      className={`opacity-0 group-hover:opacity-100 hover:text-indigo-400 transition-opacity p-1 rounded hover:bg-slate-700/50 ${currentSessionId === session.id ? "opacity-100" : ""}`}
                    >
                      <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" /><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" /></svg>
                    </button>
                    <button
                      onClick={(e) => deleteChat(session.id, e)}
                      className={`opacity-0 group-hover:opacity-100 hover:text-red-400 transition-opacity p-1 rounded hover:bg-slate-700/50 ${currentSessionId === session.id ? "opacity-100" : ""}`}
                    >
                      <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M3 6h18" /><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6" /><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2" /></svg>
                    </button>
                  </div>
                </>
              )}
            </div>
          ))}
        </div>

        <div className="p-4 border-t border-slate-800 mt-auto">
          <button onClick={() => signOut({ callbackUrl: "/login" })} className="w-full py-2 px-4 bg-slate-800 hover:bg-slate-700 text-slate-300 hover:text-white rounded-lg transition-colors text-sm font-medium">Sign Out</button>
        </div>
      </div>

      {/* Main Chat Area */}
      <div className="flex-1 flex items-center justify-center relative bg-[linear-gradient(135deg,#0f0c29_0%,#302b63_50%,#24243e_100%)] p-6">
        {currentSession && (
          <CopilotKit
            key={currentSessionId}
            runtimeUrl="/api/copilotkit"
            agent="resume_builder_agent"
            threadId={currentSessionId}
            properties={{ threadId: currentSessionId }}
            showDevConsole={true}
            onError={(event) => {
              console.error("[CopilotKit] RUN_ERROR:", event);
            }}
          >
            <ChatArea session={currentSession} updateSession={updateSessionState} />
          </CopilotKit>
        )}
      </div>
    </div>
  );
}
