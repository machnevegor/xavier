import { IconKey, IconPlus } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { explain } from "@/api/errors.ts";
import type { ApiKey, Session } from "@/api/types.ts";
import { CopyButton } from "@/components/copy-button.tsx";
import { Page, PageHeader, Section } from "@/components/page.tsx";
import { ContributeNote } from "@/components/project-notes.tsx";
import { ErrorState, LoadingRows } from "@/components/states.tsx";
import { Button } from "@/components/ui/button.tsx";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog.tsx";
import {
  Field,
  FieldDescription,
  FieldError,
  FieldLabel,
} from "@/components/ui/field.tsx";
import { Input } from "@/components/ui/input.tsx";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table.tsx";
import { toast } from "@/components/ui/toast.tsx";
import {
  accountQueries,
  createApiKey,
  revokeApiKey,
  revokeSession,
  sessionQuery,
  updateMe,
  updatePassword,
} from "@/features/auth/api.ts";
import { timeAgo } from "@/lib/format.ts";
import { type AppLocale, LocaleSelect } from "@/lib/locale.tsx";
import { m } from "@/paraglide/messages.js";
import { setLocale } from "@/paraglide/runtime.js";

export const Route = createFileRoute("/_auth/account")({
  component: AccountPage,
  head: () => ({ meta: [{ title: m.account_account_xavier() }] }),
});

function AccountPage() {
  const me = useQuery(sessionQuery());
  const name = me.data?.user.name || me.data?.user.username;

  return (
    <Page>
      <PageHeader
        lead={m.account_signed_in_devices_and_api_keys_for_other()}
        title={m.account_account()}
      >
        {name && <p className="text-muted-foreground text-sm">{name}</p>}
      </PageHeader>
      <Section title={m.account_preferences()}>
        <Language />
      </Section>
      <Section title={m.account_security()}>
        <Password />
      </Section>
      <Section title={m.account_signed_in_devices()}>
        <Devices />
      </Section>
      <Section title={m.account_api_keys()}>
        <Keys />
      </Section>
      <ContributeNote />
    </Page>
  );
}

function Language() {
  const queryClient = useQueryClient();
  const [pending, setPending] = useState(false);

  function choose(locale: AppLocale) {
    setPending(true);
    void updateMe(locale, queryClient)
      .then(() => {
        setLocale(locale);
      })
      .catch((error: unknown) => {
        setPending(false);
        toast.add({ title: explain(error).title, type: "error" });
      });
  }

  return (
    <div className="max-w-md space-y-2">
      <h3 className="text-sm font-medium">{m.account_language()}</h3>
      <p className="text-muted-foreground text-sm">
        {m.account_used_on_every_device_you_sign_in_to()}
      </p>
      <LocaleSelect disabled={pending} onLocale={choose} />
    </div>
  );
}

function Password() {
  const queryClient = useQueryClient();
  const [old, setOld] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const matches = next === confirm;
  const change = useMutation({
    mutationFn: () => updatePassword(old, next),
    onSuccess: async () => {
      setOld("");
      setNext("");
      setConfirm("");
      await queryClient.invalidateQueries({ queryKey: ["sessions"] });
      toast.add({ title: m.account_password_changed(), type: "success" });
    },
  });
  const ready = old !== "" && next.length >= 12 && confirm !== "" && matches;

  return (
    <form
      className="max-w-md space-y-4"
      onSubmit={(event) => {
        event.preventDefault();
        if (ready && !change.isPending) {
          change.mutate();
        }
      }}
    >
      <div>
        <h3 className="text-sm font-medium">{m.account_change_password()}</h3>
        <p className="text-muted-foreground mt-1 text-sm">
          {m.account_changing_your_password_signs_out_other_devices()}
        </p>
      </div>
      <Field>
        <FieldLabel htmlFor="current-password">
          {m.account_current_password()}
        </FieldLabel>
        <Input
          autoComplete="current-password"
          disabled={change.isPending}
          id="current-password"
          onChange={(event) => setOld(event.target.value)}
          required
          type="password"
          value={old}
        />
      </Field>
      <Field>
        <FieldLabel htmlFor="new-password">
          {m.account_new_password()}
        </FieldLabel>
        <Input
          autoComplete="new-password"
          disabled={change.isPending}
          id="new-password"
          minLength={12}
          onChange={(event) => setNext(event.target.value)}
          required
          type="password"
          value={next}
        />
        <FieldDescription>{m.account_password_requirement()}</FieldDescription>
      </Field>
      <Field data-invalid={confirm !== "" && !matches}>
        <FieldLabel htmlFor="confirm-password">
          {m.account_confirm_new_password()}
        </FieldLabel>
        <Input
          aria-invalid={confirm !== "" && !matches}
          autoComplete="new-password"
          disabled={change.isPending}
          id="confirm-password"
          onChange={(event) => setConfirm(event.target.value)}
          required
          type="password"
          value={confirm}
        />
        {confirm !== "" && !matches && (
          <FieldError>{m.account_passwords_do_not_match()}</FieldError>
        )}
      </Field>
      {change.isError && <ErrorState error={change.error} />}
      <Button disabled={!ready || change.isPending} type="submit">
        {change.isPending
          ? m.account_changing_password()
          : m.account_change_password()}
      </Button>
    </form>
  );
}

function Devices() {
  const sessions = useQuery(accountQueries.sessions());
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const revoke = useMutation({
    mutationFn: (session: Session) => revokeSession(session.id),
    onError: (error) =>
      toast.add({ title: explain(error).title, type: "error" }),
    onSuccess: async (_result, session) => {
      if (session.current) {
        queryClient.clear();
        await navigate({ search: { redirect: "/" }, to: "/sign-in" });
        return;
      }
      await queryClient.invalidateQueries({ queryKey: ["sessions"] });
    },
  });

  if (sessions.isPending) {
    return <LoadingRows rows={3} />;
  }
  if (sessions.isError) {
    return (
      <ErrorState
        error={sessions.error}
        onRetry={() => void sessions.refetch()}
      />
    );
  }
  const rows = sessions.data.items.filter((session) => !session.revoked_at);
  if (rows.length === 0) {
    return (
      <p className="text-muted-foreground text-sm">
        {m.account_no_signed_in_devices()}
      </p>
    );
  }
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>{m.account_device()}</TableHead>
          <TableHead>{m.account_ip()}</TableHead>
          <TableHead>{m.account_last_seen()}</TableHead>
          <TableHead className="w-24">
            <span className="sr-only">{m.account_actions()}</span>
          </TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((session) => (
          <TableRow key={session.id}>
            <TableCell className="max-w-xs">
              <span className="block truncate" title={session.user_agent}>
                {session.user_agent}
              </span>
              {session.current && (
                <span className="text-muted-foreground text-xs">
                  {m.account_this_device()}
                </span>
              )}
            </TableCell>
            <TableCell>{session.ip ?? m.common_text()}</TableCell>
            <TableCell>{timeAgo(session.last_seen_at)}</TableCell>
            <TableCell className="text-right">
              <Button
                disabled={revoke.isPending}
                onClick={() => revoke.mutate(session)}
                size="sm"
                variant="outline"
              >
                {m.account_revoke()}
              </Button>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

function Keys() {
  const keys = useQuery(accountQueries.keys());
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [created, setCreated] = useState<{ name: string; key: string } | null>(
    null,
  );
  const create = useMutation({
    mutationFn: () => createApiKey(name.trim()),
    onError: (error) =>
      toast.add({ title: explain(error).title, type: "error" }),
    onSuccess: async (result) => {
      setCreated({ key: result.key, name: result.data.name });
      setName("");
      await queryClient.invalidateQueries({ queryKey: ["api-keys"] });
    },
  });
  const revoke = useMutation({
    mutationFn: (id: string) => revokeApiKey(id),
    onError: (error) =>
      toast.add({ title: explain(error).title, type: "error" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["api-keys"] }),
  });

  return (
    <div className="space-y-4">
      <form
        className="flex flex-wrap items-end gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          if (name.trim()) {
            create.mutate();
          }
        }}
      >
        <Field className="min-w-48 flex-1">
          <FieldLabel htmlFor="api-key-name">{m.account_name()}</FieldLabel>
          <Input
            id="api-key-name"
            onChange={(event) => setName(event.target.value)}
            placeholder={m.account_what_this_key_is_for()}
            required
            value={name}
          />
        </Field>
        <Button disabled={create.isPending || !name.trim()} type="submit">
          <IconPlus data-icon="inline-start" />
          {m.account_create_key()}
        </Button>
      </form>
      {keys.isPending ? (
        <LoadingRows rows={2} />
      ) : keys.isError ? (
        <ErrorState error={keys.error} onRetry={() => void keys.refetch()} />
      ) : (
        <KeyList
          keys={keys.data.items.filter((key) => !key.revoked_at)}
          onRevoke={(id) => revoke.mutate(id)}
          pending={revoke.isPending}
        />
      )}
      <Dialog
        onOpenChange={(open) => {
          if (!open) {
            setCreated(null);
          }
        }}
        open={created !== null}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {m.account_copy({ name: created?.name ?? "" })}
            </DialogTitle>
            <DialogDescription>
              {m.account_this_key_is_shown_only_once_store_it()}
            </DialogDescription>
          </DialogHeader>
          {created && (
            <p className="bg-muted rounded-md px-3 py-2 font-mono text-sm break-all">
              {created.key}
            </p>
          )}
          <DialogFooter>
            {created && (
              <CopyButton label={m.account_copy_key()} value={created.key} />
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function KeyList({
  keys,
  onRevoke,
  pending,
}: {
  keys: ApiKey[];
  onRevoke: (id: string) => void;
  pending: boolean;
}) {
  if (keys.length === 0) {
    return (
      <p className="text-muted-foreground flex items-center gap-2 text-sm">
        <IconKey className="size-4" />
        {m.account_no_api_keys_yet()}
      </p>
    );
  }
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>{m.account_name_2()}</TableHead>
          <TableHead>{m.account_prefix()}</TableHead>
          <TableHead className="w-24">
            <span className="sr-only">{m.account_actions()}</span>
          </TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {keys.map((key) => (
          <TableRow key={key.id}>
            <TableCell>{key.name}</TableCell>
            <TableCell className="font-mono text-sm">{key.prefix}…</TableCell>
            <TableCell className="text-right">
              <Button
                disabled={pending}
                onClick={() => onRevoke(key.id)}
                size="sm"
                variant="outline"
              >
                {m.account_revoke()}
              </Button>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
