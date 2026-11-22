import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { Breadcrumbs, Page, PageHeader } from "@/components/page.tsx";
import { toast } from "@/components/ui/toast.tsx";
import { RuleBuilder } from "@/features/policy/rule-builder.tsx";
import { m } from "@/paraglide/messages.js";

export const Route = createFileRoute("/_auth/guardrails/$ruleId")({
  component: EditRulePage,
  head: () => ({
    meta: [{ title: m.guardrail_edit_rule_guardrails_xavier() }],
  }),
});

function EditRulePage() {
  const navigate = useNavigate();
  const { ruleId } = Route.useParams();
  return (
    <Page className="max-w-3xl space-y-6">
      <Breadcrumbs
        items={[
          { label: m.guardrail_guardrails(), to: "/guardrails" },
          { label: m.guardrail_edit_rule() },
        ]}
      />
      <PageHeader
        lead={m.guardrail_the_rule_is_replaced_in_one_step_when()}
        title={m.guardrail_edit_rule_2()}
      />
      <RuleBuilder
        onSaved={() => {
          toast.add({ title: m.guardrail_rule_updated(), type: "success" });
          void navigate({ to: "/guardrails" });
        }}
        ruleId={ruleId}
      />
    </Page>
  );
}
