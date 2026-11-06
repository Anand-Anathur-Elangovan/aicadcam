import type { DocumentMetaFields } from "../types";

// Common part-number patterns used in manufacturing.
// NOTE: No 'g' flag — exec() is called once per text, so global state (lastIndex)
// would cause intermittent misses on repeated calls with different inputs.
const PART_NUMBER_PATTERNS = [
  /\b([A-Z]{2,5}-[A-Z0-9]{2,10}(?:-[A-Z0-9]+)*)\b/,   // DWG-A901-REV2
  /\bP\/N[:\s]+([A-Z0-9\-/]+)\b/i,                      // P/N: 12345-A
  /\bPart\s*(?:No|Number|#)[:\s]+([A-Z0-9\-/]+)\b/i,   // Part No: ABC-001
  /\bPN[:\s]+([A-Z0-9\-/]+)\b/i,                        // PN: XYZ-99
];

const REVISION_PATTERNS = [
  /\bRev(?:ision)?[.:\s]+([A-Z0-9]{1,4})\b/i,           // Rev. A, Revision 3
  /\bREV[:\s]?([A-Z0-9]{1,4})\b/,                        // REV3, REV-B
  /[-_]R([0-9]{1,3})\b/,                                 // DWG-001-R02
];

/**
 * Auto-extract structured fields from parsed document text using regex.
 * Returns fields that weren't already supplied manually.
 */
export function autoExtractFromText(
  text: string,
  existing: DocumentMetaFields
): Record<string, string> {
  const extracted: Record<string, string> = {};

  // Extract part numbers if not manually entered
  if (!existing.partNumber) {
    for (const pattern of PART_NUMBER_PATTERNS) {
      const match = pattern.exec(text);
      if (match) {
        extracted.partNumber = match[1].trim();
        break;
      }
    }
  }

  // Extract revision if not manually entered
  if (!existing.revision) {
    for (const pattern of REVISION_PATTERNS) {
      const match = pattern.exec(text);
      if (match) {
        extracted.revision = match[1].trim().toUpperCase();
        break;
      }
    }
  }

  return extracted;
}

/**
 * Build the metadata header chunk text.
 * This is embedded as chunk_index=0 so metadata fields are fully searchable.
 *
 * Why: Part numbers and revision codes often don't appear in normal body text chunks.
 * Embedding them as a dedicated chunk ensures semantic AND BM25 search can find them.
 */
export function buildMetadataHeaderChunk(
  fields: DocumentMetaFields,
  autoExtracted: Record<string, string>,
  category: string,
  originalName: string
): string {
  const merged = {
    title: fields.docTitle ?? autoExtracted.title ?? "",
    description: fields.description ?? autoExtracted.description ?? "",
    partName: fields.partName ?? autoExtracted.partName ?? "",
    partNumber: fields.partNumber ?? autoExtracted.partNumber ?? "",
    revision: fields.revision ?? autoExtracted.revision ?? "",
    developedDate: fields.developedDate ?? "",
    customerName: fields.customerName ?? autoExtracted.customerName ?? "",
    issueDetails: fields.issueDetails ?? autoExtracted.issueDetails ?? "",
  };

  const lines = [
    `DOCUMENT: ${originalName}`,
    `CATEGORY: ${category}`,
  ];

  if (merged.title)        lines.push(`TITLE: ${merged.title}`);
  if (merged.partName)     lines.push(`PART NAME: ${merged.partName}`);
  if (merged.partNumber)   lines.push(`PART NUMBER: ${merged.partNumber}`);
  if (merged.revision)     lines.push(`REVISION: ${merged.revision}`);
  if (merged.developedDate) lines.push(`DEVELOPED DATE: ${merged.developedDate}`);
  if (merged.customerName) lines.push(`CUSTOMER: ${merged.customerName}`);
  if (merged.description)  lines.push(`DESCRIPTION: ${merged.description}`);
  if (merged.issueDetails) lines.push(`ISSUE DETAILS: ${merged.issueDetails}`);

  return lines.join("\n");
}
