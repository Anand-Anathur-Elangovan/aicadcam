// GET /api/audit — Query immutable audit log (ADMIN only)

import { NextRequest } from "next/server";
import { queryAuditLogs } from "@/lib/security/audit";
import { getAuthUser, requireRole } from "@/lib/security/rbac";
import type { AuditAction } from "@/lib/security/audit";

export const runtime = "nodejs";

export async function GET(req: NextRequest) {
  try {
    const user = await getAuthUser(req);
    requireRole(user, "ADMIN");

    const { searchParams } = new URL(req.url);

    const result = await queryAuditLogs({
      userId:     searchParams.get("userId")     ?? undefined,
      action:     (searchParams.get("action")    ?? undefined) as AuditAction | undefined,
      resourceId: searchParams.get("resourceId") ?? undefined,
      from:       searchParams.get("from")       ?? undefined,
      to:         searchParams.get("to")         ?? undefined,
      limit:      parseInt(searchParams.get("limit")  ?? "100", 10),
      offset:     parseInt(searchParams.get("offset") ?? "0",   10),
    });

    return Response.json(result);
  } catch (err) {
    const status = (err as { status?: number }).status ?? 500;
    return Response.json({ error: err instanceof Error ? err.message : "Failed" }, { status });
  }
}
