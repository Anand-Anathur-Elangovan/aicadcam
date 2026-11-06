"use client";

// Left panel of the Studio: file upload, feature summary, CAPP process plan display,
// and controls to generate G-code.

import { useState, useCallback } from "react";
import type { ProcessPlan, ProcessOperation } from "@/lib/providers/capp.provider";
import type { PartFeatures } from "@/lib/cad/featureExtractor";

interface Props {
  onPlanGenerated: (plan: ProcessPlan, features: PartFeatures) => void;
  onGCodeGenerated: (gcode: string) => void;
  onFileUploaded?: (pdfUrl: string) => void;
}

type PanelTab = "upload" | "plan" | "ops";

export default function RoutingPanel({ onPlanGenerated, onGCodeGenerated, onFileUploaded }: Props) {
  const [activeTab, setActiveTab]     = useState<PanelTab>("upload");
  const [isGenerating, setIsGenerating] = useState(false);
  const [isGcoding, setIsGcoding]     = useState(false);
  const [plan, setPlan]               = useState<ProcessPlan | null>(null);
  const [features, setFeatures]       = useState<PartFeatures | null>(null);
  const [targetMachine, setTargetMachine] = useState("Fanuc 3-Axis VMC");
  const [error, setError]             = useState<string | null>(null);
  const [progress, setProgress]       = useState("");
  const [dragOver, setDragOver]       = useState(false);

  const handleFile = useCallback(async (file: File) => {
    setError(null);
    setIsGenerating(true);
    setProgress("Uploading and extracting features…");
    setActiveTab("plan");

    try {
      // Emit PDF URL for Canvas2D
      if (file.type === "application/pdf") {
        const url = URL.createObjectURL(file);
        onFileUploaded?.(url);
      }

      const fd = new FormData();
      fd.append("file", file);
      fd.append("material", "");  // will be auto-extracted

      setProgress("Generating process plan…");
      const res = await fetch("/api/process/generate", { method: "POST", body: fd });
      if (!res.ok) throw new Error(await res.text());

      const data = await res.json() as { plan: ProcessPlan; features: PartFeatures };
      setPlan(data.plan);
      setFeatures(data.features);
      onPlanGenerated(data.plan, data.features);
      setActiveTab("plan");
      setProgress("");

    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
      setActiveTab("upload");
    } finally {
      setIsGenerating(false);
    }
  }, [onPlanGenerated, onFileUploaded]);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const file = e.dataTransfer.files[0];
    if (file) handleFile(file);
  }, [handleFile]);

  const handleInputChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) handleFile(file);
  }, [handleFile]);

  const generateGCode = useCallback(async () => {
    if (!plan || !features) return;
    setIsGcoding(true);
    setError(null);

    try {
      const res = await fetch("/api/gcode/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ plan, features, targetMachine }),
      });
      if (!res.ok) throw new Error(await res.text());

      const reader = res.body!.getReader();
      const dec    = new TextDecoder();
      let gcode    = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        const text = dec.decode(value);
        for (const line of text.split("\n")) {
          if (line.startsWith("data: ")) {
            const evt = JSON.parse(line.slice(6)) as { step: string; progress: number; gcode?: string; error?: string };
            setProgress(evt.step);
            if (evt.gcode) gcode = evt.gcode;
            if (evt.error) throw new Error(evt.error);
          }
        }
      }

      if (gcode) onGCodeGenerated(gcode);
      setProgress("");

    } catch (err) {
      setError(err instanceof Error ? err.message : "G-code generation failed");
    } finally {
      setIsGcoding(false);
    }
  }, [plan, features, targetMachine, onGCodeGenerated]);

  const tabs: { id: PanelTab; label: string }[] = [
    { id: "upload", label: "Upload" },
    { id: "plan",   label: "Plan" },
    { id: "ops",    label: "Operations" },
  ];

  return (
    <div className="flex flex-col h-full bg-slate-900 text-slate-200">
      {/* Tabs */}
      <div className="flex border-b border-slate-700 shrink-0">
        {tabs.map((t) => (
          <button
            key={t.id}
            onClick={() => setActiveTab(t.id)}
            className={`flex-1 py-2 text-xs font-medium transition-colors ${
              activeTab === t.id
                ? "text-cyan-400 border-b-2 border-cyan-400"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-3">
        {/* Error */}
        {error && (
          <div className="text-xs text-red-400 bg-red-950/40 border border-red-800 rounded p-2">
            {error}
          </div>
        )}

        {/* Progress */}
        {(isGenerating || isGcoding) && progress && (
          <div className="text-xs text-yellow-400 animate-pulse">{progress}</div>
        )}

        {/* Upload tab */}
        {activeTab === "upload" && (
          <div className="space-y-3">
            <label
              className={`flex flex-col items-center justify-center gap-2 p-6 rounded-lg border-2 border-dashed cursor-pointer transition-colors ${
                dragOver
                  ? "border-cyan-400 bg-cyan-950/20"
                  : "border-slate-600 hover:border-slate-500"
              }`}
              onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
              onDragLeave={() => setDragOver(false)}
              onDrop={handleDrop}
            >
              <svg className="w-8 h-8 text-slate-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
                  d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
              </svg>
              <span className="text-xs text-slate-400">
                Drop a STEP, IGES, or PDF drawing here
              </span>
              <input
                type="file"
                className="hidden"
                accept=".stp,.step,.igs,.iges,.pdf,.png,.jpg,.jpeg"
                onChange={handleInputChange}
                disabled={isGenerating}
              />
              <span className="text-xs text-cyan-500 font-medium">
                {isGenerating ? "Generating…" : "Click to browse"}
              </span>
            </label>

            <div className="space-y-1">
              <label className="text-xs text-slate-400">Target Machine</label>
              <select
                className="w-full text-xs bg-slate-800 border border-slate-700 rounded px-2 py-1.5 text-slate-200"
                value={targetMachine}
                onChange={(e) => setTargetMachine(e.target.value)}
              >
                <option>Fanuc 3-Axis VMC</option>
                <option>Haas VF-2</option>
                <option>Fanuc 5-Axis VMC</option>
                <option>CNC Lathe (Fanuc)</option>
                <option>CNC Lathe (Haas)</option>
              </select>
            </div>
          </div>
        )}

        {/* Plan summary tab */}
        {activeTab === "plan" && plan && (
          <div className="space-y-3">
            <PlanSummaryCard plan={plan} features={features} />

            <button
              onClick={generateGCode}
              disabled={isGcoding}
              className="w-full py-2 text-xs font-semibold rounded bg-cyan-600 hover:bg-cyan-500 disabled:opacity-50 text-white transition-colors"
            >
              {isGcoding ? "Generating G-Code…" : "⚙ Generate G-Code"}
            </button>

            <div className="space-y-1">
              <label className="text-xs text-slate-400">Machine</label>
              <select
                className="w-full text-xs bg-slate-800 border border-slate-700 rounded px-2 py-1.5 text-slate-200"
                value={targetMachine}
                onChange={(e) => setTargetMachine(e.target.value)}
              >
                <option>Fanuc 3-Axis VMC</option>
                <option>Haas VF-2</option>
                <option>Fanuc 5-Axis VMC</option>
                <option>CNC Lathe (Fanuc)</option>
              </select>
            </div>
          </div>
        )}

        {activeTab === "plan" && !plan && !isGenerating && (
          <p className="text-xs text-slate-500 text-center pt-8">
            Upload a file to generate a process plan.
          </p>
        )}

        {/* Operations tab */}
        {activeTab === "ops" && plan && (
          <div className="space-y-2">
            {plan.operations.map((op) => (
              <OperationCard key={op.opNumber} op={op} />
            ))}
          </div>
        )}

        {activeTab === "ops" && !plan && (
          <p className="text-xs text-slate-500 text-center pt-8">
            No plan yet. Upload a file first.
          </p>
        )}
      </div>
    </div>
  );
}

// ── Sub-components ─────────────────────────────────────────────────────────────

function PlanSummaryCard({ plan, features }: { plan: ProcessPlan; features: PartFeatures | null }) {
  return (
    <div className="space-y-2 text-xs">
      <div className="font-semibold text-slate-200 text-sm">{plan.partName}</div>
      <div className="grid grid-cols-2 gap-1 text-slate-400">
        <span>P/N</span>     <span className="text-slate-200">{plan.partNumber}</span>
        <span>Rev</span>     <span className="text-slate-200">{plan.revision}</span>
        <span>Material</span><span className="text-slate-200">{plan.material}</span>
        <span>Machine</span> <span className="text-slate-200">{plan.machineType}</span>
        <span>Ops</span>     <span className="text-slate-200">{plan.operations.length}</span>
        <span>Est. Time</span><span className="text-slate-200">{plan.totalEstimatedTime} min</span>
      </div>
      {features && (
        <div className="mt-2 flex gap-1 flex-wrap">
          <Badge color="cyan">{features.partType}</Badge>
          <Badge color="violet">{features.estimatedComplexity} complexity</Badge>
          {features.features.map((f, i) => (
            <Badge key={i} color="slate">{f.count}× {f.type}</Badge>
          ))}
        </div>
      )}
      {plan.generalNotes.length > 0 && (
        <ul className="mt-2 space-y-0.5 text-slate-400 text-[11px]">
          {plan.generalNotes.map((n, i) => <li key={i}>• {n}</li>)}
        </ul>
      )}
    </div>
  );
}

function OperationCard({ op }: { op: ProcessOperation }) {
  const [open, setOpen] = useState(false);

  return (
    <div className="rounded border border-slate-700 bg-slate-800/50 text-xs">
      <button
        className="w-full flex items-center justify-between px-3 py-2 text-left"
        onClick={() => setOpen((o) => !o)}
      >
        <div className="flex items-center gap-2">
          <span className="text-cyan-400 font-mono font-bold">OP{op.opNumber}</span>
          <span className="text-slate-200">{op.name}</span>
        </div>
        <div className="flex items-center gap-2 text-slate-400">
          <span>{op.estimatedTime}m</span>
          <span>{open ? "▲" : "▼"}</span>
        </div>
      </button>

      {open && (
        <div className="px-3 pb-3 space-y-2 border-t border-slate-700">
          <p className="text-slate-400 mt-2">{op.description}</p>
          <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-[11px] text-slate-400">
            <span>Tool</span>       <span className="text-slate-200">{op.tool.type} Ø{op.tool.diameter}mm</span>
            <span>Material</span>   <span className="text-slate-200">{op.tool.material}</span>
            <span>Spindle</span>    <span className="text-slate-200">S{op.cuttingParams.spindleSpeed}</span>
            <span>Feed</span>       <span className="text-slate-200">F{op.cuttingParams.feedRate}</span>
            <span>DOC</span>        <span className="text-slate-200">{op.cuttingParams.depthOfCut}mm</span>
            <span>Coolant</span>    <span className="text-slate-200">{op.cuttingParams.coolant}</span>
            <span>Clamping</span>   <span className="text-slate-200">{op.setup.clampingMethod}</span>
            <span>Datum</span>      <span className="text-slate-200">{op.setup.datumFace}</span>
          </div>
          {op.qualityChecks.length > 0 && (
            <div>
              <p className="text-[11px] text-slate-400 mb-1">Quality Checks</p>
              <ul className="space-y-0.5 text-[11px] text-slate-300">
                {op.qualityChecks.map((q, i) => <li key={i}>✓ {q}</li>)}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function Badge({ children, color }: { children: React.ReactNode; color: "cyan" | "violet" | "slate" }) {
  const cls = {
    cyan:   "bg-cyan-900/50 text-cyan-300 border-cyan-800",
    violet: "bg-violet-900/50 text-violet-300 border-violet-800",
    slate:  "bg-slate-700/50 text-slate-300 border-slate-600",
  }[color];
  return (
    <span className={`inline-block px-1.5 py-0.5 rounded text-[10px] border ${cls}`}>
      {children}
    </span>
  );
}
