"use client";

import React, { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";

// ── Type Definitions ──────────────────────────────────────────────────────

interface Experience {
  company: string;
  title: string;
  location: string;
  start_date: string;
  end_date: string;
  bullets: string[];
}

interface ProjectItem {
  name: string;
  technologies: string;
  description: string;
  bullets: string[];
}

interface EducationItem {
  institution: string;
  degree: string;
  field: string;
  start_date: string;
  end_date: string;
  gpa: string;
  highlights: string[];
}

interface MasterProfile {
  name: string;
  email: string;
  phone: string;
  linkedin: string;
  github: string;
  website: string;
  summary: string;
  experience: Experience[];
  education: EducationItem[];
  skills: Record<string, string[]>;
  projects: ProjectItem[];
  achievements: string[];
  certifications: string[];
}

const EMPTY_EXPERIENCE: Experience = {
  company: "", title: "", location: "", start_date: "", end_date: "", bullets: [""],
};
const EMPTY_PROJECT: ProjectItem = {
  name: "", technologies: "", description: "", bullets: [""],
};
const EMPTY_EDUCATION: EducationItem = {
  institution: "", degree: "", field: "", start_date: "", end_date: "", gpa: "", highlights: [""],
};

const EMPTY_PROFILE: MasterProfile = {
  name: "", email: "", phone: "", linkedin: "", github: "", website: "", summary: "",
  experience: [], education: [], skills: {}, projects: [], achievements: [], certifications: [],
};

// ── Reusable Section Components ───────────────────────────────────────────

function SectionHeader({ title, onAdd, addLabel }: { title: string; onAdd?: () => void; addLabel?: string }) {
  return (
    <div className="flex items-center justify-between mb-3 mt-8 first:mt-0">
      <h2 className="text-lg font-semibold text-indigo-300 tracking-wide">{title}</h2>
      {onAdd && (
        <button onClick={onAdd} className="text-xs bg-indigo-600 hover:bg-indigo-500 px-3 py-1.5 rounded-md font-medium transition-colors">
          + {addLabel || "Add"}
        </button>
      )}
    </div>
  );
}

function InputField({ label, value, onChange, placeholder, type = "text" }:
  { label: string; value: string; onChange: (v: string) => void; placeholder?: string; type?: string }) {
  return (
    <div>
      <label className="block text-xs font-medium text-slate-400 mb-1">{label}</label>
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-100 focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 outline-none transition placeholder-slate-500"
      />
    </div>
  );
}

function TextAreaField({ label, value, onChange, placeholder, rows = 3 }:
  { label: string; value: string; onChange: (v: string) => void; placeholder?: string; rows?: number }) {
  return (
    <div>
      <label className="block text-xs font-medium text-slate-400 mb-1">{label}</label>
      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        rows={rows}
        className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-100 focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 outline-none transition resize-none placeholder-slate-500"
      />
    </div>
  );
}

function BulletList({ bullets, onChange }: { bullets: string[]; onChange: (b: string[]) => void }) {
  return (
    <div className="space-y-2">
      <label className="block text-xs font-medium text-slate-400">Bullet Points</label>
      {bullets.map((b, i) => (
        <div key={i} className="flex gap-2 items-start">
          <span className="text-slate-500 text-sm mt-2">•</span>
          <textarea
            value={b}
            onChange={(e) => { const c = [...bullets]; c[i] = e.target.value; onChange(c); }}
            rows={2}
            className="flex-1 bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-sm text-slate-100 focus:border-indigo-500 outline-none transition resize-none placeholder-slate-500"
            placeholder="Describe accomplishment..."
          />
          <button onClick={() => onChange(bullets.filter((_, j) => j !== i))}
            className="text-red-400 hover:text-red-300 text-lg mt-1 shrink-0 px-1">×</button>
        </div>
      ))}
      <button onClick={() => onChange([...bullets, ""])}
        className="text-xs text-indigo-400 hover:text-indigo-300 font-medium">+ Add Bullet</button>
    </div>
  );
}

function RemoveButton({ onClick }: { onClick: () => void }) {
  return (
    <button onClick={onClick}
      className="absolute top-2 right-2 text-red-400 hover:text-red-300 bg-slate-800/80 rounded-full w-6 h-6 flex items-center justify-center text-sm font-bold hover:bg-red-900/30 transition">×</button>
  );
}

// ── Main Editor ───────────────────────────────────────────────────────────

export default function MasterProfileEditor() {
  const router = useRouter();
  const [profile, setProfile] = useState<MasterProfile>(EMPTY_PROFILE);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [saveStatus, setSaveStatus] = useState<"idle" | "saved" | "error">("idle");
  const [showRawJson, setShowRawJson] = useState(false);
  const [rawJson, setRawJson] = useState("");

  // Load profile
  useEffect(() => {
    (async () => {
      try {
        const res = await fetch("/api/profile");
        if (res.ok) {
          const data = await res.json();
          if (data.masterProfile) {
            setProfile({ ...EMPTY_PROFILE, ...data.masterProfile });
          }
        }
      } catch (e) { console.error("Failed to load profile:", e); }
      finally { setIsLoading(false); }
    })();
  }, []);

  // Save profile
  const save = useCallback(async () => {
    setIsSaving(true);
    setSaveStatus("idle");
    try {
      const res = await fetch("/api/profile", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ masterProfile: profile }),
      });
      setSaveStatus(res.ok ? "saved" : "error");
      if (res.ok) setTimeout(() => setSaveStatus("idle"), 3000);
    } catch { setSaveStatus("error"); }
    finally { setIsSaving(false); }
  }, [profile]);

  // Updaters
  const set = <K extends keyof MasterProfile>(key: K, val: MasterProfile[K]) =>
    setProfile(prev => ({ ...prev, [key]: val }));

  const updateExp = (idx: number, field: keyof Experience, val: unknown) =>
    set("experience", profile.experience.map((e, i) => i === idx ? { ...e, [field]: val } : e));

  const updateEdu = (idx: number, field: keyof EducationItem, val: unknown) =>
    set("education", profile.education.map((e, i) => i === idx ? { ...e, [field]: val } : e));

  const updateProj = (idx: number, field: keyof ProjectItem, val: unknown) =>
    set("projects", profile.projects.map((p, i) => i === idx ? { ...p, [field]: val } : p));

  // Skill helpers
  const addSkillCategory = () => set("skills", { ...profile.skills, "": [] });
  const renameSkillCat = (oldCat: string, newCat: string) => {
    const s = { ...profile.skills };
    const vals = s[oldCat] || [];
    delete s[oldCat];
    s[newCat] = vals;
    set("skills", s);
  };
  const setSkillValues = (cat: string, vals: string[]) => set("skills", { ...profile.skills, [cat]: vals });
  const removeSkillCat = (cat: string) => {
    const s = { ...profile.skills };
    delete s[cat];
    set("skills", s);
  };

  // Raw JSON
  const toggleRawJson = () => {
    if (!showRawJson) setRawJson(JSON.stringify(profile, null, 2));
    setShowRawJson(!showRawJson);
  };
  const applyRawJson = () => {
    try {
      setProfile(JSON.parse(rawJson));
      setShowRawJson(false);
    } catch { alert("Invalid JSON"); }
  };

  if (isLoading) {
    return (
      <div className="min-h-screen bg-slate-900 flex items-center justify-center">
        <div className="text-slate-400 animate-pulse text-lg">Loading Master Profile...</div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-[#0f0c29] via-[#302b63] to-[#24243e] text-slate-100">

      {/* Header */}
      <div className="sticky top-0 z-50 bg-slate-900/90 backdrop-blur-md border-b border-slate-800">
        <div className="max-w-5xl mx-auto px-6 py-4 flex items-center justify-between">
          <div className="flex items-center gap-4">
            <button onClick={() => router.push("/")}
              className="text-slate-400 hover:text-white transition p-1">
              <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M19 12H5"/><polyline points="12 19 5 12 12 5"/></svg>
            </button>
            <h1 className="text-xl font-bold tracking-tight">Master Profile</h1>
          </div>
          <div className="flex items-center gap-3">
            <button onClick={toggleRawJson}
              className="text-xs bg-slate-800 hover:bg-slate-700 px-3 py-1.5 rounded-md font-medium transition-colors border border-slate-700">
              {showRawJson ? "Form View" : "Raw JSON"}
            </button>
            <button onClick={save} disabled={isSaving}
              className={`text-sm font-semibold px-5 py-2 rounded-lg transition-all ${
                isSaving ? "bg-slate-700 text-slate-400 cursor-wait" :
                saveStatus === "saved" ? "bg-emerald-600 text-white" :
                saveStatus === "error" ? "bg-red-600 text-white" :
                "bg-indigo-600 hover:bg-indigo-500 text-white shadow-lg shadow-indigo-500/20"
              }`}>
              {isSaving ? "Saving..." : saveStatus === "saved" ? "✓ Saved" : saveStatus === "error" ? "Error" : "Save Profile"}
            </button>
          </div>
        </div>
      </div>

      {/* Body */}
      <div className="max-w-5xl mx-auto px-6 py-8">
        {showRawJson ? (
          <div className="space-y-4">
            <textarea value={rawJson} onChange={(e) => setRawJson(e.target.value)}
              rows={30}
              className="w-full bg-slate-800 border border-slate-700 rounded-xl px-4 py-3 text-sm text-slate-100 font-mono focus:border-indigo-500 outline-none resize-none" />
            <button onClick={applyRawJson}
              className="bg-indigo-600 hover:bg-indigo-500 px-4 py-2 rounded-lg text-sm font-medium transition-colors">Apply JSON</button>
          </div>
        ) : (
          <div className="space-y-6">

            {/* ── Personal Info ── */}
            <SectionHeader title="Personal Information" />
            <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-5 grid grid-cols-1 md:grid-cols-2 gap-4">
              <InputField label="Full Name" value={profile.name} onChange={(v) => set("name", v)} placeholder="John Doe" />
              <InputField label="Email" value={profile.email} onChange={(v) => set("email", v)} placeholder="john@example.com" type="email" />
              <InputField label="Phone" value={profile.phone} onChange={(v) => set("phone", v)} placeholder="+1 (555) 123-4567" />
              <InputField label="LinkedIn" value={profile.linkedin} onChange={(v) => set("linkedin", v)} placeholder="linkedin.com/in/johndoe" />
              <InputField label="GitHub" value={profile.github} onChange={(v) => set("github", v)} placeholder="github.com/johndoe" />
              <InputField label="Website" value={profile.website} onChange={(v) => set("website", v)} placeholder="johndoe.dev" />
            </div>
            <TextAreaField label="Professional Summary" value={profile.summary} onChange={(v) => set("summary", v)}
              placeholder="A senior software engineer with 8+ years of experience in..." rows={4} />

            {/* ── Experience ── */}
            <SectionHeader title="Experience" onAdd={() => set("experience", [...profile.experience, { ...EMPTY_EXPERIENCE }])} addLabel="Experience" />
            {profile.experience.map((exp, i) => (
              <div key={i} className="relative bg-slate-900/60 border border-slate-800 rounded-xl p-5 space-y-4">
                <RemoveButton onClick={() => set("experience", profile.experience.filter((_, j) => j !== i))} />
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <InputField label="Company" value={exp.company} onChange={(v) => updateExp(i, "company", v)} />
                  <InputField label="Title" value={exp.title} onChange={(v) => updateExp(i, "title", v)} />
                  <InputField label="Location" value={exp.location} onChange={(v) => updateExp(i, "location", v)} />
                  <div className="flex gap-3">
                    <InputField label="Start Date" value={exp.start_date} onChange={(v) => updateExp(i, "start_date", v)} placeholder="Jan 2020" />
                    <InputField label="End Date" value={exp.end_date} onChange={(v) => updateExp(i, "end_date", v)} placeholder="Present" />
                  </div>
                </div>
                <BulletList bullets={exp.bullets} onChange={(b) => updateExp(i, "bullets", b)} />
              </div>
            ))}

            {/* ── Education ── */}
            <SectionHeader title="Education" onAdd={() => set("education", [...profile.education, { ...EMPTY_EDUCATION }])} addLabel="Education" />
            {profile.education.map((edu, i) => (
              <div key={i} className="relative bg-slate-900/60 border border-slate-800 rounded-xl p-5 space-y-4">
                <RemoveButton onClick={() => set("education", profile.education.filter((_, j) => j !== i))} />
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <InputField label="Institution" value={edu.institution} onChange={(v) => updateEdu(i, "institution", v)} />
                  <InputField label="Degree" value={edu.degree} onChange={(v) => updateEdu(i, "degree", v)} />
                  <InputField label="Field of Study" value={edu.field} onChange={(v) => updateEdu(i, "field", v)} />
                  <InputField label="GPA" value={edu.gpa} onChange={(v) => updateEdu(i, "gpa", v)} />
                  <InputField label="Start Date" value={edu.start_date} onChange={(v) => updateEdu(i, "start_date", v)} />
                  <InputField label="End Date" value={edu.end_date} onChange={(v) => updateEdu(i, "end_date", v)} />
                </div>
                <BulletList bullets={edu.highlights} onChange={(b) => updateEdu(i, "highlights", b)} />
              </div>
            ))}

            {/* ── Skills ── */}
            <SectionHeader title="Skills" onAdd={addSkillCategory} addLabel="Category" />
            {Object.entries(profile.skills).map(([cat, skills], catIdx) => (
              <div key={catIdx} className="relative bg-slate-900/60 border border-slate-800 rounded-xl p-5 space-y-3">
                <RemoveButton onClick={() => removeSkillCat(cat)} />
                <InputField label="Category Name" value={cat} onChange={(v) => renameSkillCat(cat, v)} placeholder="e.g. Languages, Frameworks" />
                <div>
                  <label className="block text-xs font-medium text-slate-400 mb-1">Skills (comma-separated)</label>
                  <input
                    value={skills.join(", ")}
                    onChange={(e) => setSkillValues(cat, e.target.value.split(",").map(s => s.trim()).filter(Boolean))}
                    className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-100 focus:border-indigo-500 outline-none transition placeholder-slate-500"
                    placeholder="Python, Go, TypeScript"
                  />
                </div>
              </div>
            ))}

            {/* ── Projects ── */}
            <SectionHeader title="Projects" onAdd={() => set("projects", [...profile.projects, { ...EMPTY_PROJECT }])} addLabel="Project" />
            {profile.projects.map((proj, i) => (
              <div key={i} className="relative bg-slate-900/60 border border-slate-800 rounded-xl p-5 space-y-4">
                <RemoveButton onClick={() => set("projects", profile.projects.filter((_, j) => j !== i))} />
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <InputField label="Project Name" value={proj.name} onChange={(v) => updateProj(i, "name", v)} />
                  <InputField label="Technologies" value={proj.technologies} onChange={(v) => updateProj(i, "technologies", v)} placeholder="React, Node.js" />
                </div>
                <TextAreaField label="Description" value={proj.description} onChange={(v) => updateProj(i, "description", v)} rows={2} />
                <BulletList bullets={proj.bullets} onChange={(b) => updateProj(i, "bullets", b)} />
              </div>
            ))}

            {/* ── Achievements ── */}
            <SectionHeader title="Achievements" onAdd={() => set("achievements", [...profile.achievements, ""])} addLabel="Achievement" />
            <div className="space-y-2">
              {profile.achievements.map((ach, i) => (
                <div key={i} className="flex gap-2 items-start">
                  <span className="text-yellow-400 text-sm mt-2">★</span>
                  <input value={ach}
                    onChange={(e) => set("achievements", profile.achievements.map((a, j) => j === i ? e.target.value : a))}
                    className="flex-1 bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-100 focus:border-indigo-500 outline-none transition placeholder-slate-500"
                    placeholder="Describe achievement..."
                  />
                  <button onClick={() => set("achievements", profile.achievements.filter((_, j) => j !== i))}
                    className="text-red-400 hover:text-red-300 text-lg shrink-0 px-1">×</button>
                </div>
              ))}
            </div>

            {/* ── Certifications ── */}
            <SectionHeader title="Certifications" onAdd={() => set("certifications", [...profile.certifications, ""])} addLabel="Certification" />
            <div className="space-y-2">
              {profile.certifications.map((cert, i) => (
                <div key={i} className="flex gap-2 items-start">
                  <span className="text-emerald-400 text-sm mt-2">✦</span>
                  <input value={cert}
                    onChange={(e) => set("certifications", profile.certifications.map((c, j) => j === i ? e.target.value : c))}
                    className="flex-1 bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-100 focus:border-indigo-500 outline-none transition placeholder-slate-500"
                    placeholder="AWS Solutions Architect, etc."
                  />
                  <button onClick={() => set("certifications", profile.certifications.filter((_, j) => j !== i))}
                    className="text-red-400 hover:text-red-300 text-lg shrink-0 px-1">×</button>
                </div>
              ))}
            </div>

            {/* Bottom spacer */}
            <div className="h-16" />
          </div>
        )}
      </div>
    </div>
  );
}
