import type { Action } from "@/api/types.ts";
import { ApprovalCard } from "@/features/approvals/approval-card.tsx";
import { CapabilityRequestCard } from "@/features/approvals/capability-request-card.tsx";
import { CredentialRequestCard } from "@/features/approvals/credential-request-card.tsx";

/** The specialized decision surface for one action waiting on the operator. */
export function ActionRequestCard({
  action,
  chatTitle,
}: {
  action: Action;
  chatTitle?: string;
}) {
  if (action.tool === "request_credential") {
    return <CredentialRequestCard action={action} />;
  }
  if (action.tool === "request_capability") {
    return <CapabilityRequestCard action={action} chatTitle={chatTitle} />;
  }
  return <ApprovalCard action={action} chatTitle={chatTitle} />;
}
