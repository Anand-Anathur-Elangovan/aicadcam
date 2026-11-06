import type { DocumentCategory } from "./utils";

// ── Optional structured metadata fields (user-supplied or auto-extracted) ──
export interface DocumentMetaFields {
  docTitle?: string;
  description?: string;
  partName?: string;
  partNumber?: string;
  revision?: string;
  developedDate?: string;
  customerName?: string;
  issueDetails?: string;
}

export interface Document {
  id: string;
  filename: string;
  originalName: string;
  category: DocumentCategory;
  mimeType?: string;
  fileSize?: number;
  storagePath: string;
  storageBucket: string;
  pageCount?: number;
  status: "pending" | "processing" | "indexed" | "error";
  errorMessage?: string;
  // Structured metadata fields
  docTitle?: string;
  description?: string;
  partName?: string;
  partNumber?: string;
  revision?: string;
  developedDate?: string;
  customerName?: string;
  issueDetails?: string;
  autoExtracted?: Record<string, string>;  // LLM/regex auto-extracted fields
  metadata: Record<string, unknown>;
  sourcePath?: string;
  createdAt: string;
  updatedAt: string;
}

export interface Chunk {
  id: string;
  documentId: string;
  chunkIndex: number;
  content: string;
  contentType: "text" | "table" | "image_caption" | "ocr" | "metadata_header";
  pageNumber?: number;
  sheetName?: string;
  boundingBox?: { x: number; y: number; width: number; height: number };
  embedding?: number[];
  metadata: Record<string, unknown>;
  createdAt: string;
}

export interface RetrievedChunk {
  chunkId: string;
  documentId: string;
  content: string;
  pageNumber?: number;
  contentType?: string;
  rrfScore: number;
  denseRank: number;
  sparseRank: number;
  document?: Document;
}

export interface Citation {
  index: number;
  chunkId: string;
  documentId: string;
  documentName: string;
  category: DocumentCategory;
  pageNumber?: number;
  sheetName?: string;
  partNumber?: string;
  revision?: string;
  snippet: string;
  downloadUrl: string;
  viewUrl: string;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations?: Citation[];
  createdAt: string;
}

export interface IngestJobStatus {
  documentId: string;
  filename: string;
  status: "pending" | "processing" | "indexed" | "error";
  progress: number;
  errorMessage?: string;
}

// Provider interfaces
export interface LLMProvider {
  streamChat(params: {
    messages: { role: string; content: string }[];
    systemPrompt: string;
  }): AsyncIterable<string>;
  chat(params: {
    messages: { role: string; content: string }[];
    systemPrompt: string;
  }): Promise<string>;
}

export interface EmbeddingProvider {
  embed(text: string): Promise<number[]>;
  embedBatch(texts: string[]): Promise<number[][]>;
  dimensions: number;
}

export interface RerankProvider {
  rerank(query: string, documents: string[]): Promise<{ index: number; score: number }[]>;
}

export interface VectorProvider {
  upsertChunk(chunk: Omit<Chunk, "id" | "createdAt"> & { embedding: number[] }): Promise<string>;
  hybridSearch(params: {
    queryEmbedding: number[];
    queryText: string;
    limit: number;
    filterCategory?: DocumentCategory;
    filterPartNumber?: string;
  }): Promise<RetrievedChunk[]>;
  deleteByDocumentId(documentId: string): Promise<void>;
  upsertDocument(doc: Omit<Document, "id" | "createdAt" | "updatedAt">): Promise<string>;
  updateDocumentStatus(id: string, status: Document["status"], errorMessage?: string): Promise<void>;
  getDocument(id: string): Promise<Document | null>;
  listDocuments(params?: { category?: DocumentCategory; status?: Document["status"] }): Promise<Document[]>;
}

export interface StorageProvider {
  upload(params: {
    bucket: string;
    path: string;
    data: Buffer;
    contentType: string;
  }): Promise<{ path: string; url: string }>;
  download(bucket: string, path: string): Promise<Buffer>;
  getSignedUrl(bucket: string, path: string, expiresIn?: number): Promise<string>;
  delete(bucket: string, path: string): Promise<void>;
  list(bucket: string, prefix?: string): Promise<{ name: string; size: number }[]>;
}
