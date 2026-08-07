/** A dependency-free, XSS-safe markdown renderer for chat `text` blocks
 * (enriched in).
 *
 * It renders the block + inline subset the assistant actually emits — headings,
 * fenced code, ordered/unordered/nested lists, blockquotes, GFM pipe tables,
 * horizontal rules, links, and bold/italic/inline-code — as React nodes. It
 * NEVER injects raw HTML (no `dangerouslySetInnerHTML`): source markup that
 * isn't recognised is rendered literally, and link hrefs are scheme-guarded, so
 * a string like `<img onerror=...>` or `[x](javascript:...)` can't execute.
 *
 * Streaming-safe: an unterminated ``` fence renders the partial block as code,
 * and unmatched inline tokens render as plain text, so half-streamed markdown
 * never throws or mangles. */
import { type ReactNode } from "react";

// Inline tokens, longest-match first: link, bold-italic, bold, italic, code.
const INLINE =
  /(\[[^\]]+\]\([^)]+\)|\*\*\*[^*]+\*\*\*|\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`)/g;
const LINK = /^\[([^\]]+)\]\(([^)]+)\)$/;

/** Allow only safe link schemes; everything else renders as inert text. */
function safeHref(url: string): string | null {
  const u = url.trim();
  if (/^(https?:|mailto:|\/|#|\.)/i.test(u)) return u;
  return null;
}

/** Split a span of text into React nodes, honoring inline markdown tokens. */
function inline(text: string, keyBase: string): ReactNode[] {
  const out: ReactNode[] = [];
  text.split(INLINE).forEach((part, i) => {
    if (!part) return;
    const key = `${keyBase}-${i}`;
    const link = LINK.exec(part);
    if (link) {
      const href = safeHref(link[2]);
      out.push(
        href ? (
          <a key={key} href={href} target="_blank" rel="noopener noreferrer">
            {link[1]}
          </a>
        ) : (
          part
        ),
      );
    } else if (part.startsWith("***") && part.endsWith("***")) {
      out.push(
        <strong key={key}>
          <em>{part.slice(3, -3)}</em>
        </strong>,
      );
    } else if (part.startsWith("**") && part.endsWith("**")) {
      out.push(<strong key={key}>{part.slice(2, -2)}</strong>);
    } else if (part.startsWith("*") && part.endsWith("*")) {
      out.push(<em key={key}>{part.slice(1, -1)}</em>);
    } else if (part.startsWith("`") && part.endsWith("`")) {
      out.push(<code key={key}>{part.slice(1, -1)}</code>);
    } else {
      out.push(part);
    }
  });
  return out;
}

/** Render an `h1`–`h6` element for the given level (explicit, to keep types tight). */
function heading(level: number, children: ReactNode[], key: string): ReactNode {
  switch (level) {
    case 1:
      return <h1 key={key}>{children}</h1>;
    case 2:
      return <h2 key={key}>{children}</h2>;
    case 3:
      return <h3 key={key}>{children}</h3>;
    case 4:
      return <h4 key={key}>{children}</h4>;
    case 5:
      return <h5 key={key}>{children}</h5>;
    default:
      return <h6 key={key}>{children}</h6>;
  }
}

const HEADING = /^(#{1,6})\s+(.*)$/;
const FENCE = /^\s*```/;
const RULE = /^\s*(?:---|\*\*\*|___)\s*$/;
const QUOTE = /^\s*>\s?(.*)$/;
const UL = /^(\s*)[-*+]\s+(.*)$/;
const OL = /^(\s*)\d+\.\s+(.*)$/;

interface ListItem {
  indent: number;
  ordered: boolean;
  content: string;
}

/** Build a (possibly nested) list from contiguous list lines via an indent stack. */
function renderList(items: ListItem[], key: string): ReactNode {
  let pos = 0;
  function build(minIndent: number): ReactNode {
    const ordered = items[pos].ordered;
    const lis: ReactNode[] = [];
    while (pos < items.length && items[pos].indent >= minIndent) {
      const item = items[pos];
      const liKey = `${key}-${pos}`;
      pos += 1;
      const children: ReactNode[] = inline(item.content, liKey);
      // A more-indented run that follows becomes this item's nested sub-list.
      if (pos < items.length && items[pos].indent > item.indent) {
        children.push(build(items[pos].indent));
      }
      lis.push(<li key={liKey}>{children}</li>);
    }
    return ordered ? (
      <ol key={`${key}-ol${pos}`}>{lis}</ol>
    ) : (
      <ul key={`${key}-ul${pos}`}>{lis}</ul>
    );
  }
  return build(items[0].indent);
}

/** Split a GFM table row `| a | b |` into trimmed cell strings. */
function tableCells(row: string): string[] {
  return row
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((c) => c.trim());
}

const isTableSeparator = (line: string | undefined): boolean =>
  !!line && /\|/.test(line) && /^\s*\|?[\s:|-]+\|?\s*$/.test(line) && line.includes("-");

/** Render the markdown subset as React nodes (no raw HTML, no deps). */
export function Markdown({ text }: { text: string }) {
  const lines = text.split("\n");
  const blocks: ReactNode[] = [];
  let i = 0;
  let para: string[] = [];

  const flushPara = () => {
    if (para.length) {
      const key = `p${blocks.length}`;
      blocks.push(<p key={key}>{inline(para.join(" "), key)}</p>);
      para = [];
    }
  };

  while (i < lines.length) {
    const line = lines[i];

    // Fenced code block (streaming-safe: an unclosed fence still renders).
    if (FENCE.test(line)) {
      flushPara();
      const code: string[] = [];
      i += 1;
      while (i < lines.length && !FENCE.test(lines[i])) {
        code.push(lines[i]);
        i += 1;
      }
      i += 1; // consume the closing fence (or step past the end if unclosed)
      blocks.push(
        <pre key={`pre${blocks.length}`} className="md-code">
          <code>{code.join("\n")}</code>
        </pre>,
      );
      continue;
    }

    // Heading.
    const h = HEADING.exec(line);
    if (h) {
      flushPara();
      const key = `h${blocks.length}`;
      blocks.push(heading(h[1].length, inline(h[2], key), key));
      i += 1;
      continue;
    }

    // Horizontal rule.
    if (RULE.test(line)) {
      flushPara();
      blocks.push(<hr key={`hr${blocks.length}`} />);
      i += 1;
      continue;
    }

    // GFM pipe table: header row followed by a `|---|` separator row.
    if (line.includes("|") && isTableSeparator(lines[i + 1])) {
      flushPara();
      const headers = tableCells(line);
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && lines[i].includes("|") && lines[i].trim() !== "") {
        rows.push(tableCells(lines[i]));
        i += 1;
      }
      const tk = `tbl${blocks.length}`;
      blocks.push(
        <table key={tk} className="md-table">
          <thead>
            <tr>
              {headers.map((c, ci) => (
                <th key={`${tk}-h${ci}`}>{inline(c, `${tk}-h${ci}`)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, ri) => (
              <tr key={`${tk}-r${ri}`}>
                {r.map((c, ci) => (
                  <td key={`${tk}-r${ri}c${ci}`}>{inline(c, `${tk}-r${ri}c${ci}`)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>,
      );
      continue;
    }

    // Blockquote (consume consecutive `>` lines).
    const q = QUOTE.exec(line);
    if (q) {
      flushPara();
      const quoted: string[] = [];
      while (i < lines.length) {
        const m = QUOTE.exec(lines[i]);
        if (!m) break;
        quoted.push(m[1]);
        i += 1;
      }
      const key = `bq${blocks.length}`;
      blocks.push(
        <blockquote key={key}>{inline(quoted.join(" "), key)}</blockquote>,
      );
      continue;
    }

    // List (ordered/unordered, with indentation-based nesting).
    if (UL.test(line) || OL.test(line)) {
      flushPara();
      const items: ListItem[] = [];
      while (i < lines.length) {
        const ul = UL.exec(lines[i]);
        const ol = OL.exec(lines[i]);
        if (ul) items.push({ indent: ul[1].length, ordered: false, content: ul[2] });
        else if (ol) items.push({ indent: ol[1].length, ordered: true, content: ol[2] });
        else break;
        i += 1;
      }
      blocks.push(renderList(items, `list${blocks.length}`));
      continue;
    }

    // Blank line ends a paragraph.
    if (line.trim() === "") {
      flushPara();
      i += 1;
      continue;
    }

    // Default: accumulate into the current paragraph.
    para.push(line.trimEnd());
    i += 1;
  }
  flushPara();

  return <div className="markdown">{blocks}</div>;
}
