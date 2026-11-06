"use client";

import { useEffect, useState } from "react";

interface GpuInfo {
  name:         string;
  driverVersion: string;
  tempC:        number;
  powerW:       number;
  powerLimitW:  number;
  memUsedMB:    number;
  memTotalMB:   number;
  utilPercent:  number;
}

interface TelemetryData {
  timestamp: string;
  system: {
    model:       string;
    coreCount:   number;
    cpuUsePct:   number;
    totalMemMB:  number;
    usedMemMB:   number;
    memUsePct:   number;
    loadAvg1m:   number;
    uptime:      number;
  };
  gpu: GpuInfo[];
  vectorDb: {
    documents:    number;
    indexed:      number;
    errored:      number;
    chunks:       number;
    diffs:        number;
    diffsEmbedded: number;
    activeJobs:   number;
  };
  inference: {
    approvalsLastHour: number;
    avgRagLatencyMs:   number | null;
    avgGcodeLatencyMs: number | null;
  };
  deployment: string;
}

function GaugeBar({ pct, color = "cyan" }: { pct: number; color?: string }) {
  const colors: Record<string, string> = {
    cyan:   "bg-cyan-500",
    green:  "bg-green-500",
    yellow: "bg-yellow-500",
    red:    "bg-red-500",
    violet: "bg-violet-500",
  };
  const barColor = pct > 85 ? colors.red : pct > 65 ? colors.yellow : colors[color] ?? colors.cyan;
  return (
    <div className="h-1.5 w-full bg-slate-700 rounded-full overflow-hidden mt-1">
      <div className={`h-full ${barColor} transition-all`} style={{ width: `${Math.min(100, pct)}%` }} />
    </div>
  );
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="bg-slate-800 border border-slate-700 rounded-xl p-4 space-y-3">
      <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wide">{title}</h3>
      {children}
    </div>
  );
}

function Stat({ label, value, unit }: { label: string; value: string | number; unit?: string }) {
  return (
    <div className="flex justify-between items-center text-sm">
      <span className="text-slate-400 text-xs">{label}</span>
      <span className="text-slate-100 font-mono text-xs">{value}{unit ? <span className="text-slate-500 ml-0.5 text-[10px]">{unit}</span> : null}</span>
    </div>
  );
}

export default function TelemetryCards() {
  const [data, setData]       = useState<TelemetryData | null>(null);
  const [error, setError]     = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchTelemetry = async () => {
    try {
      const res = await fetch("/api/telemetry");
      if (!res.ok) throw new Error(await res.text());
      setData(await res.json() as TelemetryData);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load telemetry");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchTelemetry();
    const id = setInterval(fetchTelemetry, 10_000);
    return () => clearInterval(id);
  }, []);

  if (loading) return <div className="text-slate-500 text-sm">Loading telemetry…</div>;
  if (error)   return <div className="text-red-400 text-sm bg-red-950/30 border border-red-800 rounded p-3">{error}</div>;
  if (!data)   return null;

  const { system, gpu, vectorDb, inference, deployment } = data;

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
      {/* CPU & Memory */}
      <Card title="System Resources">
        <Stat label="CPU Model"  value={system.model.slice(0, 30)} />
        <Stat label="Cores"      value={system.coreCount} />
        <div>
          <Stat label="CPU Usage" value={`${system.cpuUsePct}%`} />
          <GaugeBar pct={system.cpuUsePct} />
        </div>
        <div>
          <Stat label="RAM Usage" value={`${system.usedMemMB} / ${system.totalMemMB}`} unit="MB" />
          <GaugeBar pct={system.memUsePct} color="violet" />
        </div>
        <Stat label="Load (1m)"  value={system.loadAvg1m} />
        <Stat label="Uptime"     value={`${Math.floor(system.uptime / 3600)}h ${Math.floor((system.uptime % 3600) / 60)}m`} />
      </Card>

      {/* GPU Cards */}
      {gpu.length === 0 ? (
        <Card title="GPU Telemetry">
          <p className="text-xs text-slate-500">
            {deployment === "CLOUD" ? "Cloud mode — no local GPU" : "nvidia-smi not available"}
          </p>
        </Card>
      ) : (
        gpu.map((g, i) => (
          <Card key={i} title={`GPU ${i + 1}: ${g.name}`}>
            <div>
              <Stat label="Utilization" value={`${g.utilPercent}%`} />
              <GaugeBar pct={g.utilPercent} color="cyan" />
            </div>
            <div>
              <Stat label="VRAM" value={`${g.memUsedMB} / ${g.memTotalMB}`} unit="MB" />
              <GaugeBar pct={(g.memUsedMB / g.memTotalMB) * 100} color="violet" />
            </div>
            <Stat label="Temperature" value={`${g.tempC}°C`} />
            <div>
              <Stat label="Power" value={`${g.powerW} / ${g.powerLimitW}`} unit="W" />
              <GaugeBar pct={(g.powerW / g.powerLimitW) * 100} color="yellow" />
            </div>
            <Stat label="Driver" value={g.driverVersion} />
          </Card>
        ))
      )}

      {/* Vector DB */}
      <Card title="Vector Knowledge Base">
        <Stat label="Documents"       value={vectorDb.documents} />
        <Stat label="Indexed"         value={vectorDb.indexed} />
        <Stat label="Errors"          value={vectorDb.errored} />
        <Stat label="Chunks"          value={vectorDb.chunks.toLocaleString()} />
        <Stat label="G-Code Diffs"    value={vectorDb.diffs} />
        <Stat label="Embedded Diffs"  value={vectorDb.diffsEmbedded} />
        <Stat label="Active Jobs"     value={vectorDb.activeJobs} />
      </Card>

      {/* Inference Stats */}
      <Card title="Inference Activity">
        <Stat label="Approvals (1h)"       value={inference.approvalsLastHour} />
        <Stat label="Avg RAG Latency"      value={inference.avgRagLatencyMs ?? "—"} unit={inference.avgRagLatencyMs != null ? "ms" : ""} />
        <Stat label="Avg G-Code Latency"   value={inference.avgGcodeLatencyMs ?? "—"} unit={inference.avgGcodeLatencyMs != null ? "ms" : ""} />
        <Stat label="Deployment Mode"      value={deployment} />
        <div className="text-[10px] text-slate-600 pt-1">
          Last updated: {new Date(data.timestamp).toLocaleTimeString()}
        </div>
      </Card>
    </div>
  );
}
