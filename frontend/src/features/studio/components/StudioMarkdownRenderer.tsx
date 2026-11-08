import React from 'react';

/**
 * Strips code fence delimiters (``` or ```lang) from view.
 */
export function stripCodeFences(text: string): string {
  if (!text) return '';
  return text.replace(/^\s*```[\w-]*\s*$/gm, '');
}

/**
 * Parses inline markdown:
 * - `code` -> <code>
 * - **bold** or __bold__ -> <strong>
 * - plain text -> string (React safely escapes all angle brackets like <script>)
 */
export function parseInline(text: string): React.ReactNode[] {
  if (!text) return [];

  // Match either inline code or bold (**...** or __...__)
  const regex = /(`[^`]+`|\*\*[^*]+\*\*|__[^_]+__)/g;
  const elements: React.ReactNode[] = [];
  let lastIdx = 0;
  let match: RegExpExecArray | null;
  let key = 0;

  while ((match = regex.exec(text)) !== null) {
    if (match.index > lastIdx) {
      elements.push(text.slice(lastIdx, match.index));
    }
    const token = match[0];
    if (token.startsWith('`') && token.endsWith('`')) {
      elements.push(
        <code
          key={`code-${key++}`}
          className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[11px] text-slate-800 break-all border border-slate-200/60"
        >
          {token.slice(1, -1)}
        </code>
      );
    } else if (
      (token.startsWith('**') && token.endsWith('**')) ||
      (token.startsWith('__') && token.endsWith('__'))
    ) {
      elements.push(
        <strong key={`bold-${key++}`} className="font-semibold text-slate-900">
          {token.slice(2, -2)}
        </strong>
      );
    }
    lastIdx = regex.lastIndex;
  }

  if (lastIdx < text.length) {
    elements.push(text.slice(lastIdx));
  }

  return elements.length === 0 ? [text] : elements;
}

interface TableData {
  headers: string[];
  rows: string[][];
}

function parseTable(lines: string[]): TableData | null {
  if (lines.length < 2) return null;
  // Second line must be separator: | --- | --- | or --- | ---
  const sep = lines[1].trim();
  const isSeparator = /^\|?(\s*:?-+:?\s*\|)+\s*:?-+:?\s*\|?$/.test(sep);
  if (!isSeparator) return null;

  const splitCells = (line: string): string[] => {
    let clean = line.trim();
    if (clean.startsWith('|')) clean = clean.slice(1);
    if (clean.endsWith('|')) clean = clean.slice(0, -1);
    return clean.split('|').map((c) => c.trim());
  };

  const headers = splitCells(lines[0]);
  const rows = lines.slice(2).map(splitCells);
  return { headers, rows };
}

interface StudioMarkdownRendererProps {
  content: string;
}

export const StudioMarkdownRenderer: React.FC<StudioMarkdownRendererProps> = ({ content }) => {
  if (!content) return null;

  // 1. Strip code fences
  const cleaned = stripCodeFences(content);
  const rawLines = cleaned.split(/\r?\n/);

  const blocks: React.ReactNode[] = [];
  let blockKey = 0;
  let i = 0;

  while (i < rawLines.length) {
    const line = rawLines[i];
    const trimmed = line.trim();

    if (!trimmed) {
      i++;
      continue;
    }

    // Check for Headings
    if (/^####\s+/.test(trimmed)) {
      blocks.push(
        <h4
          key={`h4-${blockKey++}`}
          className="text-xs font-semibold text-slate-900 mt-2 mb-1"
        >
          {parseInline(trimmed.replace(/^####\s+/, ''))}
        </h4>
      );
      i++;
      continue;
    }
    if (/^###\s+/.test(trimmed)) {
      blocks.push(
        <h3
          key={`h3-${blockKey++}`}
          className="text-sm font-semibold text-slate-900 mt-2.5 mb-1"
        >
          {parseInline(trimmed.replace(/^###\s+/, ''))}
        </h3>
      );
      i++;
      continue;
    }
    if (/^##\s+/.test(trimmed)) {
      blocks.push(
        <h2
          key={`h2-${blockKey++}`}
          className="text-sm font-bold text-slate-900 mt-3 mb-1"
        >
          {parseInline(trimmed.replace(/^##\s+/, ''))}
        </h2>
      );
      i++;
      continue;
    }
    if (/^#\s+/.test(trimmed)) {
      blocks.push(
        <h1
          key={`h1-${blockKey++}`}
          className="text-base font-bold text-slate-900 mt-3.5 mb-1"
        >
          {parseInline(trimmed.replace(/^#\s+/, ''))}
        </h1>
      );
      i++;
      continue;
    }

    // Check for Table (current line has pipe and next line is separator)
    if (
      trimmed.includes('|') &&
      i + 1 < rawLines.length &&
      /^\|?(\s*:?-+:?\s*\|)+\s*:?-+:?\s*\|?$/.test(rawLines[i + 1].trim())
    ) {
      const tableLines: string[] = [];
      while (i < rawLines.length && rawLines[i].trim().includes('|')) {
        tableLines.push(rawLines[i]);
        i++;
      }
      const tableData = parseTable(tableLines);
      if (tableData) {
        blocks.push(
          <div
            key={`tbl-wrap-${blockKey++}`}
            className="overflow-x-auto max-w-full my-2.5 rounded-lg border border-slate-200 shadow-xs"
          >
            <table className="min-w-full divide-y divide-slate-200 text-left text-[11px]">
              <thead className="bg-slate-50 font-semibold text-slate-700">
                <tr>
                  {tableData.headers.map((h, hIdx) => (
                    <th key={hIdx} className="px-3 py-2 whitespace-nowrap">
                      {parseInline(h)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 bg-white">
                {tableData.rows.map((row, rIdx) => (
                  <tr key={rIdx} className="hover:bg-slate-50/60 transition-colors">
                    {row.map((cell, cIdx) => (
                      <td key={cIdx} className="px-3 py-1.5 text-slate-700">
                        {parseInline(cell)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        );
        continue;
      }
    }

    // Check for Unordered List (- or *)
    if (/^[-*+]\s+/.test(trimmed)) {
      const items: string[] = [];
      while (i < rawLines.length && /^[-*+]\s+/.test(rawLines[i].trim())) {
        items.push(rawLines[i].trim().replace(/^[-*+]\s+/, ''));
        i++;
      }
      blocks.push(
        <ul
          key={`ul-${blockKey++}`}
          className="list-disc list-inside space-y-1 my-1.5 text-xs text-slate-700 pl-1"
        >
          {items.map((it, itIdx) => (
            <li key={itIdx} className="leading-relaxed">
              {parseInline(it)}
            </li>
          ))}
        </ul>
      );
      continue;
    }

    // Check for Ordered List (1. 2. etc)
    if (/^\d+\.\s+/.test(trimmed)) {
      const items: string[] = [];
      while (i < rawLines.length && /^\d+\.\s+/.test(rawLines[i].trim())) {
        items.push(rawLines[i].trim().replace(/^\d+\.\s+/, ''));
        i++;
      }
      blocks.push(
        <ol
          key={`ol-${blockKey++}`}
          className="list-decimal list-inside space-y-1 my-1.5 text-xs text-slate-700 pl-1"
        >
          {items.map((it, itIdx) => (
            <li key={itIdx} className="leading-relaxed">
              {parseInline(it)}
            </li>
          ))}
        </ol>
      );
      continue;
    }

    // Normal Paragraph
    blocks.push(
      <p
        key={`p-${blockKey++}`}
        className="my-1.5 text-xs leading-relaxed text-slate-700 break-words [overflow-wrap:anywhere]"
      >
        {parseInline(trimmed)}
      </p>
    );
    i++;
  }

  return (
    <div className="w-full max-w-full overflow-hidden text-xs text-slate-800 space-y-1">
      {blocks}
    </div>
  );
};

