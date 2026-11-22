import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { ApiError, explain } from "@/api/errors.ts";
import type { Action, Breadth, Decision, Scope } from "@/api/types.ts";
import { toast } from "@/components/ui/toast.tsx";
import { decide } from "@/features/approvals/api.ts";
import { m } from "@/paraglide/messages.js";

/** Answers after which this action can no longer be decided from here. */
export const isGone = (error: unknown): boolean =>
  error instanceof ApiError &&
  (error.status === 404 ||
    (error.status === 409 &&
      (error.message.includes("already decided") ||
        error.message.includes("deleted"))));

export interface DecisionRequest {
  decision: Decision;
  scope?: Scope;
  breadth?: Breadth;
  summary?: string;
}

/**
 * One decision per approval card. Scoped approvals create the standing rule on the
 * server in the same request as the decision.
 */
export function useApprovalDecision(action: Action) {
  const queryClient = useQueryClient();
  const [lastError, setLastError] = useState<unknown>(null);
  const inFlight = useRef(false);

  const mutation = useMutation({
    mutationFn: ({
      decision,
      scope = "once",
      breadth = "exact",
    }: DecisionRequest) => decide(action.id, decision, scope, breadth),
    onError: (error, { summary }) => {
      setLastError(error);
      if (!isGone(error)) {
        return;
      }
      const { title, hint } = explain(error);
      toast.add({
        description: summary ? `${hint} ${summary}` : hint,
        timeout: 0,
        title,
        type: "error",
      });
    },
    onSettled: () => {
      inFlight.current = false;
      void queryClient.invalidateQueries({
        predicate: ({ queryKey }) =>
          queryKey[0] === "approvals" ||
          queryKey[0] === "chats" ||
          queryKey[0] === "rules" ||
          (queryKey[0] === "chat" && queryKey[1] === action.chat_id),
      });
    },
    onSuccess: (_, { scope, summary }) => {
      setLastError(null);
      if (scope === "chat" || scope === "always") {
        toast.add({
          description: summary,
          title: m.approval_allowed_and_the_permission_is_saved(),
          type: "success",
        });
      }
    },
  });

  /** Two taps that land before the buttons disable still send one decision. */
  const mutate = (request: DecisionRequest) => {
    if (inFlight.current) {
      return;
    }
    inFlight.current = true;
    mutation.mutate(request);
  };

  return {
    ...mutation,
    failure: lastError
      ? { stage: "decision" as const, permission: null }
      : null,
    mutate,
  };
}
