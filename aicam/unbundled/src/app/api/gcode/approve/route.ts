// POST /api/gcode/approve
// Called when an engineer finishes editing the AI-generated G-code and clicks "Approve & Train".
// 1. Diffs generated vs approved G-code
// 2. Stores the diff snapshot in gcode_diffs table
// 3. Embeds the edit summary into vector memory for future retrieval
// 4. Writes an immutable audit log entry
// Returns: { diffId, stats, editSummary }

import { NextRequest } from "next/server";
import { Pool } from "pg";
import { diffGCode, hashPartFeatures } from "@/lib/feedback/diffEngine";
import { storeDiffInMemory, buildPartContextText } from "@/lib/feedback/memoryPipeline";
import { writeAuditLog } from "@/lib/security/audit";
import { getAuthUser } from "@/lib/security/rbac";

export const runtime    = "nodejs";
export const maxDuration = 60;

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

    const body = await req.json() as {
      generatedGcode:  string;
      approvedGcode:   string;
      editReason?:     string;
      qualityRating?:  number;   // 1-5
      features?: {
        partNumber?: string;
        partName?:   string;
        revision?:   string;
        material?:   string;
        machineType?: string;
        partType?:   string;
        features?:   Array<{ type: string; count: number; depth?: number; diameter?: number }>;
        tolerances?: Array<{ tolerance: string }>;
      };
      planJson?: Record<string, unknown>;
    };

    const { generatedGcode, approvedGcode, editReason, qualityRating, features, planJson } = body;

    if (!generatedGcode || !approvedGcode) {
      return Response.json({ error: "Both generatedGcode and approvedGcode are required" }, { status: 400 });
    }

    // 1. Compute diff
    const diff = diffGCode(generatedGcode, approvedGcode);

    // 2. Compute geometry hash
    const geometryHash = hashPartFeatures(features ?? {});

    // 3. Build part context string for embedding
    const partContext = buildPartContextText({
      material:    features?.material,
      partType:    features?.partType,
      features:    features?.features,
      tolerances:  features?.tolerances,
      machineType: features?.machineType,
    });

    // 4. Store diff in gcode_diffs table
    const pool = getPool();
    const res  = await pool.query<{ id: string }>(
      `INSERT INTO gcode_diffs
       (approved_by_user_id, approved_by_email, part_number, part_name, revision,
        material, machine_type, part_geometry_hash,
        generated_gcode, generated_plan_json, approved_gcode, edit_reason,
        total_lines_generated, total_lines_approved,
        lines_added, lines_removed, lines_unchanged,
        diff_summary, quality_rating, approved_at)
       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,now())
       RETURNING id`,
      [
        user?.id   ?? null,
        user?.email ?? null,
        features?.partNumber ?? null,
        features?.partName   ?? null,
        features?.revision   ?? null,
        features?.material   ?? null,
        features?.machineType ?? null,
        geometryHash,
        generatedGcode,
        planJson ? JSON.stringify(planJson) : null,
        approvedGcode,
        editReason ?? null,
        diff.totalGenerated,
        diff.totalApproved,
        diff.linesAdded,
        diff.linesRemoved,
        diff.linesUnchanged,
        JSON.stringify(diff.lines.filter((l) => l.type !== "equal").slice(0, 200)),
        qualityRating ?? null,
      ]
    );

    const diffId = res.rows[0].id;

    // 5. Embed into vector memory (non-blocking, best-effort)
    if (diff.linesAdded > 0 || diff.linesRemoved > 0) {
      storeDiffInMemory({
        diffId,
        partContext,
        diff,
        editReason:    editReason ?? "",
        approvedGcode,
        geometryHash,
      }).catch((err) => console.error("[approve] Memory pipeline error:", err));
    }

    // 6. Immutable audit log
    await writeAuditLog({
      userId:       user?.id,
      userEmail:    user?.email,
      userRole:     user?.role,
      ipAddress:    req.headers.get("x-forwarded-for") ?? req.headers.get("x-real-ip") ?? undefined,
      userAgent:    req.headers.get("user-agent") ?? undefined,
      action:       "GCODE_APPROVE",
      resourceType: "gcode_diff",
      resourceId:   diffId,
      resourceName: features?.partNumber ?? "unknown",
      details: {
        linesAdded:    diff.linesAdded,
        linesRemoved:  diff.linesRemoved,
        editSummary:   diff.editSummary,
        qualityRating,
      },
    });

    return Response.json({
      diffId,
      stats: {
        linesAdded:     diff.linesAdded,
        linesRemoved:   diff.linesRemoved,
        linesUnchanged: diff.linesUnchanged,
        totalGenerated: diff.totalGenerated,
        totalApproved:  diff.totalApproved,
      },
      editSummary: diff.editSummary,
      embeddedInMemory: diff.linesAdded > 0 || diff.linesRemoved > 0,
    });

  } catch (err) {
    console.error("[/api/gcode/approve] Error:", err);
    return Response.json(
      { error: err instanceof Error ? err.message : "Approval failed" },
      { status: 500 }
    );
  }
}

// GET /api/gcode/approve — list past approved diffs
export async function GET(req: NextRequest) {
  try {
    const { searchParams } = new URL(req.url);
    const partNumber = searchParams.get("partNumber");
    const limit      = parseInt(searchParams.get("limit") ?? "20", 10);
    const offset     = parseInt(searchParams.get("offset") ?? "0", 10);

    const pool = getPool();
    const where = partNumber ? "WHERE part_number = $3" : "";
    const values: (string | number)[] = partNumber
      ? [limit, offset, partNumber]
      : [limit, offset];

    const res = await pool.query(
      `SELECT id, approved_at, approved_by_email, part_number, part_name,
              material, machine_type, lines_added, lines_removed, lines_unchanged,
              edit_reason, quality_rating, embedded_in_vector, used_in_training
       FROM gcode_diffs
       ${where}
       ORDER BY approved_at DESC NULLS LAST
       LIMIT $1 OFFSET $2`,
      values
    );

    return Response.json({ diffs: res.rows, limit, offset });
  } catch (err) {
    return Response.json({ error: err instanceof Error ? err.message : "Query failed" }, { status: 500 });
  }
}
