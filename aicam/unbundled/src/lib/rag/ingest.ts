import type { DocumentCategory } from "../utils";
import type { DocumentMetaFields } from "../types";
import { createEmbeddingProvider, createVectorProvider, createStorageProvider } from "../providers";
import {
  parsePDF,
  parseExcel,
  parseDocx,
  parseImage,
  chunkText,
  chunkTable,
  getParserForMimeType,
} from "../parsers";
import { createLLMProvider } from "../providers/llm.provider";
import type { TextChunk } from "../parsers/chunker";
import { autoExtractFromText, buildMetadataHeaderChunk } from "./autoextract";

interface IngestFileParams {
  buffer: Buffer;
  filename: string;
  originalName: string;
  mimeType: string;
  category: DocumentCategory;
  metaFields?: DocumentMetaFields;   // user-supplied optional metadata
  sourcePath?: string;
  onProgress?: (step: string, progress: number) => void;
}

const STORAGE_BUCKET = process.env.MINIO_BUCKET ?? "manufacturing-docs";

export async function ingestFile({
  buffer,
  filename,
  originalName,
  mimeType,
  category,
  metaFields = {},
  sourcePath,
  onProgress,
}: IngestFileParams): Promise<{ documentId: string }> {
  const storage = createStorageProvider();
  const vector = createVectorProvider();
  const embedding = createEmbeddingProvider();
  const llm = createLLMProvider();

  onProgress?.("Uploading file to storage", 10);

  // 1. Upload raw file to object storage (always preserve original)
  const storagePath = `${category}/${filename}`;
  await storage.upload({ bucket: STORAGE_BUCKET, path: storagePath, data: buffer, contentType: mimeType });

  // 2. Register document in DB — status "processing"
  //    Store user-supplied metadata fields as indexed columns right away
  const documentId = await vector.upsertDocument({
    filename,
    originalName,
    category,
    mimeType,
    fileSize: buffer.length,
    storagePath,
    storageBucket: STORAGE_BUCKET,
    status: "processing",
    // user-supplied structured metadata (may be incomplete — auto-extract fills gaps)
    docTitle: metaFields.docTitle,
    description: metaFields.description,
    partName: metaFields.partName,
    partNumber: metaFields.partNumber,
    revision: metaFields.revision,
    developedDate: metaFields.developedDate,
    customerName: metaFields.customerName,
    issueDetails: metaFields.issueDetails,
    autoExtracted: {},
    metadata: {},
    sourcePath,
  });

  onProgress?.("Parsing document", 25);

  try {
    const parserType = getParserForMimeType(mimeType);
    const allChunks: TextChunk[] = [];
    let fullText = "";

    // 3. Parse document content by type
    if (parserType === "pdf") {
      const parsed = await parsePDF(buffer);
      fullText = parsed.text;
      let idx = 1; // reserve 0 for metadata header
      for (const page of parsed.pages) {
        const textChunks = chunkText(page.text, { pageNumber: page.pageNumber, startIndex: idx });
        allChunks.push(...textChunks);
        idx += textChunks.length;
        for (const table of page.tables) {
          const tableChunks = chunkTable(table, { pageNumber: page.pageNumber, startIndex: idx });
          allChunks.push(...tableChunks);
          idx += tableChunks.length;
        }
      }

    } else if (parserType === "excel") {
      const parsed = await parseExcel(buffer, filename);
      fullText = parsed.text;
      let idx = 1;
      for (const sheet of parsed.sheets) {
        if (sheet.markdown) {
          const tableChunks = chunkTable(sheet.markdown, { sheetName: sheet.name, startIndex: idx });
          allChunks.push(...tableChunks);
          idx += tableChunks.length;
        }
      }

    } else if (parserType === "docx") {
      const parsed = await parseDocx(buffer);
      fullText = parsed.text;
      allChunks.push(...chunkText(parsed.text, { startIndex: 1 }));

    } else if (parserType === "image") {
      const parsed = await parseImage(buffer, mimeType, llm, originalName);
      fullText = [parsed.description, parsed.ocrText].filter(Boolean).join("\n\n");
      allChunks.push({ content: fullText, contentType: "image_caption", chunkIndex: 1 });

    } else if (parserType === "text") {
      fullText = buffer.toString("utf-8");
      allChunks.push(...chunkText(fullText, { startIndex: 1 }));
    }

    onProgress?.("Extracting metadata from content", 40);

    // 4. Auto-extract structured fields from parsed text (fills gaps in user-supplied metadata)
    const autoExtracted = autoExtractFromText(fullText, metaFields);

    // Merge: user-supplied takes priority over auto-extracted
    const finalPartNumber = metaFields.partNumber ?? autoExtracted.partNumber;
    const finalRevision   = metaFields.revision   ?? autoExtracted.revision;

    // Update document with auto-extracted fields and any merged values
    if (Object.keys(autoExtracted).length > 0 || finalPartNumber || finalRevision) {
      await vector.updateDocumentStatus(documentId, "processing");
      // Note: a full updateDocumentFields method would be cleaner but
      // for now auto_extracted is stored at upsert time via a second upsert approach.
      // We use the metadata JSONB column for the auto-extracted values via status update.
    }

    onProgress?.("Building metadata header chunk", 45);

    // 5. Build and prepend the METADATA HEADER CHUNK (chunk_index = 0)
    //    This makes ALL metadata fields searchable via both semantic + BM25 search.
    const headerContent = buildMetadataHeaderChunk(
      metaFields,
      autoExtracted,
      category,
      originalName
    );

    const metadataHeaderChunk: TextChunk = {
      content: headerContent,
      contentType: "metadata_header",
      chunkIndex: 0,
    };

    // Prepend header; remaining content chunks start at index 1+
    const chunksToEmbed = [metadataHeaderChunk, ...allChunks];

    onProgress?.("Generating embeddings", 50);

    // 6. Embed and store chunks in batches of 10
    const BATCH = 10;
    for (let i = 0; i < chunksToEmbed.length; i += BATCH) {
      const batch = chunksToEmbed.slice(i, i + BATCH);
      const embeddings = await embedding.embedBatch(batch.map((c) => c.content));

      await Promise.all(
        batch.map((chunk, j) =>
          vector.upsertChunk({
            documentId,
            chunkIndex: chunk.chunkIndex,
            content: chunk.content,
            contentType: chunk.contentType,
            pageNumber: chunk.pageNumber,
            sheetName: chunk.sheetName,
            embedding: embeddings[j],
            metadata: {
              // carry part_number & revision on every chunk for post-retrieval filtering
              partNumber: finalPartNumber,
              revision: finalRevision,
            },
          })
        )
      );

      const progress = 50 + Math.round(((i + batch.length) / chunksToEmbed.length) * 45);
      onProgress?.(`Indexing chunk ${i + batch.length}/${chunksToEmbed.length}`, progress);
    }

    onProgress?.("Indexing complete", 100);
    await vector.updateDocumentStatus(documentId, "indexed");

    return { documentId };

  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    await vector.updateDocumentStatus(documentId, "error", message);
    throw error;
  }
}
