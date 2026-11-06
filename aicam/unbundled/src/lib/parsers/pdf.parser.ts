export interface ParsedPage {
  pageNumber: number;
  text: string;
  tables: string[];
}

export interface ParsedDocument {
  text: string;
  pages: ParsedPage[];
  pageCount: number;
  metadata: Record<string, unknown>;
}

/**
 * Extract text and tables from a PDF buffer.
 * Uses pdf-parse for text extraction (server-side safe).
 */
export async function parsePDF(buffer: Buffer): Promise<ParsedDocument> {
  // pdf-parse has CJS/ESM interop issues; use require for reliable import
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const pdfParseFn = require("pdf-parse") as (buf: Buffer, opts?: object) => Promise<{ text: string; numpages: number; info: unknown }>;

  const pageTexts: string[] = [];

  const data = await pdfParseFn(buffer, {
    pagerender: (pageData: { getTextContent: () => Promise<{ items: { str: string; transform: number[] }[] }> }) => {
      return pageData.getTextContent().then((textContent) => {
        let lastY: number | null = null;
        let text = "";
        for (const item of textContent.items) {
          if (lastY !== null && Math.abs(item.transform[5] - lastY) > 5) {
            text += "\n";
          }
          text += item.str;
          lastY = item.transform[5];
        }
        pageTexts.push(text);
        return text;
      });
    },
  });

  const pages: ParsedPage[] = pageTexts.map((text, i) => ({
    pageNumber: i + 1,
    text: text.trim(),
    tables: extractMarkdownTables(text),
  }));

  return {
    text: data.text,
    pages,
    pageCount: data.numpages,
    metadata: {
      info: data.info,
      numpages: data.numpages,
    },
  };
}

/**
 * Naively detect table-like content in text and return as markdown.
 * A real implementation would use a dedicated table extraction library.
 */
function extractMarkdownTables(text: string): string[] {
  const lines = text.split("\n").filter((l) => l.trim());
  const tables: string[] = [];
  let tableLines: string[] = [];
  let inTable = false;

  for (const line of lines) {
    // Heuristic: lines with multiple pipe characters or consistent tab/space separation
    const cellCount = (line.match(/\s{2,}|\t/g) || []).length;
    if (cellCount >= 2) {
      inTable = true;
      tableLines.push(line);
    } else {
      if (inTable && tableLines.length >= 2) {
        tables.push(buildMarkdownTable(tableLines));
      }
      inTable = false;
      tableLines = [];
    }
  }

  if (inTable && tableLines.length >= 2) {
    tables.push(buildMarkdownTable(tableLines));
  }

  return tables;
}

function buildMarkdownTable(lines: string[]): string {
  const rows = lines.map((l) => l.split(/\s{2,}|\t/).filter(Boolean));
  const maxCols = Math.max(...rows.map((r) => r.length));
  const padded = rows.map((r) => {
    while (r.length < maxCols) r.push("");
    return `| ${r.join(" | ")} |`;
  });
  const separator = `| ${Array(maxCols).fill("---").join(" | ")} |`;
  return [padded[0], separator, ...padded.slice(1)].join("\n");
}
