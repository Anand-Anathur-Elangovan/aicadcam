// Active RAG Memory Pipeline for Human G-Code Corrections
// Stores human edit summaries as vector embeddings so future similar parts
// retrieve relevant past corrections as few-shot context injections.

import { Pool } from "pg";
import { createEmbeddingProvider } from "../providers";
import type { GCodeDiff, FewShotExample } from "./diffEngine";
import { buildFewShotRules } from "./diffEngine";

declare const globalThis: { __pgPool?: Pool };

function getPool(): Pool {
  if (!globalThis.__pgPool) {
    globalThis.__pgPool = new Pool({ connectionString: process.env.DATABASE_URL });
  }
  return globalThis.__pgPool;
}

export interface DiffMemoryRecord {
  id:               string;
  partContext:      string;    // "Material: Ti-6Al-4V | Features: 3x holes, 1x pocket"
  editSummaryText:  string;    // human-readable list of changes
  editReason:       string;
  approvedGcode:    string;
  geometryHash:     string;
  embedding?:       number[];
}

// Build a text representation of a part for embedding
export function buildPartContextText(fields: {
  material?:   string;
  partType?:   string;
  features?:   Array<{ type: string; count: number; depth?: number; diameter?: number }>;
  tolerances?: Array<{ tolerance: string }>;
  machineType?: string;
}): string {
  const featureStr = fields.features
    ?.map((f) => `${f.count}x ${f.type}${f.diameter ? ` Ø${f.diameter}mm` : ""}${f.depth ? ` d=${f.depth}mm` : ""}`)
    .join(", ") ?? "";

  const tolStr = fields.tolerances
    ?.map((t) => t.tolerance)
    .join(", ") ?? "";

  return [
    fields.material   && `Material: ${fields.material}`,
    fields.partType   && `Type: ${fields.partType}`,
    featureStr        && `Features: ${featureStr}`,
    tolStr            && `Tolerances: ${tolStr}`,
    fields.machineType && `Machine: ${fields.machineType}`,
  ].filter(Boolean).join(" | ");
}

// ─── Store a new approved diff in vector memory ───────────────────────────────

export async function storeDiffInMemory(params: {
  diffId:         string;
  partContext:    string;
  diff:           GCodeDiff;
  editReason:     string;
  approvedGcode:  string;
  geometryHash:   string;
}): Promise<void> {
  const { diffId, partContext, diff, editReason, approvedGcode, geometryHash } = params;

  // Build embeddable text from the edit summaries
  const summaryText = diff.editSummary.map((s) => s.description).join(". ");
  const embeddableText = `${partContext} | Changes: ${summaryText}${editReason ? ` | Reason: ${editReason}` : ""}`;

  try {
    const embedding = await createEmbeddingProvider().embedBatch([embeddableText]);

    const pool = getPool();
    await pool.query(
      `UPDATE gcode_diffs
       SET embedded_in_vector = true
       WHERE id = $1`,
      [diffId]
    );

    // Store in the chunks table using the feedback category
    // Re-using the existing vector infrastructure (pgvector chunks table)
    await pool.query(
      `INSERT INTO chunks
       (document_id, chunk_index, content, content_type, embedding, metadata)
       SELECT id, -1, $2, 'feedback', $3, $4
       FROM documents
       WHERE category = 'GENERAL'
       LIMIT 1`,
      [
        embeddableText,
        JSON.stringify(embedding[0]),
        JSON.stringify({
          diffId,
          partContext,
          editSummary:  diff.editSummary,
          editReason,
          approvedGcode: approvedGcode.slice(0, 2000),  // truncate for metadata
          geometryHash,
          type: "gcode_feedback",
        }),
      ]
    );
  } catch (err) {
    // Non-fatal — log and continue
    console.error("[MemoryPipeline] Failed to embed diff:", err);
  }
}

// ─── Retrieve relevant past corrections for a new part ────────────────────────

export async function retrieveSimilarCorrections(
  partContext: string,
  geometryHash: string,
  topK = 3
): Promise<FewShotExample[]> {
  try {
    const embedding = await createEmbeddingProvider().embedBatch([partContext]);
    const pool = getPool();

    // First try exact geometry hash match (same part family)
    const exactRes = await pool.query<{
      metadata: Record<string, unknown>;
      content: string;
    }>(
      `SELECT metadata, content FROM chunks
       WHERE content_type = 'feedback'
         AND metadata->>'geometryHash' = $1
       ORDER BY created_at DESC
       LIMIT $2`,
      [geometryHash, topK]
    );

    if (exactRes.rows.length >= topK) {
      return exactRes.rows.map(rowToFewShot);
    }

    // Fall back to semantic similarity search
    const embVec = `[${embedding[0].join(",")}]`;
    const semRes = await pool.query<{
      metadata: Record<string, unknown>;
      content: string;
      similarity: number;
    }>(
      `SELECT metadata, content, 1 - (embedding <=> $1::vector) AS similarity
       FROM chunks
       WHERE content_type = 'feedback'
       ORDER BY embedding <=> $1::vector
       LIMIT $2`,
      [embVec, topK]
    );

    const combined = [...exactRes.rows, ...semRes.rows];
    const seen     = new Set<string>();
    const unique   = combined.filter((r) => {
      const id = (r.metadata as Record<string, unknown>)?.diffId as string;
      if (seen.has(id)) return false;
      seen.add(id);
      return true;
    });

    return unique.slice(0, topK).map(rowToFewShot);
  } catch (err) {
    console.error("[MemoryPipeline] Retrieval error:", err);
    return [];
  }
}

function rowToFewShot(row: { metadata: Record<string, unknown>; content: string }): FewShotExample {
  const m = row.metadata as {
    partContext?: string;
    editSummary?: Array<{ category: string; description: string; generatedValue?: string; approvedValue?: string }>;
    editReason?: string;
    approvedGcode?: string;
  };
  return {
    partContext:   m.partContext ?? row.content,
    editSummary:   (m.editSummary ?? []) as import("./diffEngine").EditSummary[],
    editReason:    m.editReason,
    approvedGcode: m.approvedGcode ?? "",
  };
}

// ─── Inject corrections into CAPP prompt ─────────────────────────────────────

export async function enrichPromptWithMemory(
  systemPrompt: string,
  partContext: string,
  geometryHash: string
): Promise<string> {
  const examples = await retrieveSimilarCorrections(partContext, geometryHash);
  if (!examples.length) return systemPrompt;
  const injection = buildFewShotRules(examples);
  return systemPrompt + injection;
}
