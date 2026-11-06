export interface ParsedSheet {
  name: string;
  headers: string[];
  rows: string[][];
  markdown: string;
  json: Record<string, string>[];
}

export interface ParsedExcel {
  sheets: ParsedSheet[];
  text: string;
}

/**
 * Parse XLSX/CSV buffers into structured sheets with markdown table output.
 */
export async function parseExcel(buffer: Buffer, filename: string): Promise<ParsedExcel> {
  const XLSX = await import("xlsx");

  const workbook = XLSX.read(buffer, {
    type: "buffer",
    cellDates: true,
    cellNF: false,
    cellText: false,
  });

  const sheets: ParsedSheet[] = workbook.SheetNames.map((name) => {
    const sheet = workbook.Sheets[name];
    const raw = XLSX.utils.sheet_to_json<string[]>(sheet, { header: 1, defval: "" });

    if (raw.length === 0) {
      return { name, headers: [], rows: [], markdown: "", json: [] };
    }

    const headers = (raw[0] as string[]).map(String);
    const rows = (raw.slice(1) as string[][]).map((r) => headers.map((_, i) => String(r[i] ?? "")));
    const markdown = buildMarkdownTable(headers, rows);
    const json = rows.map((r) => Object.fromEntries(headers.map((h, i) => [h, r[i]])));

    return { name, headers, rows, markdown, json };
  });

  const text = sheets
    .map((s) => `## Sheet: ${s.name}\n\n${s.markdown}`)
    .join("\n\n---\n\n");

  return { sheets, text };
}

function buildMarkdownTable(headers: string[], rows: string[][]): string {
  const headerRow = `| ${headers.join(" | ")} |`;
  const separator = `| ${headers.map(() => "---").join(" | ")} |`;
  const dataRows = rows.map((r) => `| ${r.join(" | ")} |`);
  return [headerRow, separator, ...dataRows].join("\n");
}
