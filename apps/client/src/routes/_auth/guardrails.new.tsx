import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { Breadcrumbs, Page, PageHeader } from "@/components/page.tsx";
import { toast } from "@/components/ui/toast.tsx";
import { RuleBuilder } from "@/features/policy/rule-builder.tsx";
import { m } from "@/paraglide/messages.js";

export const Route = createFileRoute("/_auth/guardrails/new")({
  component: NewRulePage,
  head: () => ({
    meta: [{ title: m.guardrail_new_new_rule_guardrails_xavier() }],
  }),
  // ?tool= opens the form with that action chosen, as linked from an approval.
  validateSearch: (search: Record<string, unknown>): { tool?: string } =>
    typeof search.tool === "string" ? { tool: search.tool } : {},
});

function NewRulePage() {
  const navigate = useNavigate();
  const { tool } = Route.useSearch();
  return (
    <Page className="max-w-3xl space-y-6">
      <Breadcrumbs
        items={[
          { label: m.guardrail_new_guardrails(), to: "/guardrails" },
          { label: m.guardrail_new_new_rule() },
        ]}
      />
      <PageHeader
        lead={m.guardrail_new_answer_four_questions_you_will_see_what_changes()}
        title={m.guardrail_new_new_rule_2()}
      />
      <RuleBuilder
        onSaved={() => {
          toast.add({ title: m.guardrail_new_rule_saved(), type: "success" });
          void navigate({ to: "/guardrails" });
        }}
        presetTool={tool}
      />
    </Page>
  );
}
