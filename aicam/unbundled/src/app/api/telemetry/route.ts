// GET /api/telemetry — System & GPU health metrics
// Returns CPU, memory, GPU telemetry, vector DB stats, active job counts.
// GPU data via nvidia-smi subprocess; system via Node.js os module.

import { NextRequest } from "next/server";
import { execFile } from "child_process";
import { promisify } from "util";
import os from "os";
import { Pool } from "pg";
import { getAuthUser, requireRole } from "@/lib/security/rbac";

export const runtime    = "nodejs";
export const maxDuration = 15;

const execFileAsync = promisify(execFile);

declare const globalThis: { __pgPool?: Pool };

function getPool(): Pool {
  if (!globalThis.__pgPool) {
    globalThis.__pgPool = new Pool({ connectionString: process.env.DATABASE_URL });
  }
  return globalThis.__pgPool;
}

// ─── GPU via nvidia-smi ───────────────────────────────────────────────────────

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

async function getNvidiaInfo(): Promise<GpuInfo[]> {
  try {
    const { stdout } = await execFileAsync("nvidia-smi", [
      "--query-gpu=name,driver_version,temperature.gpu,power.draw,power.limit,memory.used,memory.total,utilization.gpu",
      "--format=csv,noheader,nounits",
    ], { timeout: 5000 });

    return stdout.trim().split("\n").map((line) => {
      const [name, driver, temp, power, powerLimit, memUsed, memTotal, util] = line.split(", ").map((s) => s.trim());
      return {
        name,
        driverVersion: driver,
        tempC:         parseFloat(temp)      || 0,
        powerW:        parseFloat(power)     || 0,
        powerLimitW:   parseFloat(powerLimit) || 0,
        memUsedMB:     parseFloat(memUsed)   || 0,
        memTotalMB:    parseFloat(memTotal)  || 0,
        utilPercent:   parseFloat(util)      || 0,
      };
    });
  } catch {
    return [];   // No GPU or nvidia-smi not available
  }
}

// ─── CPU & Memory ─────────────────────────────────────────────────────────────

function getCpuInfo() {
  const cpus    = os.cpus();
  const totalMem = os.totalmem();
  const freeMem  = os.freemem();

  // CPU usage approximation via load average
  const loadAvg  = os.loadavg();    // [1m, 5m, 15m]
  const cpuCount = cpus.length;
  const cpuUsePct = Math.min(100, (loadAvg[0] / cpuCount) * 100);

  return {
    model:          cpus[0]?.model ?? "Unknown",
    coreCount:      cpuCount,
    loadAvg1m:      Math.round(loadAvg[0] * 100) / 100,
    loadAvg5m:      Math.round(loadAvg[1] * 100) / 100,
    cpuUsePct:      Math.round(cpuUsePct),
    totalMemMB:     Math.round(totalMem / 1024 / 1024),
    usedMemMB:      Math.round((totalMem - freeMem) / 1024 / 1024),
    freeMemMB:      Math.round(freeMem / 1024 / 1024),
    memUsePct:      Math.round(((totalMem - freeMem) / totalMem) * 100),
    uptime:         Math.round(os.uptime()),
    platform:       os.platform(),
    arch:           os.arch(),
  };
}

// ─── Vector DB Stats ──────────────────────────────────────────────────────────

async function getVectorDbStats() {
  try {
    const pool = getPool();
    const [docRes, chunkRes, diffRes, jobRes] = await Promise.all([
      pool.query(`SELECT COUNT(*) AS total, COUNT(*) FILTER (WHERE status='indexed') AS indexed,
                         COUNT(*) FILTER (WHERE status='error') AS errored FROM documents`),
      pool.query(`SELECT COUNT(*) AS total FROM chunks`),
      pool.query(`SELECT COUNT(*) AS total, COUNT(*) FILTER (WHERE embedded_in_vector) AS embedded FROM gcode_diffs`),
      pool.query(`SELECT COUNT(*) AS total FROM training_jobs WHERE status IN ('QUEUED','RUNNING')`),
    ]);

    return {
      documents:    parseInt(docRes.rows[0].total,   10),
      indexed:      parseInt(docRes.rows[0].indexed, 10),
      errored:      parseInt(docRes.rows[0].errored, 10),
      chunks:       parseInt(chunkRes.rows[0].total, 10),
      diffs:        parseInt(diffRes.rows[0].total,   10),
      diffsEmbedded: parseInt(diffRes.rows[0].embedded, 10),
      activeJobs:   parseInt(jobRes.rows[0].total,   10),
    };
  } catch {
    return { documents: 0, indexed: 0, errored: 0, chunks: 0, diffs: 0, diffsEmbedded: 0, activeJobs: 0 };
  }
}

// ─── Inference Latency (stored in DB) ────────────────────────────────────────

async function getInferenceStats() {
  // In production, store actual latencies in a telemetry table.
  // For now, derive approximate stats from recent gcode_diffs timing.
  try {
    const pool  = getPool();
    const res   = await pool.query(
      `SELECT COUNT(*) AS count FROM gcode_diffs WHERE approved_at > now() - interval '1 hour'`
    );
    const approvals = parseInt(res.rows[0].count, 10);
    return {
      approvalsLastHour: approvals,
      avgRagLatencyMs:   null as number | null,   // populate from real metrics
      avgGcodeLatencyMs: null as number | null,
    };
  } catch {
    return { approvalsLastHour: 0, avgRagLatencyMs: null, avgGcodeLatencyMs: null };
  }
}

// ─── Route ────────────────────────────────────────────────────────────────────

export async function GET(req: NextRequest) {
  try {
    const user = await getAuthUser(req);
    requireRole(user, "ADMIN");

    const [gpu, vectorDb, inferenceStats] = await Promise.all([
      getNvidiaInfo(),
      getVectorDbStats(),
      getInferenceStats(),
    ]);

    const cpu = getCpuInfo();

    return Response.json({
      timestamp: new Date().toISOString(),
      system:    cpu,
      gpu,
      vectorDb,
      inference: inferenceStats,
      deployment: process.env.DEPLOYMENT_MODE ?? "CLOUD",
    });

  } catch (err) {
    const status = (err as { status?: number }).status ?? 500;
    return Response.json({ error: err instanceof Error ? err.message : "Failed" }, { status });
  }
}
