import { IconKey, IconPlus } from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useState } from "react";
import type { Chat } from "@/api/types.ts";
import { ErrorState, LoadingRows } from "@/components/states.tsx";
import { Button } from "@/components/ui/button.tsx";
import { ProfileEmpty } from "@/features/chats/profile-sections.tsx";
import { watchQueries } from "@/features/watch/api.ts";
import {
  CredentialRows,
  NewCredentialDialog,
} from "@/features/watch/credentials.tsx";
import { m } from "@/paraglide/messages.js";

/** Credentials only this chat may use. Their values go in and never come back out. */
export function ChatCredentials({ chat }: { chat: Chat }) {
  const [adding, setAdding] = useState(false);
  const credentials = useQuery(watchQueries.credentials({ chatId: chat.id }));
  const shared = useQuery(watchQueries.credentials({ global: true }));
  if (credentials.isPending) {
    return <LoadingRows rows={2} />;
  }
  if (credentials.isError) {
    return (
      <ErrorState
        error={credentials.error}
        onRetry={() => void credentials.refetch()}
      />
    );
  }
  const sharedCount = (shared.data ?? []).filter(
    (credential) => credential.grants.agent && credential.status === "active",
  ).length;

  return (
    <div className="space-y-3">
      <CredentialRows
        credentials={credentials.data}
        empty={
          <ProfileEmpty icon={IconKey} title={m.chat_no_credentials()}>
            {m.chat_tokens_and_logins_this_chat_may_use_on()}
          </ProfileEmpty>
        }
      />
      <Button
        className="w-full"
        onClick={() => setAdding(true)}
        size="sm"
        variant="outline"
      >
        <IconPlus data-icon="inline-start" />
        {m.chat_add_credential()}
      </Button>
      {sharedCount > 0 && (
        <p className="text-muted-foreground text-xs">
          {m.chat_shared_credential_also_works_in_every_chat({
            count: sharedCount,
          })}{" "}
          <Link className="underline underline-offset-4" to="/watch/settings">
            {m.chat_manage()}
          </Link>
        </p>
      )}
      <NewCredentialDialog
        agent
        chatId={chat.id}
        onOpenChange={setAdding}
        open={adding}
      />
    </div>
  );
}
