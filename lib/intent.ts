export type Decision = "approve" | "reject" | "done";

const APPROVE =
  /^(?:(?:(?:yes|yeah|yep|yup|ok|okay|sure|sounds good|looks good|lgtm) )?(?:approve(?: (?:it|this|that|the fix|the repair))?|go ahead(?: and (?:fix|apply|approve) (?:it|this|that))?|(?:fix|do|apply|ship) it|apply the fix)|yes|yeah|yep|yup|looks good|sounds good|lgtm)$/;

const REJECT =
  /^(?:no|nope|nah|(?:(?:no|nope|nah) )?(?:reject(?: (?:it|this|that|the fix|the repair))?|decline(?: it)?|cancel(?: it)?|(?:dont|do not) (?:fix|apply|approve)(?: (?:it|this|that))?|leave it|hold off|not now))$/;

const DONE =
  /^(?:(?:ok|okay|yes|alright) )?(?:(?:im |i am |all )?(?:done|finished)|i (?:did|fixed|finished|completed) (?:it|this|that)|(?:ive|i have) (?:done|fixed|finished|completed) (?:it|this|that))$/;

// Mirrors parseDecision in backend/app/agent/graph.py. The backend makes the actual decision;
// the app only uses this to word its progress message, and the mock agent to script replies.
export function parseDecision(message: string): Decision | null {
  if (message.includes("?")) return null;

  const phrase = message
    .toLowerCase()
    .replace(/[’']/g, "")
    .replace(/[^a-z\s]/g, " ")
    .replace(/\b(please|thanks|thank you)\b/g, " ")
    .replace(/\s+/g, " ")
    .trim();

  if (REJECT.test(phrase)) return "reject";
  if (APPROVE.test(phrase)) return "approve";
  if (DONE.test(phrase)) return "done";
  return null;
}
