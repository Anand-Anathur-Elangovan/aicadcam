// Monaco Editor custom language definition for G-Code / M-Code
// Registers a "gcode" language with full token highlighting

import type * as Monaco from "monaco-editor";

export const GCODE_LANGUAGE_ID = "gcode";

export const GCODE_THEME_ID = "gcode-dark";

export function registerGCodeLanguage(monaco: typeof Monaco) {
  // Register language if not already registered
  const langs = monaco.languages.getLanguages();
  if (langs.some((l) => l.id === GCODE_LANGUAGE_ID)) return;

  monaco.languages.register({ id: GCODE_LANGUAGE_ID });

  monaco.languages.setMonarchTokensProvider(GCODE_LANGUAGE_ID, {
    tokenizer: {
      root: [
        // Comments: (parenthetical) and ; style
        [/\(.*?\)/, "comment"],
        [/;.*$/, "comment"],

        // Program number O****
        [/\bO\d+\b/, "gcode.program-number"],

        // Safety / mode G-codes (G17,G20,G21,G40,G49,G80,G90,G91,G28,G92,G54-G59)
        [/\bG(17|20|21|40|49|80|90|91|28|92|5[4-9])\b/i, "gcode.setup"],

        // Motion G-codes (G0, G00, G1, G01, G2, G02, G3, G03)
        [/\bG0?[0-3]\b/i, "gcode.motion"],

        // Canned cycles
        [/\bG8[0-9]\b/i, "gcode.canned"],

        // Tool compensation
        [/\bG4[0-4]\b/i, "gcode.compensation"],

        // M-codes
        [/\bM0?[0-9]\b/i, "gcode.mcode"],
        [/\bM[0-9]{2}\b/i, "gcode.mcode"],

        // Word addresses: X Y Z axes
        [/\b[XYZ]([-+]?\d*\.?\d+)/, "gcode.axis"],

        // Word addresses: IJK arc offsets
        [/\b[IJK]([-+]?\d*\.?\d+)/, "gcode.arc"],

        // Feed rate F and Radius R
        [/\b[FR]([-+]?\d*\.?\d+)/, "gcode.feed"],

        // Spindle speed S
        [/\bS\d+/, "gcode.spindle"],

        // Tool number T
        [/\bT\d+/, "gcode.tool"],

        // Height offset H
        [/\bH\d+/, "gcode.offset"],

        // Numbers
        [/[-+]?\d*\.?\d+/, "number"],

        // Operators
        [/[%]/, "gcode.percent"],
      ],
    },
  });

  // Dark theme for G-code editor
  monaco.editor.defineTheme(GCODE_THEME_ID, {
    base: "vs-dark",
    inherit: true,
    rules: [
      { token: "gcode.program-number", foreground: "FF9900", fontStyle: "bold" },
      { token: "gcode.motion",         foreground: "00E5FF", fontStyle: "bold" },
      { token: "gcode.setup",          foreground: "A78BFA" },
      { token: "gcode.canned",         foreground: "F59E0B" },
      { token: "gcode.compensation",   foreground: "8B5CF6" },
      { token: "gcode.mcode",          foreground: "F87171", fontStyle: "bold" },
      { token: "gcode.axis",           foreground: "34D399" },
      { token: "gcode.arc",            foreground: "60A5FA" },
      { token: "gcode.feed",           foreground: "FBBF24" },
      { token: "gcode.spindle",        foreground: "FB923C" },
      { token: "gcode.tool",           foreground: "E879F9" },
      { token: "gcode.offset",         foreground: "94A3B8" },
      { token: "gcode.percent",        foreground: "6EE7B7" },
      { token: "comment",              foreground: "6B7280", fontStyle: "italic" },
      { token: "number",               foreground: "93C5FD" },
    ],
    colors: {
      "editor.background":            "#0F172A",
      "editor.foreground":            "#E2E8F0",
      "editorLineNumber.foreground":  "#475569",
      "editorLineNumber.activeForeground": "#94A3B8",
      "editor.lineHighlightBackground": "#1E293B",
      "editorCursor.foreground":      "#00E5FF",
      "editor.selectionBackground":   "#1E40AF66",
      "editorIndentGuide.background": "#1E293B",
    },
  });

  // Auto-completion for common G/M codes
  monaco.languages.registerCompletionItemProvider(GCODE_LANGUAGE_ID, {
    provideCompletionItems: (_model, _position) => {
      const suggestions: Monaco.languages.CompletionItem[] = GCODE_COMPLETIONS.map((c) => ({
        label: c.label,
        kind: monaco.languages.CompletionItemKind.Keyword,
        insertText: c.insert,
        detail: c.detail,
        documentation: c.doc,
        range: {
          startLineNumber: _position.lineNumber,
          endLineNumber: _position.lineNumber,
          startColumn: _position.column,
          endColumn: _position.column,
        },
      }));
      return { suggestions };
    },
  });
}

const GCODE_COMPLETIONS = [
  { label: "G00", insert: "G00 X${1:0} Y${2:0} Z${3:0}", detail: "Rapid Positioning", doc: "Move to position at rapid speed" },
  { label: "G01", insert: "G01 X${1:0} Y${2:0} Z${3:0} F${4:200}", detail: "Linear Interpolation", doc: "Linear feed motion" },
  { label: "G02", insert: "G02 X${1:0} Y${2:0} I${3:0} J${4:0} F${5:200}", detail: "Circular CW", doc: "Clockwise circular interpolation" },
  { label: "G03", insert: "G03 X${1:0} Y${2:0} I${3:0} J${4:0} F${5:200}", detail: "Circular CCW", doc: "Counter-clockwise circular interpolation" },
  { label: "G17", insert: "G17", detail: "XY Plane Select", doc: "Select XY plane for circular interpolation" },
  { label: "G21", insert: "G21", detail: "Metric Mode", doc: "Set units to millimeters" },
  { label: "G28", insert: "G28 Z0", detail: "Return to Reference", doc: "Return axis to machine reference point" },
  { label: "G43", insert: "G43 H${1:01} Z${2:50}", detail: "Tool Length Compensation +", doc: "Apply positive tool length offset" },
  { label: "G54", insert: "G54", detail: "Work Coord System 1", doc: "Select work coordinate system 1" },
  { label: "G80", insert: "G80", detail: "Cancel Canned Cycle", doc: "Cancel active canned cycle" },
  { label: "G81", insert: "G81 Z${1:-15} R${2:2} F${3:120}", detail: "Drilling Cycle", doc: "Simple drilling canned cycle" },
  { label: "G90", insert: "G90", detail: "Absolute Mode", doc: "Absolute coordinate mode" },
  { label: "M03", insert: "S${1:2500} M03", detail: "Spindle CW", doc: "Start spindle clockwise" },
  { label: "M06", insert: "T${1:01} M06", detail: "Tool Change", doc: "Execute tool change" },
  { label: "M08", insert: "M08", detail: "Coolant On", doc: "Turn on flood coolant" },
  { label: "M30", insert: "M30", detail: "Program End + Rewind", doc: "End program and rewind" },
];
