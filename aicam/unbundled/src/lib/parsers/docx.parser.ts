export interface ParsedDocx {
  text: string;
  html: string;
  messages: string[];
}

/**
 * Parse DOCX buffer to plain text and HTML using mammoth.
 */
export async function parseDocx(buffer: Buffer): Promise<ParsedDocx> {
  const mammoth = await import("mammoth");

  const [textResult, htmlResult] = await Promise.all([
    mammoth.extractRawText({ buffer }),
    mammoth.convertToHtml({ buffer }),
  ]);

  return {
    text: textResult.value,
    html: htmlResult.value,
    messages: [...textResult.messages, ...htmlResult.messages].map((m) => m.message),
  };
}
