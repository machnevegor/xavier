import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { Disclosure } from "@/components/disclosure.tsx";
import { FormField } from "@/components/form-field.tsx";
import { ErrorState } from "@/components/states.tsx";
import { Button } from "@/components/ui/button.tsx";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select.tsx";
import { Switch } from "@/components/ui/switch.tsx";
import { Textarea } from "@/components/ui/textarea.tsx";
import {
  AttachButton,
  AttachmentList,
} from "@/features/artifacts/attachment-picker.tsx";
import { useAttachments } from "@/features/artifacts/use-attachments.ts";
import { startRun } from "@/features/runs/api.ts";
import { LANGUAGES, type Language, parsePaths } from "@/features/runs/model.ts";
import { m } from "@/paraglide/messages.js";

const MAX_EXPORTS = 20;

export function RunForm() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [language, setLanguage] = useState<Language>("python");
  const [code, setCode] = useState("");
  const [network, setNetwork] = useState(false);
  const [exports, setExports] = useState("");
  const attachments = useAttachments("new-run");
  const paths = parsePaths(exports);
  const tooMany = paths.length > MAX_EXPORTS;

  const start = useMutation({
    mutationFn: () =>
      startRun({
        code,
        export: paths,
        input_artifact_ids:
          attachments.references(false).file_artifact_ids ?? [],
        language,
        network,
      }),
    onSuccess: async ({ id }) => {
      attachments.clear();
      await queryClient.invalidateQueries({ queryKey: ["executions"] });
      await navigate({ params: { executionId: id }, to: "/runs/$executionId" });
    },
  });
  const canRun =
    code.trim() !== "" &&
    !attachments.uploading &&
    !tooMany &&
    !start.isPending;
  const languages = LANGUAGES.map((item) => ({
    label: item.label(),
    value: item.value,
  }));

  return (
    <form
      className="max-w-3xl space-y-6"
      onSubmit={(event) => {
        event.preventDefault();
        if (canRun) {
          start.mutate();
        }
      }}
    >
      <FormField
        help={m.runs_runs_in_a_fresh_container_with_an_empty()}
        id="run-code"
        label={m.runs_code()}
      >
        <Textarea
          className="font-mono text-sm"
          id="run-code"
          onChange={(event) => setCode(event.target.value)}
          placeholder={
            LANGUAGES.find((item) => item.value === language)?.sample
          }
          required
          rows={6}
          spellCheck={false}
          value={code}
        />
      </FormField>
      <div className="flex flex-wrap items-end gap-4">
        <FormField id="run-language" label={m.runs_language()}>
          <Select
            items={languages}
            onValueChange={(next) => {
              const match = LANGUAGES.find((item) => item.value === next);
              if (match) {
                setLanguage(match.value);
              }
            }}
            value={language}
          >
            <SelectTrigger className="w-40" id="run-language">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {languages.map((item) => (
                <SelectItem key={item.value} value={item.value}>
                  {item.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </FormField>
        <label
          className="flex min-h-11 cursor-pointer items-center gap-3"
          htmlFor="run-network"
        >
          <Switch
            checked={network}
            id="run-network"
            onCheckedChange={setNetwork}
          />
          {m.runs_internet_access()}
        </label>
        <Button className="ml-auto" disabled={!canRun} type="submit">
          {start.isPending ? m.runs_starting() : m.runs_run()}
        </Button>
      </div>
      <Disclosure
        contentClassName="mt-3 space-y-4"
        label={m.runs_files_in_and_out()}
      >
        <div>
          <p className="text-sm font-medium">{m.runs_input_files()}</p>
          <p className="text-muted-foreground text-sm">
            {m.runs_copied_into_workspace_under_their_names_before_the()}
          </p>
          <div className="mt-2 flex items-center gap-2">
            <AttachButton attachments={attachments} />
            <AttachmentList attachments={attachments} />
          </div>
        </div>
        <FormField
          error={tooMany ? m.runs_at_most_paths({ n: MAX_EXPORTS }) : undefined}
          help={m.runs_paths_under_workspace_to_keep_as_downloadable_files()}
          id="run-export"
          label={m.runs_files_to_keep()}
        >
          <Textarea
            className="font-mono text-sm"
            id="run-export"
            onChange={(event) => setExports(event.target.value)}
            placeholder={m.runs_report_csv()}
            rows={2}
            spellCheck={false}
            value={exports}
          />
        </FormField>
      </Disclosure>
      {start.isError && <ErrorState error={start.error} />}
    </form>
  );
}
