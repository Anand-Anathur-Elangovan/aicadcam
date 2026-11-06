import { NextRequest } from "next/server";
import { ingestFile } from "@/lib/rag/ingest";
import { createVectorProvider } from "@/lib/providers/vector.provider";
import type { DocumentCategory } from "@/lib/utils";
import { DOCUMENT_CATEGORIES } from "@/lib/utils";
import type { DocumentMetaFields } from "@/lib/types";

export const runtime = "nodejs";
export const maxDuration = 300;

// POST /api/ingest — Upload, parse, and index a document
export async function POST(req: NextRequest) {
  try {
    const formData = await req.formData();

    const file = formData.get("file") as File | null;
    const category = (formData.get("category") as DocumentCategory) ?? "GENERAL";

    if (!file) return Response.json({ error: "No file provided" }, { status: 400 });
    if (!DOCUMENT_CATEGORIES.includes(category)) {
      return Response.json({ error: `Invalid category: ${category}` }, { status: 400 });
    }

    // Parse optional structured metadata fields from form
    const metaFields: DocumentMetaFields = {
      docTitle:      getString(formData, "docTitle"),
      description:   getString(formData, "description"),
      partName:      getString(formData, "partName"),
      partNumber:    getString(formData, "partNumber"),
      revision:      getString(formData, "revision"),
      developedDate: getString(formData, "developedDate"),
      customerName:  getString(formData, "customerName"),
      issueDetails:  getString(formData, "issueDetails"),
    };

    const buffer = Buffer.from(await file.arrayBuffer());
    const filename = `${Date.now()}_${file.name.replace(/[^a-zA-Z0-9._-]/g, "_")}`;

    const encoder = new TextEncoder();
    const stream = new ReadableStream({
      async start(controller) {
        const send = (step: string, progress: number, extra?: object) => {
          controller.enqueue(encoder.encode(`data: ${JSON.stringify({ step, progress, ...extra })}\n\n`));
        };

        try {
          let documentId = "";
          await ingestFile({
            buffer,
            filename,
            originalName: file.name,
            mimeType: file.type || "application/octet-stream",
            category,
            metaFields,
            onProgress: (step, progress) => send(step, progress),
          }).then((r) => { documentId = r.documentId; });

          send("Complete", 100, { documentId });
        } catch (err) {
          const message = err instanceof Error ? err.message : "Ingestion failed";
          controller.enqueue(encoder.encode(`data: ${JSON.stringify({ step: "Error", progress: 0, error: message })}\n\n`));
        } finally {
          controller.close();
        }
      },
    });

    return new Response(stream, {
      headers: {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
      },
    });
  } catch (err) {
    console.error("[/api/ingest] Error:", err);
    return Response.json({ error: err instanceof Error ? err.message : "Failed" }, { status: 500 });
  }
}

// GET /api/ingest — List documents with optional filters
export async function GET(req: NextRequest) {
  try {
    const { searchParams } = new URL(req.url);
    const category    = searchParams.get("category") as DocumentCategory | null;
    const status      = searchParams.get("status") as "pending" | "processing" | "indexed" | "error" | null;
    const vector      = createVectorProvider();
    const documents   = await vector.listDocuments({
      ...(category ? { category } : {}),
      ...(status   ? { status }   : {}),
    });
    return Response.json({ documents });
  } catch (err) {
    console.error("[/api/ingest GET] Error:", err);
    return Response.json({ error: err instanceof Error ? err.message : "Failed" }, { status: 500 });
  }
}

function getString(fd: FormData, key: string): string | undefined {
  const val = fd.get(key);
  return typeof val === "string" && val.trim() ? val.trim() : undefined;
}
