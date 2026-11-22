import { IconKey } from "@tabler/icons-react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { api, read } from "@/api/client.ts";
import { type Action, isRecord } from "@/api/types.ts";
import { Disclosure } from "@/components/disclosure.tsx";
import { AgentReason, FeedComposerField } from "@/components/feed-surface.tsx";
import { ErrorState } from "@/components/states.tsx";
import { Button } from "@/components/ui/button.tsx";
import { Input } from "@/components/ui/input.tsx";
import { decide } from "@/features/approvals/api.ts";
import {
  agentText,
  resolveWithAgent,
} from "@/features/approvals/credential-request-model.ts";
import { DecisionCard } from "@/features/approvals/decision-card.tsx";
import { timeAgo } from "@/lib/format.ts";
import { m } from "@/paraglide/messages.js";

function strings(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

/**
 * Approval card for `request_credential`: the secret is entered here and never reaches the
 * agent; approving stores a chat-scoped credential and continues the turn.
 */
export function CredentialRequestCard({ action }: { action: Action }) {
  const args = isRecord(action.arguments) ? action.arguments : {};
  const name =
    typeof args.name === "string" ? args.name : m.approval_credential();
  const secretType = args.type === "login" ? "login" : "token";
  const reason = typeof args.reason === "string" ? args.reason : "";
  const hosts = strings(args.hosts);
  const hints = useMemo(
    () => ({
      header: agentText(args, "header"),
      name,
      prefix: agentText(args, "prefix"),
      refreshUrl: agentText(args, "refresh_url"),
      username: agentText(args, "username"),
    }),
    [args, name],
  );
  const [value, setValue] = useState("");
  const [username, setUsername] = useState("");
  const [credentialName, setCredentialName] = useState("");
  const [header, setHeader] = useState("");
  const [prefix, setPrefix] = useState("");
  const [refreshUrl, setRefreshUrl] = useState("");
  const [refreshToken, setRefreshToken] = useState("");
  const queryClient = useQueryClient();
  const submit = useMutation({
    mutationFn: () => {
      const savedName = resolveWithAgent(credentialName, hints.name);
      if (secretType === "login") {
        return read(
          api.POST("/api/v1/credentials/requests/{action_id}", {
            body: {
              data: {
                password: value,
                secret_type: "login",
                username: resolveWithAgent(username, hints.username),
                ...(savedName !== name ? { name: savedName } : {}),
              },
            },
            params: { path: { action_id: action.id } },
          }),
        );
      }
      const resolvedHeader = resolveWithAgent(header, hints.header);
      const resolvedPrefix = resolveWithAgent(prefix, hints.prefix);
      const resolvedRefreshUrl = resolveWithAgent(refreshUrl, hints.refreshUrl);
      return read(
        api.POST("/api/v1/credentials/requests/{action_id}", {
          body: {
            data: {
              prefix: resolvedPrefix,
              secret_type: "token",
              value,
              ...(savedName !== name ? { name: savedName } : {}),
              ...(resolvedHeader ? { header: resolvedHeader } : {}),
              ...(resolvedRefreshUrl
                ? { refresh_url: resolvedRefreshUrl }
                : {}),
              ...(refreshToken ? { refresh_token: refreshToken } : {}),
            },
          },
          params: { path: { action_id: action.id } },
        }),
      );
    },
    onSuccess: () => {
      setValue("");
      setRefreshToken("");
    },
    onSettled: () =>
      void queryClient.invalidateQueries({
        predicate: ({ queryKey }) =>
          queryKey[0] === "approvals" ||
          queryKey[0] === "chats" ||
          queryKey[0] === "credentials" ||
          (queryKey[0] === "chat" && queryKey[1] === action.chat_id),
      }),
  });
  const reject = useMutation({
    mutationFn: () => decide(action.id, "reject"),
    onSettled: () =>
      void queryClient.invalidateQueries({
        predicate: ({ queryKey }) =>
          queryKey[0] === "approvals" ||
          (queryKey[0] === "chat" && queryKey[1] === action.chat_id),
      }),
  });
  const busy = submit.isPending || reject.isPending;
  const resolvedUsername = resolveWithAgent(username, hints.username);
  const ready =
    secretType === "login"
      ? resolvedUsername !== "" && value !== ""
      : value !== "";

  return (
    <DecisionCard
      ariaLabel={m.approval_credential_request({ name })}
      busy={busy}
      emphasis
      footer={
        <>
          <Button
            className="h-11 sm:h-8"
            disabled={busy || !ready}
            form={`cred-form-${action.id}`}
            size="sm"
            type="submit"
          >
            {submit.isPending
              ? m.approval_saving_2()
              : m.approval_save_and_allow()}
          </Button>
          <Button
            className="h-11 sm:h-8"
            disabled={busy}
            onClick={() => reject.mutate()}
            size="sm"
            variant="outline"
          >
            {reject.isPending ? m.approval_rejecting() : m.approval_reject()}
          </Button>
        </>
      }
      icon={<IconKey aria-hidden="true" />}
      label={m.approval_asks_for_a_credential()}
      meta={timeAgo(action.created_at)}
    >
      <p className="text-base font-medium">{name}</p>
      <p className="text-muted-foreground text-sm">
        {secretType === "login" ? m.approval_login() : m.approval_token()}
        {hosts.length > 0 && (
          <> {m.approval_for({ hosts: hosts.join(", ") })}</>
        )}
      </p>
      {reason && <AgentReason>{reason}</AgentReason>}
      <form
        autoComplete="off"
        className="mt-4 space-y-3"
        id={`cred-form-${action.id}`}
        onSubmit={(event) => {
          event.preventDefault();
          if (ready && !busy) {
            submit.mutate();
          }
        }}
      >
        {secretType === "login" && (
          <FeedComposerField
            id={`cred-user-${action.id}`}
            label={m.approval_username()}
          >
            <Input
              autoComplete="off"
              className="bg-background"
              disabled={busy}
              id={`cred-user-${action.id}`}
              onChange={(event) => setUsername(event.target.value)}
              placeholder={hints.username}
              value={username}
            />
          </FeedComposerField>
        )}
        <FeedComposerField
          id={`cred-value-${action.id}`}
          label={
            secretType === "login"
              ? m.approval_password()
              : m.approval_token_2()
          }
        >
          <Input
            autoComplete="off"
            className="bg-background"
            disabled={busy}
            id={`cred-value-${action.id}`}
            onChange={(event) => setValue(event.target.value)}
            required
            spellCheck={false}
            type="password"
            value={value}
          />
        </FeedComposerField>
        <p className="text-muted-foreground text-xs leading-relaxed">
          {m.approval_stored_encrypted_for_this_chat_only_the_agent()}
        </p>
        <Disclosure
          contentClassName="space-y-3 pt-2"
          label={m.credential_advanced()}
        >
          <FeedComposerField
            id={`cred-name-${action.id}`}
            label={m.credential_name()}
          >
            <Input
              className="bg-background"
              disabled={busy}
              id={`cred-name-${action.id}`}
              onChange={(event) => setCredentialName(event.target.value)}
              placeholder={hints.name}
              value={credentialName}
            />
          </FeedComposerField>
          {secretType === "token" && (
            <>
              <FeedComposerField
                id={`cred-header-${action.id}`}
                label={m.credential_header()}
              >
                <Input
                  className="bg-background"
                  disabled={busy}
                  id={`cred-header-${action.id}`}
                  onChange={(event) => setHeader(event.target.value)}
                  placeholder={hints.header}
                  value={header}
                />
              </FeedComposerField>
              <FeedComposerField
                hint={m.credential_text_sent_before_the_value_such_as_bearer()}
                id={`cred-prefix-${action.id}`}
                label={m.credential_prefix()}
              >
                <Input
                  className="bg-background"
                  disabled={busy}
                  id={`cred-prefix-${action.id}`}
                  onChange={(event) => setPrefix(event.target.value)}
                  placeholder={hints.prefix}
                  value={prefix}
                />
              </FeedComposerField>
              <FeedComposerField
                hint={m.credential_https_address_on_one_of_the_hosts_that()}
                id={`cred-refresh-url-${action.id}`}
                label={m.credential_refresh_url()}
              >
                <Input
                  className="bg-background"
                  disabled={busy}
                  id={`cred-refresh-url-${action.id}`}
                  onChange={(event) => setRefreshUrl(event.target.value)}
                  placeholder={hints.refreshUrl}
                  type="url"
                  value={refreshUrl}
                />
              </FeedComposerField>
              <FeedComposerField
                id={`cred-refresh-token-${action.id}`}
                label={m.credential_refresh_token()}
              >
                <Input
                  autoComplete="off"
                  className="bg-background"
                  disabled={busy}
                  id={`cred-refresh-token-${action.id}`}
                  onChange={(event) => setRefreshToken(event.target.value)}
                  spellCheck={false}
                  type="password"
                  value={refreshToken}
                />
              </FeedComposerField>
            </>
          )}
        </Disclosure>
      </form>
      {(submit.isError || reject.isError) && (
        <div className="mt-3">
          <ErrorState error={submit.error ?? reject.error} />
        </div>
      )}
    </DecisionCard>
  );
}
