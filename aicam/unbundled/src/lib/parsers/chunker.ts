export interface TextChunk {
  content: string;
  contentType: "text" | "table" | "image_caption" | "ocr" | "metadata_header";
  pageNumber?: number;
  sheetName?: string;
  chunkIndex: number;
}

const CHUNK_SIZE = parseInt(process.env.CHUNK_SIZE ?? "512");
const CHUNK_OVERLAP = parseInt(process.env.CHUNK_OVERLAP ?? "64");

/**
 * Split text into overlapping chunks by character count.
 * Preserves sentence boundaries where possible.
 */
export function chunkText(
  text: string,
  options: {
    contentType?: TextChunk["contentType"];
    pageNumber?: number;
    sheetName?: string;
    startIndex?: number;
  } = {}
): TextChunk[] {
  const { contentType = "text", pageNumber, sheetName, startIndex = 0 } = options;
  const chunks: TextChunk[] = [];

  // Split into sentences first to avoid mid-sentence cuts
  const sentences = text.split(/(?<=[.!?])\s+/);
  let current = "";
  let chunkIndex = startIndex;

  for (const sentence of sentences) {
    if ((current + " " + sentence).length > CHUNK_SIZE && current.length > 0) {
      chunks.push({ content: current.trim(), contentType, pageNumber, sheetName, chunkIndex });
      // Overlap: keep last N characters
      current = current.slice(Math.max(0, current.length - CHUNK_OVERLAP)) + " " + sentence;
      chunkIndex++;
    } else {
      current = current ? current + " " + sentence : sentence;
    }
  }

  if (current.trim()) {
    chunks.push({ content: current.trim(), contentType, pageNumber, sheetName, chunkIndex });
  }

  return chunks;
}

/**
 * Chunk a table (markdown string) as a single atomic unit.
 * Tables are never split mid-row to preserve structural integrity.
 */
export function chunkTable(
  markdown: string,
  options: { pageNumber?: number; sheetName?: string; startIndex?: number } = {}
): TextChunk[] {
  const { pageNumber, sheetName, startIndex = 0 } = options;

  // If table fits in one chunk, keep it whole
  if (markdown.length <= CHUNK_SIZE * 2) {
    return [{ content: markdown, contentType: "table", pageNumber, sheetName, chunkIndex: startIndex }];
  }

  // Split large tables by row groups
  const lines = markdown.split("\n");
  const header = lines.slice(0, 2).join("\n"); // header + separator
  const dataRows = lines.slice(2);
  const chunks: TextChunk[] = [];
  let rowBatch: string[] = [];
  let idx = startIndex;

  for (const row of dataRows) {
    rowBatch.push(row);
    const content = [header, ...rowBatch].join("\n");
    if (content.length > CHUNK_SIZE) {
      chunks.push({ content, contentType: "table", pageNumber, sheetName, chunkIndex: idx });
      rowBatch = [];
      idx++;
    }
  }

  if (rowBatch.length > 0) {
    chunks.push({
      content: [header, ...rowBatch].join("\n"),
      contentType: "table",
      pageNumber,
      sheetName,
      chunkIndex: idx,
    });
  }

  return chunks;
}
