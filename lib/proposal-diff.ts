import type { JsonValue } from "@/lib/types";

export type DiffLanguage = "javascript" | "python" | "json";

export interface DiffSection {
  label: string;
  language: DiffLanguage;
  before: string;
  after: string;
}

const CODE_FIELDS: Record<string, { label: string; language: DiffLanguage }> = {
  jsCode: { label: "Code · JavaScript", language: "javascript" },
  pythonCode: { label: "Code · Python", language: "python" },
};

type JsonObject = { [key: string]: JsonValue };

function isObject(value: JsonValue | undefined): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function toJson(value: JsonValue | undefined): string {
  return JSON.stringify(value ?? null, null, 2);
}

function withoutKey(object: JsonObject, key: string): JsonObject {
  const copy = { ...object };
  delete copy[key];
  return copy;
}

// The backend sends node views, {name, type, parameters}, so a changed Code node is pulled out
// and diffed as code. Every other difference stays visible in a JSON section: nothing is hidden.
export function buildDiffSections(before: JsonValue, after: JsonValue): DiffSection[] {
  if (typeof before === "string" && typeof after === "string") {
    return [{ label: "Code", language: "javascript", before, after }];
  }

  const sameNode =
    isObject(before) &&
    isObject(after) &&
    isObject(before.parameters) &&
    isObject(after.parameters) &&
    toJson(withoutKey(before, "parameters")) === toJson(withoutKey(after, "parameters"));

  let restBefore: JsonValue = sameNode ? (before as JsonObject).parameters : before;
  let restAfter: JsonValue = sameNode ? (after as JsonObject).parameters : after;
  const sections: DiffSection[] = [];

  if (isObject(restBefore) && isObject(restAfter)) {
    for (const [field, { label, language }] of Object.entries(CODE_FIELDS)) {
      const oldCode = restBefore[field];
      const newCode = restAfter[field];
      if (typeof oldCode === "string" && typeof newCode === "string" && oldCode !== newCode) {
        sections.push({ label, language, before: oldCode, after: newCode });
        restBefore = withoutKey(restBefore, field);
        restAfter = withoutKey(restAfter, field);
      }
    }
  }

  const oldRest = toJson(restBefore);
  const newRest = toJson(restAfter);
  if (sections.length === 0 || oldRest !== newRest) {
    sections.push({
      label: sections.length > 0 ? "Other parameters" : "Parameters",
      language: "json",
      before: oldRest,
      after: newRest,
    });
  }
  return sections;
}
