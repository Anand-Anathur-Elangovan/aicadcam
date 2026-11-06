// POST /api/gcode/generate
// Accepts a ProcessPlan + PartFeatures JSON, generates machine-ready G-code.
// Returns SSE stream so the Studio editor can show generation progress.

import { NextRequest } from "next/server";
import { createCAPPProvider } from "@/lib/providers/capp.provider";
import type { ProcessPlan } from "@/lib/providers/capp.provider";
import type { PartFeatures } from "@/lib/cad/featureExtractor";

export const runtime    = "nodejs";
export const maxDuration = 120;

export async function POST(req: NextRequest) {
  try {
    const body = await req.json() as {
      plan: ProcessPlan;
      features: PartFeatures;
      targetMachine?: string;
    };

    const { plan, features, targetMachine = "Fanuc 3-Axis VMC" } = body;

    if (!plan || !features) {
      return Response.json({ error: "Both 'plan' and 'features' are required" }, { status: 400 });
    }

    const encoder = new TextEncoder();

    const stream = new ReadableStream({
      async start(controller) {
        const send = (data: object) =>
          controller.enqueue(encoder.encode(`data: ${JSON.stringify(data)}\n\n`));

        try {
          send({ step: "Initializing G-code generator", progress: 10 });

          const capp = createCAPPProvider();

          send({ step: "Generating G-code program", progress: 30 });
          const gcode = await capp.generateGCode(plan, features, targetMachine);

          send({ step: "Post-processing output", progress: 90 });

          // Validate has key markers
          const hasProgram = gcode.includes("M30") || gcode.includes("M02");
          if (!hasProgram) {
            send({ step: "Warning: G-code may be incomplete", progress: 95 });
          }

          send({ step: "Complete", progress: 100, gcode });

        } catch (err) {
          const message = err instanceof Error ? err.message : "G-code generation failed";
          send({ step: "Error", progress: 0, error: message });
        } finally {
          controller.close();
        }
      },
    });

    return new Response(stream, {
      headers: {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "Connection":    "keep-alive",
      },
    });

  } catch (err) {
    console.error("[/api/gcode/generate] Error:", err);
    return Response.json(
      { error: err instanceof Error ? err.message : "Failed" },
      { status: 500 }
    );
  }
}
