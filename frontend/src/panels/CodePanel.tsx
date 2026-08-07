/** Generated-code panel: syntax highlight, copy, collapse, error line. */
import { useState } from "react";

import { IconButton, Tooltip, useToast } from "../components";
import { extractErrorLine, highlightLines } from "./codeHighlight";

const COLLAPSE_THRESHOLD = 12; // lines shown when collapsed

export function CodePanel({ code, error }: { code: string; error?: string | null }) {
  const toast = useToast();
  const [collapsed, setCollapsed] = useState(false);
  const lines = highlightLines(code);
  const errorLine = extractErrorLine(error);
  const collapsible = lines.length > COLLAPSE_THRESHOLD;
  const shown = collapsed && collapsible ? lines.slice(0, COLLAPSE_THRESHOLD) : lines;

  async function copy() {
    try {
      await navigator.clipboard.writeText(code);
      toast.success("Code copied");
    } catch {
      toast.error("Copy failed");
    }
  }

  return (
    <div className="codepanel">
      <div className="codepanel-head">
        <span className="codepanel-title">build123d</span>
        <span className="codepanel-actions">
          {collapsible && (
            <button className="vp-chip" onClick={() => setCollapsed((c) => !c)}>
              {collapsed ? "Expand" : "Collapse"}
            </button>
          )}
          <Tooltip label="Copy code">
            <IconButton label="Copy code" onClick={copy}>⧉</IconButton>
          </Tooltip>
        </span>
      </div>
      <pre className="code-rows" aria-label="Generated code">
        {shown.map((html, i) => {
          const lineNo = i + 1;
          const isError = errorLine === lineNo;
          return (
            <div key={i} className={`code-line ${isError ? "code-line-error" : ""}`}>
              <span className="code-gutter">{lineNo}</span>
              <code className="code-content" dangerouslySetInnerHTML={{ __html: html || " " }} />
            </div>
          );
        })}
        {collapsed && collapsible && (
          <div className="code-line code-more">… {lines.length - COLLAPSE_THRESHOLD} more lines</div>
        )}
      </pre>
    </div>
  );
}
