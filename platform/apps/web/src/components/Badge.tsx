const COLOR_MAP: Record<string, string> = {
  proposed: "blue", approved: "green", acknowledged: "green", active: "green", ok: "green",
  complete: "green", optimal: "green", low: "green",
  pending: "amber", testing: "amber", draft: "gray", queued: "amber", medium: "amber", warning: "amber",
  rejected: "red", failed: "red", timed_out: "red", disabled: "red", expired: "red", high: "red",
  critical: "red", policy_block: "red",
  insufficient_evidence: "purple", held: "purple",
  observe: "gray", recommend: "blue", approval_required: "amber", autonomous_bounded: "purple",
};

export default function Badge({ text }: { text: string }) {
  const color = COLOR_MAP[text?.toLowerCase()] || "gray";
  return <span className={`badge badge-${color}`}>{text}</span>;
}
