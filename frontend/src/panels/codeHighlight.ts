/** Python syntax highlighting + error-line extraction. */
import Prism from "prismjs";
import "prismjs/components/prism-python";

/** Highlight one line of Python to an HTML string (token spans). */
export function highlightPython(line: string): string {
  return Prism.highlight(line, Prism.languages.python, "python");
}

/** Highlight each line of code independently (preserves per-line rows). */
export function highlightLines(code: string): string[] {
  return code.replace(/\n$/, "").split("\n").map(highlightPython);
}

/** Pull a 1-based line number out of an error message, if present.
 * Matches "line 5", "(line 5)", and traceback "File ..., line 5". */
export function extractErrorLine(error: string | null | undefined): number | null {
  if (!error) return null;
  const m = error.match(/line (\d+)/i);
  return m ? Number(m[1]) : null;
}
