export { parsePDF } from "./pdf.parser";
export { parseExcel } from "./excel.parser";
export { parseDocx } from "./docx.parser";
export { parseImage } from "./image.parser";
export { chunkText, chunkTable } from "./chunker";
export type { ParsedDocument, ParsedPage } from "./pdf.parser";
export type { ParsedExcel, ParsedSheet } from "./excel.parser";
export type { ParsedDocx } from "./docx.parser";
export type { ParsedImage } from "./image.parser";
export type { TextChunk } from "./chunker";

export function getParserForMimeType(mimeType: string): "pdf" | "excel" | "docx" | "image" | "text" | "unknown" {
  if (mimeType === "application/pdf") return "pdf";
  if (["application/vnd.ms-excel", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "text/csv"].includes(mimeType)) return "excel";
  if (mimeType === "application/vnd.openxmlformats-officedocument.wordprocessingml.document") return "docx";
  if (mimeType.startsWith("image/")) return "image";
  if (mimeType.startsWith("text/")) return "text";
  return "unknown";
}
