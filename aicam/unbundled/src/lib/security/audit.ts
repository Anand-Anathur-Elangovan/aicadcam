// ITAR / AS9100 Immutable Audit Logger
// Every significant system action is written to the audit_logs table.
// The audit log is append-only: rows are never updated or deleted.

import { createHash } from "crypto";
import { Pool } from "pg";

// Reuse the global pool from vector.provider.ts
declare const globalThis: { __pgPool?: Pool };

function getPool(): Pool {
  if (!globalThis.__pgPool) {
    globalThis.__pgPool = new Pool({ connectionString: process.env.DATABASE_URL });
  }
  return globalThis.__pgPool;
}

export type AuditAction =
  | "LOGIN" | "LOGOUT"
  | "DOCUMENT_VIEW" | "DOCUMENT_DOWNLOAD" | "DOCUMENT_UPLOAD" | "DOCUMENT_DELETE"
  | "GCODE_GENERATE" | "GCODE_EDIT" | "GCODE_APPROVE" | "GCODE_EXPORT"
  | "PROCESS_PLAN_GENERATE" | "PROCESS_PLAN_APPROVE"
  | "DNC_TRANSFER"
  | "FAI_VIEW" | "FAI_SIGN"
  | "SYSTEM_SETTINGS_CHANGE" | "USER_MANAGE"
  | "MODEL_FINETUNE_START" | "MODEL_FINETUNE_COMPLETE";

export interface AuditEntry {
  userId?:       string;
  userEmail?:    string;
  userRole?:     string;
  ipAddress?:    string;
  userAgent?:    string;
  action:        AuditAction;
  resourceType?: string;
  resourceId?:   string;
  resourceName?: string;
  details?:      Record<string, unknown>;
  sessionId?:    string;
}

export async function writeAuditLog(entry: AuditEntry): Promise<void> {
  const timestamp = new Date().toISOString();
  const rawString = `${timestamp}|${entry.userId ?? ""}|${entry.action}|${entry.resourceId ?? ""}`;
  const checksum  = createHash("sha256").update(rawString).digest("hex");

  try {
    const pool = getPool();
    await pool.query(
      `INSERT INTO audit_logs
       (timestamp, user_id, user_email, user_role, ip_address, user_agent,
        action, resource_type, resource_id, resource_name, details, checksum, session_id)
       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)`,
      [
        timestamp,
        entry.userId   ?? null,
        entry.userEmail ?? null,
        entry.userRole  ?? null,
        entry.ipAddress ?? null,
        entry.userAgent ?? null,
        entry.action,
        entry.resourceType ?? null,
        entry.resourceId   ?? null,
        entry.resourceName ?? null,
        JSON.stringify(entry.details ?? {}),
        checksum,
        entry.sessionId ?? null,
      ]
    );
  } catch (err) {
    // Audit failures must not break the main flow — log to stderr only
    console.error("[AUDIT] Failed to write audit log:", err);
  }
}

export interface AuditQueryParams {
  userId?:    string;
  action?:    AuditAction;
  from?:      string;   // ISO date
  to?:        string;
  resourceId?: string;
  limit?:     number;
  offset?:    number;
}

export interface AuditRow {
  id:            number;
  timestamp:     string;
  userEmail:     string | null;
  userRole:      string | null;
  ipAddress:     string | null;
  action:        string;
  resourceType:  string | null;
  resourceName:  string | null;
  details:       Record<string, unknown>;
  checksum:      string;
}

export async function queryAuditLogs(params: AuditQueryParams = {}): Promise<{ rows: AuditRow[]; total: number }> {
  const pool      = getPool();
  const conditions: string[] = [];
  const values:    unknown[] = [];
  let   idx = 1;

  if (params.userId)     { conditions.push(`user_id = $${idx++}`);      values.push(params.userId); }
  if (params.action)     { conditions.push(`action = $${idx++}`);       values.push(params.action); }
  if (params.resourceId) { conditions.push(`resource_id = $${idx++}`);  values.push(params.resourceId); }
  if (params.from)       { conditions.push(`timestamp >= $${idx++}`);   values.push(params.from); }
  if (params.to)         { conditions.push(`timestamp <= $${idx++}`);   values.push(params.to); }

  const where = conditions.length ? `WHERE ${conditions.join(" AND ")}` : "";
  const limit  = params.limit  ?? 100;
  const offset = params.offset ?? 0;

  const [dataRes, countRes] = await Promise.all([
    pool.query(
      `SELECT id, timestamp, user_email, user_role, ip_address,
              action, resource_type, resource_name, details, checksum
       FROM audit_logs ${where}
       ORDER BY timestamp DESC
       LIMIT $${idx++} OFFSET $${idx}`,
      [...values, limit, offset]
    ),
    pool.query(`SELECT COUNT(*) FROM audit_logs ${where}`, values),
  ]);

  const rows: AuditRow[] = dataRes.rows.map((r) => ({
    id:           r.id,
    timestamp:    r.timestamp,
    userEmail:    r.user_email,
    userRole:     r.user_role,
    ipAddress:    r.ip_address,
    action:       r.action,
    resourceType: r.resource_type,
    resourceName: r.resource_name,
    details:      typeof r.details === "string" ? JSON.parse(r.details) : r.details,
    checksum:     r.checksum,
  }));

  return { rows, total: parseInt(countRes.rows[0].count, 10) };
}
