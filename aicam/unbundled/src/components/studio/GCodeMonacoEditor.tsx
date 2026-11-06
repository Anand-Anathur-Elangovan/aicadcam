"use client";

// Monaco Editor for G-Code using @monaco-editor/react (handles worker setup automatically)
// - Custom gcode-dark theme + syntax highlighting via beforeMount
// - Cursor line click → emits 0-based line index for Canvas3D sync
// - Amber line highlight decoration for 3D→editor sync
// - readOnly prop disables editing during AI generation

import { useRef, useEffect, useCallback } from "react";
import Editor, { type OnMount, type BeforeMount } from "@monaco-editor/react";
import type * as Monaco from "monaco-editor";
import { registerGCodeLanguage, GCODE_LANGUAGE_ID, GCODE_THEME_ID } from "@/lib/gcode/syntax";

interface Props {
  value: string;
  onChange?: (value: string) => void;
  onLineClick?: (lineIndex: number) => void;   // 0-based
  highlightLine?: number;
  readOnly?: boolean;
  className?: string;
}

export default function GCodeMonacoEditor({
  value,
  onChange,
  onLineClick,
  highlightLine,
  readOnly = false,
  className = "",
}: Props) {
  const editorRef      = useRef<Monaco.editor.IStandaloneCodeEditor | null>(null);
  const monacoRef      = useRef<typeof Monaco | null>(null);
  const decorationsRef = useRef<Monaco.editor.IEditorDecorationsCollection | null>(null);
  const suppressRef    = useRef(false);

  // Register language + theme before editor mounts (runs synchronously on the monaco instance)
  const handleBeforeMount: BeforeMount = useCallback((monaco) => {
    registerGCodeLanguage(monaco);
  }, []);

  const handleMount: OnMount = useCallback((editor, monaco) => {
    editorRef.current  = editor;
    monacoRef.current  = monaco;
    decorationsRef.current = editor.createDecorationsCollection([]);

    // Cursor position → line click
    editor.onDidChangeCursorPosition((e) => {
      onLineClick?.(e.position.lineNumber - 1);
    });

    // Content change
    editor.onDidChangeModelContent(() => {
      if (suppressRef.current) return;
      onChange?.(editor.getValue());
    });
  }, [onChange, onLineClick]);

  // Sync value from outside without losing cursor
  useEffect(() => {
    const editor = editorRef.current;
    if (!editor) return;
    if (editor.getValue() === value) return;
    suppressRef.current = true;
    const pos = editor.getPosition();
    editor.setValue(value);
    if (pos) editor.setPosition(pos);
    suppressRef.current = false;
  }, [value]);

  // Sync readOnly
  useEffect(() => {
    editorRef.current?.updateOptions({ readOnly });
  }, [readOnly]);

  // Highlight line from 3D canvas click
  useEffect(() => {
    const editor = editorRef.current;
    const monaco = monacoRef.current;
    const decorations = decorationsRef.current;
    if (!editor || !monaco || !decorations || highlightLine === undefined) return;

    const lineNum = highlightLine + 1;
    decorations.set([
      {
        range: new monaco.Range(lineNum, 1, lineNum, 1),
        options: {
          isWholeLine: true,
          className:   "gcode-highlight-line",
          glyphMarginClassName: "gcode-highlight-glyph",
        },
      },
    ]);
    editor.revealLineInCenterIfOutsideViewport(lineNum);
  }, [highlightLine]);

  return (
    <div className={`relative w-full h-full flex flex-col ${className}`}>
      {/* Toolbar */}
      <div className="flex items-center gap-3 px-3 py-1.5 bg-slate-800 border-b border-slate-700 text-xs text-slate-400 shrink-0">
        <span className="text-slate-300 font-medium">G-Code Editor</span>
        {readOnly && <span className="text-yellow-400 animate-pulse">⟳ Generating…</span>}
        <span className="ml-auto text-slate-500">Click a line to sync 3D view</span>
      </div>

      <div className="flex-1 min-h-0">
        <Editor
          height="100%"
          language={GCODE_LANGUAGE_ID}
          theme={GCODE_THEME_ID}
          value={value}
          beforeMount={handleBeforeMount}
          onMount={handleMount}
          options={{
            readOnly,
            fontSize:              13,
            fontFamily:            "'JetBrains Mono', 'Fira Code', monospace",
            fontLigatures:         true,
            lineNumbers:           "on",
            minimap:               { enabled: true },
            scrollBeyondLastLine:  false,
            wordWrap:              "off",
            renderWhitespace:      "none",
            folding:               true,
            automaticLayout:       true,
            scrollbar: { verticalScrollbarSize: 8, horizontalScrollbarSize: 8 },
          }}
        />
      </div>

      <style>{`
        .gcode-highlight-line {
          background: rgba(251, 191, 36, 0.12) !important;
          border-left: 2px solid #fbbf24 !important;
        }
        .gcode-highlight-glyph {
          background: #fbbf24;
          border-radius: 50%;
          width: 6px !important;
          height: 6px !important;
          margin-top: 7px;
          margin-left: 3px;
        }
      `}</style>
    </div>
  );
}
