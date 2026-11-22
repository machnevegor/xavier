import {
  IconAdjustmentsHorizontal,
  IconArrowUp,
  IconCode,
  IconDeviceDesktop,
  IconEye,
  IconFolder,
} from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import type { ToolGroup } from "@/api/types.ts";
import { ErrorState } from "@/components/states.tsx";
import { Button } from "@/components/ui/button.tsx";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible.tsx";
import { Textarea } from "@/components/ui/textarea.tsx";
import {
  AttachButton,
  AttachmentList,
  FileDropZone,
  pasteFiles,
} from "@/features/artifacts/attachment-picker.tsx";
import { useAttachments } from "@/features/artifacts/use-attachments.ts";
import { catalogQueries } from "@/features/catalog/api.ts";
import { chatKeys, createChat } from "@/features/chats/api.ts";
import {
  ChatOptions,
  ModeToggle,
  offeredOptions,
  selectedModel,
  stepLimitReady,
  toChatSettings,
  useLastOptions,
} from "@/features/chats/chat-options.tsx";
import { TOOL_GROUPS } from "@/features/chats/model.ts";
import { useAutoGrow } from "@/hooks/use-auto-grow.ts";
import { m } from "@/paraglide/messages.js";

const TOOL_ICONS: Partial<Record<ToolGroup, typeof IconEye>> = {
  compute: IconDeviceDesktop,
  execute: IconCode,
  files: IconFolder,
  observe: IconEye,
};

const STARTERS = [
  m.work_research_the_latest_python_release_and_cite_the,
  m.work_watch_https_example_com_status_and_tell_me,
  m.work_analyze_the_file_i_upload_and_chart_it,
  m.work_use_the_computer_to_open_a_site_and,
];

export function NewChatForm() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const models = useQuery(catalogQueries.models());
  const [text, setText] = useState("");
  const [options, setOptions] = useLastOptions();
  const attachments = useAttachments("new-chat");
  const field = useAutoGrow();
  const seesImages =
    selectedModel(models.data, options.model)?.input.includes("image") ?? false;
  const note = attachments.note(seesImages, options.tool_groups);

  const create = useMutation({
    mutationFn: () =>
      createChat({
        text: text.trim(),
        ...attachments.references(seesImages),
        ...toChatSettings(offeredOptions(options, models.data)),
      }),
    onSuccess: async (chat) => {
      attachments.clear();
      await queryClient.invalidateQueries({ queryKey: chatKeys.all });
      await navigate({ params: { chatId: chat.id }, to: "/chats/$chatId" });
    },
  });

  const canSend =
    (text.trim() !== "" || attachments.ready > 0) &&
    !attachments.uploading &&
    options.tool_groups.length > 0 &&
    stepLimitReady(options) &&
    !create.isPending;
  const submit = () => {
    if (canSend) {
      create.mutate();
    }
  };
  const groups = TOOL_GROUPS.filter((group) =>
    options.tool_groups.includes(group.value),
  );

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      <div className="mb-2">
        <ModeToggle
          describedBy="new-task-mode"
          onChange={(mode) => setOptions({ ...options, mode })}
          value={options.mode}
        />
      </div>
      <FileDropZone attachments={attachments}>
        <label className="sr-only" htmlFor="new-task">
          {m.work_describe_the_task()}
        </label>
        <Textarea
          className="max-h-72 min-h-24 resize-none border-0 bg-transparent p-4 text-base shadow-none focus-visible:ring-0 dark:bg-transparent"
          id="new-task"
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              submit();
            }
          }}
          onPaste={(event) => pasteFiles(event, attachments)}
          placeholder={m.work_research_a_question_watch_a_page_analyze_a()}
          ref={field}
          rows={3}
          value={text}
        />
        <AttachmentList attachments={attachments} className="px-3 pb-2" />
        {note && <p className="text-attention px-3 pb-2 text-xs">{note}</p>}
        <Collapsible>
          <div className="flex items-center justify-between gap-3 px-3 pb-3">
            <div className="flex min-w-0 items-center gap-1">
              <AttachButton attachments={attachments} />
              <CollapsibleTrigger
                aria-label={m.work_settings_the_agent_may_use({
                  groups:
                    groups.map((group) => group.label()).join(", ") ||
                    m.work_nothing(),
                })}
                render={
                  <Button
                    className="text-muted-foreground"
                    size="sm"
                    type="button"
                    variant="ghost"
                  />
                }
                title={m.work_agent_tools_model_and_budget()}
              >
                <IconAdjustmentsHorizontal aria-hidden="true" />
                {groups.length === 0 ? (
                  <span className="text-attention">{m.work_no_tools()}</span>
                ) : (
                  groups.map((group) => {
                    const Icon = TOOL_ICONS[group.value];
                    return Icon ? (
                      <Icon aria-hidden="true" key={group.value} />
                    ) : null;
                  })
                )}
              </CollapsibleTrigger>
            </div>
            <Button
              aria-label={
                create.isPending ? m.work_starting() : m.work_start_task()
              }
              className="shrink-0"
              disabled={!canSend}
              size="icon"
              type="submit"
            >
              <IconArrowUp />
            </Button>
          </div>
          <CollapsibleContent className="border-t p-4">
            <ChatOptions onChange={setOptions} value={options} />
          </CollapsibleContent>
        </Collapsible>
      </FileDropZone>
      {options.tool_groups.length === 0 && (
        <p className="text-attention mt-2">
          {m.work_choose_at_least_one_thing_the_agent_may()}
        </p>
      )}
      {create.isError && (
        <div className="mt-3">
          <ErrorState error={create.error} onRetry={submit} />
        </div>
      )}
      {!text && (
        <ul aria-label={m.work_example_tasks()} className="mt-4">
          {STARTERS.map((starter) => {
            const text = starter();
            return (
              <li key={text}>
                <button
                  className="text-muted-foreground hover:text-foreground block rounded py-1 text-left underline-offset-2 hover:underline"
                  onClick={() => setText(text)}
                  type="button"
                >
                  {text}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </form>
  );
}
