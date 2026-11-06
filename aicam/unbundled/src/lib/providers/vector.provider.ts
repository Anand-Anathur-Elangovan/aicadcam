import type { VectorProvider, Chunk, Document, RetrievedChunk } from "../types";
import type { DocumentCategory } from "../utils";

// ─── Supabase / Cloud PostgreSQL + pgvector ───────────────────────────────────
class SupabaseVectorProvider implements VectorProvider {
  private get client() {
    const { createClient } = require("@supabase/supabase-js");
    return createClient(process.env.SUPABASE_URL!, process.env.SUPABASE_SERVICE_ROLE_KEY!);
  }

  async upsertDocument(doc: Omit<Document, "id" | "createdAt" | "updatedAt">): Promise<string> {
    const { data, error } = await this.client
      .from("documents")
      .insert({
        filename: doc.filename,
        original_name: doc.originalName,
        category: doc.category,
        mime_type: doc.mimeType,
        file_size: doc.fileSize,
        storage_path: doc.storagePath,
        storage_bucket: doc.storageBucket,
        page_count: doc.pageCount,
        status: doc.status,
        // structured metadata
        doc_title: doc.docTitle,
        description: doc.description,
        part_name: doc.partName,
        part_number: doc.partNumber,
        revision: doc.revision,
        developed_date: doc.developedDate ?? null,
        customer_name: doc.customerName,
        issue_details: doc.issueDetails,
        auto_extracted: doc.autoExtracted ?? {},
        metadata: doc.metadata,
        source_path: doc.sourcePath,
      })
      .select("id")
      .single();

    if (error) throw new Error(`Supabase upsertDocument: ${error.message}`);
    return data.id;
  }

  async updateDocumentStatus(id: string, status: Document["status"], errorMessage?: string): Promise<void> {
    const { error } = await this.client
      .from("documents")
      .update({ status, error_message: errorMessage })
      .eq("id", id);
    if (error) throw new Error(`Supabase updateDocumentStatus: ${error.message}`);
  }

  async getDocument(id: string): Promise<Document | null> {
    const { data, error } = await this.client.from("documents").select("*").eq("id", id).single();
    if (error) return null;
    return mapDocumentRow(data);
  }

  async listDocuments(params?: { category?: DocumentCategory; status?: Document["status"] }): Promise<Document[]> {
    let query = this.client.from("documents").select("*").order("created_at", { ascending: false });
    if (params?.category) query = query.eq("category", params.category);
    if (params?.status) query = query.eq("status", params.status);
    const { data, error } = await query;
    if (error) throw new Error(`Supabase listDocuments: ${error.message}`);
    return (data ?? []).map(mapDocumentRow);
  }

  async upsertChunk(chunk: Omit<Chunk, "id" | "createdAt"> & { embedding: number[] }): Promise<string> {
    const embeddingStr = `[${chunk.embedding.join(",")}]`;
    const { data, error } = await this.client
      .from("chunks")
      .insert({
        document_id: chunk.documentId,
        chunk_index: chunk.chunkIndex,
        content: chunk.content,
        content_type: chunk.contentType,
        page_number: chunk.pageNumber,
        sheet_name: chunk.sheetName,
        bounding_box: chunk.boundingBox,
        embedding: embeddingStr,
        metadata: chunk.metadata,
      })
      .select("id")
      .single();

    if (error) throw new Error(`Supabase upsertChunk: ${error.message}`);
    return data.id;
  }

  async hybridSearch(params: {
    queryEmbedding: number[];
    queryText: string;
    limit: number;
    filterCategory?: DocumentCategory;
    filterPartNumber?: string;
  }): Promise<RetrievedChunk[]> {
    const embeddingStr = `[${params.queryEmbedding.join(",")}]`;
    const { data, error } = await this.client.rpc("hybrid_search", {
      query_embedding: embeddingStr,
      query_text: params.queryText,
      match_count: params.limit,
      filter_category: params.filterCategory ?? null,
      filter_part_number: params.filterPartNumber ?? null,
    });

    if (error) throw new Error(`Supabase hybridSearch: ${error.message}`);
    return (data ?? []).map(mapHybridRow);
  }

  async deleteByDocumentId(documentId: string): Promise<void> {
    const { error } = await this.client.from("chunks").delete().eq("document_id", documentId);
    if (error) throw new Error(`Supabase deleteByDocumentId: ${error.message}`);
  }
}

// ─── On-Prem PostgreSQL + pgvector ───────────────────────────────────────────
class LocalPostgresVectorProvider implements VectorProvider {
  private get connectionString() {
    return process.env.ONPREM_POSTGRES_URL ?? "postgresql://postgres:postgres@localhost:5432/manufacturing_rag";
  }

  private async getPool() {
    const { Pool } = await import("pg");
    if (!(globalThis as Record<string, unknown>).__pgPool) {
      (globalThis as Record<string, unknown>).__pgPool = new Pool({ connectionString: this.connectionString });
    }
    return (globalThis as Record<string, unknown>).__pgPool as InstanceType<typeof Pool>;
  }

  async upsertDocument(doc: Omit<Document, "id" | "createdAt" | "updatedAt">): Promise<string> {
    const pool = await this.getPool();
    const { rows } = await pool.query(
      `INSERT INTO documents (
        filename, original_name, category, mime_type, file_size,
        storage_path, storage_bucket, page_count, status,
        doc_title, description, part_name, part_number, revision,
        developed_date, customer_name, issue_details, auto_extracted, metadata, source_path
      ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20)
      RETURNING id`,
      [
        doc.filename, doc.originalName, doc.category, doc.mimeType, doc.fileSize,
        doc.storagePath, doc.storageBucket, doc.pageCount, doc.status,
        doc.docTitle, doc.description, doc.partName, doc.partNumber, doc.revision,
        doc.developedDate ?? null, doc.customerName, doc.issueDetails,
        JSON.stringify(doc.autoExtracted ?? {}),
        JSON.stringify(doc.metadata), doc.sourcePath,
      ]
    );
    return rows[0].id;
  }

  async updateDocumentStatus(id: string, status: Document["status"], errorMessage?: string): Promise<void> {
    const pool = await this.getPool();
    await pool.query("UPDATE documents SET status=$1, error_message=$2 WHERE id=$3", [status, errorMessage, id]);
  }

  async getDocument(id: string): Promise<Document | null> {
    const pool = await this.getPool();
    const { rows } = await pool.query("SELECT * FROM documents WHERE id=$1", [id]);
    return rows[0] ? mapDocumentRow(rows[0]) : null;
  }

  async listDocuments(params?: { category?: DocumentCategory; status?: Document["status"] }): Promise<Document[]> {
    const pool = await this.getPool();
    const conditions: string[] = [];
    const values: unknown[] = [];

    if (params?.category) { conditions.push(`category=$${values.length + 1}`); values.push(params.category); }
    if (params?.status)   { conditions.push(`status=$${values.length + 1}`);   values.push(params.status); }

    const where = conditions.length ? `WHERE ${conditions.join(" AND ")}` : "";
    const { rows } = await pool.query(`SELECT * FROM documents ${where} ORDER BY created_at DESC`, values);
    return rows.map(mapDocumentRow);
  }

  async upsertChunk(chunk: Omit<Chunk, "id" | "createdAt"> & { embedding: number[] }): Promise<string> {
    const pool = await this.getPool();
    const embeddingStr = `[${chunk.embedding.join(",")}]`;
    const { rows } = await pool.query(
      `INSERT INTO chunks (document_id, chunk_index, content, content_type, page_number,
        sheet_name, bounding_box, embedding, metadata)
       VALUES ($1,$2,$3,$4,$5,$6,$7,$8::vector,$9) RETURNING id`,
      [chunk.documentId, chunk.chunkIndex, chunk.content, chunk.contentType,
       chunk.pageNumber, chunk.sheetName, JSON.stringify(chunk.boundingBox),
       embeddingStr, JSON.stringify(chunk.metadata)]
    );
    return rows[0].id;
  }

  async hybridSearch(params: {
    queryEmbedding: number[];
    queryText: string;
    limit: number;
    filterCategory?: DocumentCategory;
    filterPartNumber?: string;
  }): Promise<RetrievedChunk[]> {
    const pool = await this.getPool();
    const embeddingStr = `[${params.queryEmbedding.join(",")}]`;
    const { rows } = await pool.query(
      "SELECT * FROM hybrid_search($1::vector, $2, $3, 60, $4, $5)",
      [embeddingStr, params.queryText, params.limit,
       params.filterCategory ?? null, params.filterPartNumber ?? null]
    );
    return rows.map(mapHybridRow);
  }

  async deleteByDocumentId(documentId: string): Promise<void> {
    const pool = await this.getPool();
    await pool.query("DELETE FROM chunks WHERE document_id=$1", [documentId]);
  }
}

// ─── Shared Helpers ───────────────────────────────────────────────────────────
interface HybridRow {
  chunk_id: string;
  document_id: string;
  content: string;
  page_number?: number;
  content_type?: string;
  rrf_score: number;
  dense_rank: number;
  sparse_rank: number;
}

function mapHybridRow(row: HybridRow): RetrievedChunk {
  return {
    chunkId: row.chunk_id,
    documentId: row.document_id,
    content: row.content,
    pageNumber: row.page_number,
    contentType: row.content_type,
    rrfScore: row.rrf_score,
    denseRank: row.dense_rank,
    sparseRank: row.sparse_rank,
  };
}

function mapDocumentRow(row: Record<string, unknown>): Document {
  return {
    id: row.id as string,
    filename: row.filename as string,
    originalName: row.original_name as string,
    category: row.category as DocumentCategory,
    mimeType: row.mime_type as string | undefined,
    fileSize: row.file_size as number | undefined,
    storagePath: row.storage_path as string,
    storageBucket: row.storage_bucket as string,
    pageCount: row.page_count as number | undefined,
    status: row.status as Document["status"],
    errorMessage: row.error_message as string | undefined,
    // structured metadata
    docTitle: row.doc_title as string | undefined,
    description: row.description as string | undefined,
    partName: row.part_name as string | undefined,
    partNumber: row.part_number as string | undefined,
    revision: row.revision as string | undefined,
    developedDate: row.developed_date as string | undefined,
    customerName: row.customer_name as string | undefined,
    issueDetails: row.issue_details as string | undefined,
    autoExtracted: (row.auto_extracted as Record<string, string>) ?? {},
    metadata: (row.metadata as Record<string, unknown>) ?? {},
    sourcePath: row.source_path as string | undefined,
    createdAt: row.created_at as string,
    updatedAt: row.updated_at as string,
  };
}

// ─── Factory ─────────────────────────────────────────────────────────────────
export function createVectorProvider(): VectorProvider {
  const mode = process.env.DEPLOYMENT_MODE ?? "CLOUD";
  return mode === "ON_PREM" ? new LocalPostgresVectorProvider() : new SupabaseVectorProvider();
}
