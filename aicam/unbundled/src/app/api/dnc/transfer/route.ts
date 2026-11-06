// POST /api/dnc/transfer — Push approved G-code directly to a CNC machine
// GET  /api/dnc/transfer — List registered machines and their status
// Supports TCP socket, SMB, and FTP protocols.

import { NextRequest } from "next/server";
import { Pool } from "pg";
import { transferGCode, pingMachine, type MachineTarget } from "@/lib/dnc/networkTransfer";
import { writeAuditLog } from "@/lib/security/audit";
import { getAuthUser, requireRole } from "@/lib/security/rbac";

export const runtime    = "nodejs";
export const maxDuration = 30;

declare const globalThis: { __pgPool?: Pool };

function getPool(): Pool {
  if (!globalThis.__pgPool) {
    globalThis.__pgPool = new Pool({ connectionString: process.env.DATABASE_URL });
  }
  return globalThis.__pgPool;
}

export async function POST(req: NextRequest) {
  try {
    const user = await getAuthUser(req);
    requireRole(user, "PROGRAMMER");

    const body = await req.json() as {
      machineId:    string;
      programName:  string;
      gcode:        string;
      diffId?:      string;
    };

    const { machineId, programName, gcode, diffId } = body;

    if (!machineId || !programName || !gcode) {
      return Response.json({ error: "machineId, programName, and gcode are required" }, { status: 400 });
    }

    const pool = getPool();

    // Fetch machine config
    const machineRes = await pool.query<{
      id: string; name: string; display_name: string; protocol: string;
      host: string; port: number; network_path: string | null;
      username: string | null; password_enc: string | null;
    }>(
      `SELECT id, name, display_name, protocol, host, port, network_path, username, password_enc
       FROM machines WHERE id = $1 AND is_active = true`,
      [machineId]
    );

    if (!machineRes.rows.length) {
      return Response.json({ error: "Machine not found or inactive" }, { status: 404 });
    }

    const m = machineRes.rows[0];
    const machine: MachineTarget = {
      id:          m.id,
      name:        m.name,
      displayName: m.display_name,
      protocol:    m.protocol as MachineTarget["protocol"],
      host:        m.host,
      port:        m.port,
      networkPath: m.network_path ?? undefined,
      username:    m.username    ?? undefined,
      password:    m.password_enc ?? undefined,  // decrypt in prod with KMS
    };

    // Log transfer start
    const transferRes = await pool.query<{ id: string }>(
      `INSERT INTO dnc_transfers
       (machine_id, machine_name, gcode_diff_id, program_name, file_size_bytes, transferred_by, status)
       VALUES ($1,$2,$3,$4,$5,$6,'TRANSFERRING')
       RETURNING id`,
      [machineId, m.name, diffId ?? null, programName, Buffer.byteLength(gcode, "utf8"), user!.id]
    );

    const transferId = transferRes.rows[0].id;

    // Execute transfer
    const result = await transferGCode(machine, programName, gcode);

    // Update transfer record
    await pool.query(
      `UPDATE dnc_transfers
       SET status = $1, completed_at = now(), error_message = $2, checksum = $3
       WHERE id = $4`,
      [
        result.success ? "SUCCESS" : "FAILED",
        result.error ?? null,
        result.checksum,
        transferId,
      ]
    );

    // Update machine last_seen
    if (result.success) {
      await pool.query(`UPDATE machines SET last_seen = now(), status = 'ONLINE' WHERE id = $1`, [machineId]);
    } else {
      await pool.query(`UPDATE machines SET status = 'ERROR' WHERE id = $1`, [machineId]);
    }

    // Audit log
    await writeAuditLog({
      userId:       user!.id,
      userEmail:    user!.email,
      userRole:     user!.role,
      ipAddress:    req.headers.get("x-forwarded-for") ?? undefined,
      action:       "DNC_TRANSFER",
      resourceType: "machine",
      resourceId:   machineId,
      resourceName: `${m.name} / ${programName}`,
      details:      { transferId, success: result.success, bytes: result.bytesTransferred, error: result.error },
    });

    return Response.json({ transferId, ...result });

  } catch (err) {
    const status = (err as { status?: number }).status ?? 500;
    return Response.json({ error: err instanceof Error ? err.message : "Transfer failed" }, { status });
  }
}

export async function GET(req: NextRequest) {
  try {
    const user = await getAuthUser(req);
    requireRole(user, "OPERATOR");

    const pool = getPool();
    const { searchParams } = new URL(req.url);

    // Return machine list + ping status
    if (searchParams.get("action") === "ping") {
      const id   = searchParams.get("id");
      const mRes = await pool.query<{ id: string; host: string; port: number }>(
        `SELECT id, host, port FROM machines WHERE ${id ? "id = $1" : "is_active = true"}`,
        id ? [id] : []
      );
      const results = await Promise.all(
        mRes.rows.map(async (m) => ({
          id:     m.id,
          online: await pingMachine(m.host, m.port, 2000),
        }))
      );
      return Response.json({ machines: results });
    }

    // List machines
    const mRes = await pool.query(
      `SELECT id, name, display_name, controller, protocol, host, port,
              network_path, is_active, last_seen, status, notes
       FROM machines ORDER BY name`
    );

    // Recent transfers
    const tRes = await pool.query(
      `SELECT t.id, t.created_at, t.completed_at, t.machine_name, t.program_name,
              t.file_size_bytes, t.status, t.error_message, t.checksum,
              u.email AS transferred_by_email
       FROM dnc_transfers t
       LEFT JOIN users u ON u.id = t.transferred_by
       ORDER BY t.created_at DESC LIMIT 50`
    );

    return Response.json({ machines: mRes.rows, recentTransfers: tRes.rows });

  } catch (err) {
    const status = (err as { status?: number }).status ?? 500;
    return Response.json({ error: err instanceof Error ? err.message : "Failed" }, { status });
  }
}
