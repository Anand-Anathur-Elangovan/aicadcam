import { NextRequest } from "next/server";
import { createStorageProvider } from "@/lib/providers/storage.provider";
import { createVectorProvider } from "@/lib/providers/vector.provider";

export const runtime = "nodejs";

// GET /api/files/download/[id] — Securely stream a file from storage
export async function GET(
  _req: NextRequest,
  context: { params: Promise<{ id: string }> }
) {
  const { id } = await context.params;

  if (!id) {
    return Response.json({ error: "Document ID required" }, { status: 400 });
  }

  try {
    const vector = createVectorProvider();
    const doc = await vector.getDocument(id);

    if (!doc) {
      return Response.json({ error: "Document not found" }, { status: 404 });
    }

    const storage = createStorageProvider();
    const buffer = await storage.download(doc.storageBucket, doc.storagePath);

    return new Response(new Uint8Array(buffer), {
      headers: {
        "Content-Type": doc.mimeType ?? "application/octet-stream",
        "Content-Disposition": `attachment; filename="${encodeURIComponent(doc.originalName)}"`,
        "Content-Length": String(buffer.length),
        "Cache-Control": "private, max-age=3600",
      },
    });
  } catch (err) {
    console.error("[/api/files/download] Error:", err);
    return Response.json({ error: err instanceof Error ? err.message : "Download failed" }, { status: 500 });
  }
}

// GET /api/files/view/[id]?page=N — Return signed/presigned URL for inline viewing
export async function HEAD(
  _req: NextRequest,
  context: { params: Promise<{ id: string }> }
) {
  const { id } = await context.params;

  try {
    const vector = createVectorProvider();
    const doc = await vector.getDocument(id);
    if (!doc) return new Response(null, { status: 404 });

    const storage = createStorageProvider();
    const signedUrl = await storage.getSignedUrl(doc.storageBucket, doc.storagePath, 3600);

    return new Response(null, {
      status: 200,
      headers: { "X-Signed-URL": signedUrl },
    });
  } catch (err) {
    return new Response(null, { status: 500 });
  }
}
