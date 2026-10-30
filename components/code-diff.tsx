import type { ReactNode } from "react";

import type { DiffLanguage, DiffSection } from "@/lib/proposal-diff";
import { cn } from "@/lib/utils";

interface DiffLine {
  kind: "same" | "del" | "add";
  text: string;
  oldNo?: number;
  newNo?: number;
}

function diffLines(before: string[], after: string[]): DiffLine[] {
  // Longest-common-subsequence table, filled from the end so the walk below runs forward.
  const lcs = Array.from({ length: before.length + 1 }, () =>
    new Array<number>(after.length + 1).fill(0),
  );
  for (let i = before.length - 1; i >= 0; i--) {
    for (let j = after.length - 1; j >= 0; j--) {
      lcs[i][j] =
        before[i] === after[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
    }
  }

  const lines: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < before.length || j < after.length) {
    if (i < before.length && j < after.length && before[i] === after[j]) {
      lines.push({ kind: "same", text: before[i], oldNo: i + 1, newNo: j + 1 });
      i++;
      j++;
    } else if (j >= after.length || (i < before.length && lcs[i + 1][j] >= lcs[i][j + 1])) {
      lines.push({ kind: "del", text: before[i], oldNo: i + 1 });
      i++;
    } else {
      lines.push({ kind: "add", text: after[j], newNo: j + 1 });
      j++;
    }
  }
  return lines;
}

// Pairs each run of removals with the additions after it so both columns stay aligned.
function toRows(lines: DiffLine[]): { left?: DiffLine; right?: DiffLine }[] {
  const rows: { left?: DiffLine; right?: DiffLine }[] = [];
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    if (line.kind === "same") {
      rows.push({ left: line, right: line });
      index++;
      continue;
    }
    const removed: DiffLine[] = [];
    const added: DiffLine[] = [];
    while (lines[index]?.kind === "del") removed.push(lines[index++]);
    while (lines[index]?.kind === "add") added.push(lines[index++]);
    for (let n = 0; n < Math.max(removed.length, added.length); n++) {
      rows.push({ left: removed[n], right: added[n] });
    }
  }
  return rows;
}

const COLORS = {
  comment: "text-muted-foreground italic",
  string: "text-[#c8d6a4]",
  key: "text-[#9db8e3]",
  keyword: "text-[#c9a7e8]",
  literal: "text-[#e0a98a]",
};

const JS_KEYWORDS =
  "const|let|var|function|return|if|else|for|while|of|in|new|throw|try|catch|finally|await|async|typeof|class|switch|case|break|continue";
const PY_KEYWORDS =
  "def|return|if|elif|else|for|while|in|not|and|or|import|from|as|raise|try|except|finally|with|class|lambda|pass|break|continue|is";

const TOKENS: Record<DiffLanguage, RegExp> = {
  json: new RegExp(
    `(?<string>"(?:\\\\.|[^"\\\\])*")(?<colon>\\s*:)?|(?<literal>\\b(?:true|false|null)\\b|-?\\b\\d+(?:\\.\\d+)?(?:[eE][+-]?\\d+)?\\b)`,
    "g",
  ),
  javascript: new RegExp(
    `(?<comment>//.*$)|(?<string>"(?:\\\\.|[^"\\\\])*"|'(?:\\\\.|[^'\\\\])*'|\`(?:\\\\.|[^\`\\\\])*\`)|(?<keyword>\\b(?:${JS_KEYWORDS})\\b)|(?<literal>\\b(?:true|false|null|undefined)\\b|\\b\\d+(?:\\.\\d+)?\\b)`,
    "g",
  ),
  python: new RegExp(
    `(?<comment>#.*$)|(?<string>"(?:\\\\.|[^"\\\\])*"|'(?:\\\\.|[^'\\\\])*')|(?<keyword>\\b(?:${PY_KEYWORDS})\\b)|(?<literal>\\b(?:True|False|None)\\b|\\b\\d+(?:\\.\\d+)?\\b)`,
    "g",
  ),
};

function highlight(text: string, language: DiffLanguage): ReactNode[] {
  const parts: ReactNode[] = [];
  let cursor = 0;
  for (const match of text.matchAll(TOKENS[language])) {
    const { comment, string, colon, keyword, literal } = match.groups ?? {};
    parts.push(text.slice(cursor, match.index));
    const token = comment ?? string ?? keyword ?? literal ?? "";
    const color = comment
      ? COLORS.comment
      : string
        ? colon
          ? COLORS.key
          : COLORS.string
        : keyword
          ? COLORS.keyword
          : COLORS.literal;
    parts.push(
      <span key={match.index} className={color}>
        {token}
      </span>,
    );
    if (colon) parts.push(colon);
    cursor = match.index + match[0].length;
  }
  parts.push(text.slice(cursor));
  return parts;
}

const LINE_STYLES = {
  same: "",
  del: "bg-fail/10",
  add: "bg-ok/10",
};

const MARKS = { same: " ", del: "−", add: "+" };
const MARK_STYLES = { same: "text-transparent", del: "text-fail", add: "text-ok" };
const KIND_LABELS = { same: "", del: "Removed: ", add: "Added: " };

function Line({
  line,
  number,
  language,
}: {
  line?: DiffLine;
  number?: number;
  language: DiffLanguage;
}) {
  if (!line) {
    return <div className="min-h-5 bg-secondary/30" />;
  }
  return (
    <div className={cn("grid grid-cols-[2.25rem_1rem_1fr] leading-5", LINE_STYLES[line.kind])}>
      <span aria-hidden className="pr-2 text-right text-muted-foreground/60 select-none">
        {number}
      </span>
      <span aria-hidden className={cn("select-none", MARK_STYLES[line.kind])}>
        {MARKS[line.kind]}
      </span>
      <span className="min-w-0 pr-3 break-words whitespace-pre-wrap">
        <span className="sr-only">{KIND_LABELS[line.kind]}</span>
        {highlight(line.text, language)}
      </span>
    </div>
  );
}

export function CodeDiff({ section }: { section: DiffSection }) {
  const { label, language } = section;
  const lines = diffLines(section.before.split("\n"), section.after.split("\n"));
  const rows = toRows(lines);

  return (
    <div className="@container overflow-hidden rounded-lg border bg-background font-mono text-xs">
      <div className="chart-label border-b px-3 py-1.5">{label}</div>
      {/* Side by side when the card is wide enough, otherwise one unified column. */}
      <div className="hidden @2xl:block">
        <div className="grid grid-cols-2 divide-x border-b">
          <div className="chart-label px-3 py-1.5">Before</div>
          <div className="chart-label px-3 py-1.5">After</div>
        </div>
        <div className="py-1.5">
          {rows.map((row, index) => (
            <div key={index} className="grid grid-cols-2 divide-x">
              <Line line={row.left} number={row.left?.oldNo} language={language} />
              <Line line={row.right} number={row.right?.newNo} language={language} />
            </div>
          ))}
        </div>
      </div>
      <div className="@2xl:hidden">
        <div className="chart-label border-b px-3 py-1.5">Before → After</div>
        <div className="py-1.5">
          {lines.map((line, index) => (
            <Line key={index} line={line} number={line.newNo ?? line.oldNo} language={language} />
          ))}
        </div>
      </div>
    </div>
  );
}
