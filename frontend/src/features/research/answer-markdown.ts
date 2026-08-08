export type AnswerInlineNode =
  | { kind: "text"; text: string }
  | { kind: "strong"; children: AnswerInlineNode[] }
  | { kind: "emphasis"; children: AnswerInlineNode[] }
  | { kind: "code"; text: string }
  | { kind: "citation"; index: number };

export type AnswerMarkdownBlock =
  | { kind: "heading"; level: number; content: AnswerInlineNode[] }
  | { kind: "paragraph"; content: AnswerInlineNode[] }
  | { kind: "blockquote"; content: AnswerInlineNode[] }
  | { kind: "list"; ordered: boolean; items: AnswerInlineNode[][] }
  | { kind: "table"; headers: AnswerInlineNode[][]; rows: AnswerInlineNode[][][] };

const headingPattern = /^(#{1,6})\s+(.+)$/;
const unorderedListPattern = /^[-*+]\s+(.+)$/;
const orderedListPattern = /^\d+[.)]\s+(.+)$/;
const blockquotePattern = /^>\s?(.*)$/;
const inlinePattern = /(\*\*([^*]+)\*\*|\*([^*]+)\*|`([^`]+)`|\[(\d+)\])/g;

function parseInline(text: string, citationIndexes: ReadonlySet<number>): AnswerInlineNode[] {
  const nodes: AnswerInlineNode[] = [];
  let cursor = 0;

  for (const match of text.matchAll(inlinePattern)) {
    const start = match.index ?? 0;
    if (start > cursor) nodes.push({ kind: "text", text: text.slice(cursor, start) });

    if (match[2] !== undefined) {
      nodes.push({ kind: "strong", children: parseInline(match[2], citationIndexes) });
    } else if (match[3] !== undefined) {
      nodes.push({ kind: "emphasis", children: parseInline(match[3], citationIndexes) });
    } else if (match[4] !== undefined) {
      nodes.push({ kind: "code", text: match[4] });
    } else {
      const index = Number(match[5]);
      if (citationIndexes.has(index)) nodes.push({ kind: "citation", index });
      else nodes.push({ kind: "text", text: match[0] });
    }
    cursor = start + match[0].length;
  }

  if (cursor < text.length) nodes.push({ kind: "text", text: text.slice(cursor) });
  return nodes;
}

function splitTableRow(line: string): string[] {
  const trimmed = line.trim().replace(/^\|/, "").replace(/\|$/, "");
  return trimmed.split("|").map((cell) => cell.trim());
}

function isTableSeparator(line: string): boolean {
  const cells = splitTableRow(line);
  return cells.length > 0 && cells.every((cell) => /^:?-{3,}:?$/.test(cell));
}

function isTableStart(lines: string[], index: number): boolean {
  return Boolean(
    lines[index]?.includes("|") && lines[index + 1] && isTableSeparator(lines[index + 1]),
  );
}

function isBlockStart(lines: string[], index: number): boolean {
  const line = lines[index] ?? "";
  return (
    headingPattern.test(line) ||
    unorderedListPattern.test(line) ||
    orderedListPattern.test(line) ||
    blockquotePattern.test(line) ||
    isTableStart(lines, index)
  );
}

export function parseAnswerMarkdown(
  source: string,
  citationIndexes: readonly number[],
): AnswerMarkdownBlock[] {
  const lines = source.replace(/\r\n?/g, "\n").split("\n");
  const citations = new Set(citationIndexes);
  const blocks: AnswerMarkdownBlock[] = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index] ?? "";
    if (!line.trim()) {
      index += 1;
      continue;
    }

    const heading = line.match(headingPattern);
    if (heading) {
      blocks.push({
        kind: "heading",
        level: heading[1].length,
        content: parseInline(heading[2], citations),
      });
      index += 1;
      continue;
    }

    if (isTableStart(lines, index)) {
      const headers = splitTableRow(line).map((cell) => parseInline(cell, citations));
      const rows: AnswerInlineNode[][][] = [];
      index += 2;
      while (index < lines.length && lines[index]?.includes("|")) {
        rows.push(splitTableRow(lines[index] ?? "").map((cell) => parseInline(cell, citations)));
        index += 1;
      }
      blocks.push({ kind: "table", headers, rows });
      continue;
    }

    const unorderedItem = line.match(unorderedListPattern);
    const orderedItem = line.match(orderedListPattern);
    if (unorderedItem || orderedItem) {
      const ordered = Boolean(orderedItem);
      const pattern = ordered ? orderedListPattern : unorderedListPattern;
      const items: AnswerInlineNode[][] = [];
      while (index < lines.length) {
        const match = (lines[index] ?? "").match(pattern);
        if (!match) break;
        items.push(parseInline(match[1], citations));
        index += 1;
      }
      blocks.push({ kind: "list", ordered, items });
      continue;
    }

    if (blockquotePattern.test(line)) {
      const quoteLines: string[] = [];
      while (index < lines.length) {
        const quote = (lines[index] ?? "").match(blockquotePattern);
        if (!quote) break;
        quoteLines.push(quote[1]);
        index += 1;
      }
      blocks.push({ kind: "blockquote", content: parseInline(quoteLines.join("\n"), citations) });
      continue;
    }

    const paragraphLines: string[] = [];
    while (index < lines.length && lines[index]?.trim() && !isBlockStart(lines, index)) {
      paragraphLines.push(lines[index] ?? "");
      index += 1;
    }
    blocks.push({ kind: "paragraph", content: parseInline(paragraphLines.join("\n"), citations) });
  }

  return blocks;
}
