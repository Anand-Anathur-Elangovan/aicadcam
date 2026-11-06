// POST /api/process/generate
// Accepts a file (STEP/PDF/image) or pre-extracted text + metadata,
// runs feature extraction, then generates a CAPP process plan via AI.

import { NextRequest } from "next/server";
import { createCAPPProvider } from "@/lib/providers/capp.provider";
import { extractFeatures } from "@/lib/cad/featureExtractor";
import type { PartFeatures } from "@/lib/cad/featureExtractor";

export const runtime    = "nodejs";
export const maxDuration = 120;

export async function POST(req: NextRequest) {
  try {
    const formData = await req.formData();

    // Resolve features: accept pre-built JSON OR a raw file for extraction
    let features: PartFeatures;
    const featuresJson = formData.get("features") as string | null;

    if (featuresJson) {
      // Caller already ran feature extraction (e.g., Studio client)
      features = JSON.parse(featuresJson) as PartFeatures;
    } else {
      const file = formData.get("file") as File | null;
      if (!file) {
        return Response.json({ error: "Provide either 'features' JSON or a 'file'" }, { status: 400 });
      }

      const buffer = Buffer.from(await file.arrayBuffer());
      const textContent = formData.get("textContent") as string | undefined ?? undefined;

      const metaOverrides = {
        partName:   getString(formData, "partName"),
        partNumber: getString(formData, "partNumber"),
        revision:   getString(formData, "revision"),
        material:   getString(formData, "material"),
      };

      features = extractFeatures(buffer, file.type, file.name, textContent, metaOverrides);
    }

    // Generate process plan
    const capp = createCAPPProvider();
    const plan = await capp.generateProcessPlan(features);

    return Response.json({ plan, features });

  } catch (err) {
    console.error("[/api/process/generate] Error:", err);
    return Response.json(
      { error: err instanceof Error ? err.message : "Generation failed" },
      { status: 500 }
    );
  }
}

function getString(fd: FormData, key: string): string | undefined {
  const val = fd.get(key);
  return typeof val === "string" && val.trim() ? val.trim() : undefined;
}
