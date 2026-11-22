import { IconDots, IconKey, IconPlus, IconTrash } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, type ReactNode, useState } from "react";
import { explain } from "@/api/errors.ts";
import type { Credential, CredentialCreate, SecretType } from "@/api/types.ts";
import { ConfirmAction } from "@/components/confirm-action.tsx";
import { FormField } from "@/components/form-field.tsx";
import { ErrorState, LoadingRows } from "@/components/states.tsx";
import { type StatusMeta, StatusText } from "@/components/status-text.tsx";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog.tsx";
import { Button } from "@/components/ui/button.tsx";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible.tsx";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog.tsx";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu.tsx";
import { FieldLegend, FieldSet } from "@/components/ui/field.tsx";
import { Input } from "@/components/ui/input.tsx";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select.tsx";
import { Switch } from "@/components/ui/switch.tsx";
import { toast } from "@/components/ui/toast.tsx";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group.tsx";
import {
  createCredential,
  deleteCredential,
  revokeCredential,
  updateCredential,
  watchKeys,
  watchQueries,
} from "@/features/watch/api.ts";
import { m } from "@/paraglide/messages.js";

const HEADER_PRESETS = [
  {
    header: "Authorization",
    id: "bearer",
    label: () => `${m.credential_authorization()} / ${m.credential_bearer()}`,
    prefix: "Bearer ",
  },
  { header: "X-API-Key", id: "api-key", label: () => "X-API-Key", prefix: "" },
  { header: "Cookie", id: "cookie", label: () => "Cookie", prefix: "" },
] as const;

const hostList = (text: string): string[] =>
  text
    .split(/[\s,]+/)
    .map((host) => host.trim())
    .filter(Boolean);

function statusOf(credential: Credential): StatusMeta {
  if (credential.status === "revoked") {
    return { label: m.credential_revoked(), tone: "neutral" };
  }
  if (credential.expires_at && Date.parse(credential.expires_at) < Date.now()) {
    return { label: m.credential_expired(), tone: "attention" };
  }
  if (credential.failed_at) {
    return { label: m.credential_rejected(), tone: "danger" };
  }
  return { label: m.credential_active(), tone: "ok" };
}

function grantText(credential: Credential): string {
  const grants = [
    credential.grants.agent ? m.credential_agent() : null,
    credential.grants.watch ? m.credential_watch() : null,
    credential.grants.env,
  ].filter((grant): grant is string => Boolean(grant));
  return grants.join(", ") || m.credential_no_grants();
}

export function CredentialForm({
  idPrefix,
  chatId,
  agent = false,
  watch = false,
  pending,
  onSubmit,
}: {
  idPrefix: string;
  /** Set for a credential only this chat may use; null is every chat. */
  chatId: string | null;
  agent?: boolean;
  watch?: boolean;
  pending: boolean;
  onSubmit: (body: CredentialCreate) => void;
}) {
  const [secretType, setSecretType] = useState<SecretType>("token");
  const [name, setName] = useState("");
  const [preset, setPreset] =
    useState<(typeof HEADER_PRESETS)[number]["id"]>("bearer");
  const [prefix, setPrefix] = useState("Bearer ");
  const [refreshUrl, setRefreshUrl] = useState("");
  const [value, setValue] = useState("");
  const [refreshToken, setRefreshToken] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [hosts, setHosts] = useState("");
  const [grantAgent, setGrantAgent] = useState(agent);
  const [grantWatch, setGrantWatch] = useState(watch);
  const [grantEnv, setGrantEnv] = useState(false);
  const [envName, setEnvName] = useState("");
  const [confirmEnv, setConfirmEnv] = useState(false);
  const [envAllowed, setEnvAllowed] = useState(false);

  const chosen =
    HEADER_PRESETS.find((item) => item.id === preset) ?? HEADER_PRESETS[0];
  const parsedHosts = hostList(hosts);
  const env = grantEnv && envName.trim() ? envName.trim() : null;
  const ready =
    name.trim() !== "" &&
    parsedHosts.length > 0 &&
    (secretType === "token"
      ? value.trim() !== ""
      : username.trim() !== "" && password !== "") &&
    (!grantEnv || envName.trim() !== "");

  const enableEnv = (on: boolean) => {
    if (on && !envAllowed) {
      setConfirmEnv(true);
      return;
    }
    setGrantEnv(on);
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!ready || pending) {
      return;
    }
    const grants = { agent: grantAgent, env, watch: grantWatch };
    const common = {
      chat_id: chatId,
      grants,
      hosts: parsedHosts,
      name: name.trim(),
    };
    onSubmit(
      secretType === "token"
        ? {
            ...common,
            header: chosen.header,
            prefix,
            refresh_token: refreshToken.trim() || null,
            refresh_url: refreshUrl.trim() || null,
            secret_type: "token",
            value,
          }
        : {
            ...common,
            password,
            secret_type: "login",
            username: username.trim(),
          },
    );
  };

  return (
    <form className="space-y-6" id={`${idPrefix}-form`} onSubmit={submit}>
      <FormField id={`${idPrefix}-name`} label={m.credential_name()}>
        <Input
          id={`${idPrefix}-name`}
          maxLength={100}
          onChange={(event) => setName(event.target.value)}
          placeholder={m.credential_github()}
          required
          value={name}
        />
      </FormField>
      <div className="space-y-2">
        <p className="text-sm font-medium" id={`${idPrefix}-type`}>
          {m.credential_secret()}
        </p>
        <ToggleGroup
          aria-labelledby={`${idPrefix}-type`}
          onValueChange={([next]) => {
            if (next === "token" || next === "login") {
              setSecretType(next);
            }
          }}
          size="sm"
          spacing={0}
          value={[secretType]}
          variant="outline"
        >
          <ToggleGroupItem value="token">
            {m.credential_token()}
          </ToggleGroupItem>
          <ToggleGroupItem value="login">
            {m.credential_login()}
          </ToggleGroupItem>
        </ToggleGroup>
      </div>
      {secretType === "token" ? (
        <>
          <FormField id={`${idPrefix}-header`} label={m.credential_header()}>
            <Select
              items={HEADER_PRESETS.map((item) => ({
                label: item.label(),
                value: item.id,
              }))}
              onValueChange={(next) => {
                const match = HEADER_PRESETS.find((item) => item.id === next);
                if (match) {
                  setPreset(match.id);
                  setPrefix(match.prefix);
                }
              }}
              value={preset}
            >
              <SelectTrigger className="w-full" id={`${idPrefix}-header`}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {HEADER_PRESETS.map((item) => (
                  <SelectItem key={item.id} value={item.id}>
                    {item.label()}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </FormField>
          <FormField id={`${idPrefix}-value`} label={m.credential_value()}>
            <Input
              autoComplete="off"
              id={`${idPrefix}-value`}
              onChange={(event) => setValue(event.target.value)}
              required
              spellCheck={false}
              type="password"
              value={value}
            />
          </FormField>
          <Collapsible>
            <CollapsibleTrigger className="text-muted-foreground text-sm underline-offset-4 hover:underline">
              {m.credential_advanced()}
            </CollapsibleTrigger>
            <CollapsibleContent className="space-y-5 pt-4">
              <FormField
                help={m.credential_text_sent_before_the_value_such_as_bearer()}
                id={`${idPrefix}-prefix`}
                label={m.credential_prefix()}
              >
                <Input
                  id={`${idPrefix}-prefix`}
                  onChange={(event) => setPrefix(event.target.value)}
                  spellCheck={false}
                  value={prefix}
                />
              </FormField>
              <FormField
                help={m.credential_https_address_on_one_of_the_hosts_that()}
                id={`${idPrefix}-refresh`}
                label={m.credential_refresh_url()}
              >
                <Input
                  id={`${idPrefix}-refresh`}
                  onChange={(event) => setRefreshUrl(event.target.value)}
                  placeholder="https://example.com/oauth/token"
                  spellCheck={false}
                  type="url"
                  value={refreshUrl}
                />
              </FormField>
              <FormField
                id={`${idPrefix}-refresh-token`}
                label={m.credential_refresh_token()}
              >
                <Input
                  autoComplete="off"
                  id={`${idPrefix}-refresh-token`}
                  onChange={(event) => setRefreshToken(event.target.value)}
                  spellCheck={false}
                  type="password"
                  value={refreshToken}
                />
              </FormField>
            </CollapsibleContent>
          </Collapsible>
        </>
      ) : (
        <>
          <FormField
            id={`${idPrefix}-username`}
            label={m.credential_username()}
          >
            <Input
              autoComplete="off"
              id={`${idPrefix}-username`}
              onChange={(event) => setUsername(event.target.value)}
              required
              value={username}
            />
          </FormField>
          <FormField
            id={`${idPrefix}-password`}
            label={m.credential_password()}
          >
            <Input
              autoComplete="off"
              id={`${idPrefix}-password`}
              onChange={(event) => setPassword(event.target.value)}
              required
              type="password"
              value={password}
            />
          </FormField>
        </>
      )}
      <FormField
        help={m.credential_example_com_or_example_com_at_least_one()}
        id={`${idPrefix}-hosts`}
        label={m.credential_hosts()}
      >
        <Input
          id={`${idPrefix}-hosts`}
          onChange={(event) => setHosts(event.target.value)}
          placeholder="api.example.com"
          required
          spellCheck={false}
          value={hosts}
        />
      </FormField>
      <FieldSet className="gap-3">
        <FieldLegend variant="label">{m.credential_grants()}</FieldLegend>
        <Grant
          checked={grantAgent}
          hint={m.credential_tools_may_send_a_token_or_type_a()}
          id={`${idPrefix}-agent`}
          label={m.credential_agent()}
          onChange={setGrantAgent}
        />
        <Grant
          checked={grantWatch}
          hint={m.credential_observe_polls_may_send_a_token_on_these()}
          id={`${idPrefix}-watch`}
          label={m.credential_watch()}
          onChange={setGrantWatch}
        />
        <Grant
          checked={grantEnv}
          hint={m.credential_the_chats_code_runs_receive_the_value_under()}
          id={`${idPrefix}-env`}
          label={m.credential_environment_variable()}
          onChange={enableEnv}
        />
        {grantEnv && (
          <FormField
            help={m.credential_letters_digits_and_underscores_the_executed_code_can()}
            id={`${idPrefix}-env-name`}
            label={m.credential_variable_name()}
          >
            <Input
              className="font-mono"
              id={`${idPrefix}-env-name`}
              onChange={(event) => setEnvName(event.target.value.toUpperCase())}
              placeholder="GITHUB_TOKEN"
              required
              spellCheck={false}
              value={envName}
            />
          </FormField>
        )}
      </FieldSet>
      <AlertDialog onOpenChange={setConfirmEnv} open={confirmEnv}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {m.credential_code_can_read_this_value()}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {m.credential_code_can_read_this_value_and_send_it()}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{m.common_cancel()}</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                setEnvAllowed(true);
                setGrantEnv(true);
              }}
            >
              {m.credential_allow_code_access()}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </form>
  );
}

function Grant({
  id,
  label,
  hint,
  checked,
  onChange,
}: {
  id: string;
  label: string;
  hint: string;
  checked: boolean;
  onChange: (on: boolean) => void;
}) {
  return (
    <label className="flex cursor-pointer items-start gap-3" htmlFor={id}>
      <Switch
        checked={checked}
        className="mt-0.5"
        id={id}
        onCheckedChange={onChange}
      />
      <span>
        <span className="font-medium">{label}</span>
        <span className="text-muted-foreground block text-sm">{hint}</span>
      </span>
    </label>
  );
}

function ReplaceSecretDialog({ credential }: { credential: Credential }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState("");
  const replace = useMutation({
    mutationFn: () =>
      updateCredential(
        credential.id,
        credential.secret_type === "token"
          ? { secret_type: "token", value }
          : { password: value, secret_type: "login" },
      ),
    onSuccess: async () => {
      setOpen(false);
      setValue("");
      toast.add({
        title: m.credential_replaced_the_secret_of({ name: credential.name }),
        type: "success",
      });
      await queryClient.invalidateQueries({ queryKey: watchKeys.credentials });
    },
  });
  return (
    <Dialog
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) {
          setValue("");
          replace.reset();
        }
      }}
      open={open}
    >
      <Button onClick={() => setOpen(true)} size="sm" variant="outline">
        {m.credential_replace_secret()}
      </Button>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{m.credential_replace_the_secret()}</DialogTitle>
          <DialogDescription>
            {m.credential_the_new_value_is_stored_encrypted_the_previous()}
          </DialogDescription>
        </DialogHeader>
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            replace.mutate();
          }}
        >
          <FormField
            id={`replace-${credential.id}`}
            label={
              credential.secret_type === "login"
                ? m.credential_password()
                : m.credential_value()
            }
          >
            <Input
              autoComplete="off"
              id={`replace-${credential.id}`}
              onChange={(event) => setValue(event.target.value)}
              required
              type="password"
              value={value}
            />
          </FormField>
          {replace.isError && <ErrorState error={replace.error} />}
          <DialogFooter>
            <Button disabled={replace.isPending || value === ""} type="submit">
              {replace.isPending
                ? m.credential_saving()
                : m.credential_replace()}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function CredentialRow({ credential }: { credential: Credential }) {
  const queryClient = useQueryClient();
  const [confirming, setConfirming] = useState<"revoke" | "delete" | null>(
    null,
  );
  const act = useMutation({
    mutationFn: (action: "revoke" | "delete") =>
      action === "revoke"
        ? revokeCredential(credential.id)
        : deleteCredential(credential.id),
    onError: (error) => {
      const { title, hint } = explain(error);
      toast.add({ description: hint, timeout: 0, title, type: "error" });
    },
    onSettled: () =>
      queryClient.invalidateQueries({ queryKey: watchKeys.credentials }),
  });
  const kind =
    credential.secret_type === "login"
      ? m.credential_login_2({ username: credential.username ?? "" })
      : (credential.header ?? m.credential_authorization_2());
  return (
    <li className="flex items-start gap-2 py-2.5">
      <div className="min-w-0 flex-1">
        <p className="truncate font-medium">{credential.name}</p>
        <p className="text-muted-foreground truncate text-xs">
          {kind} / {credential.hosts.join(", ")} / {grantText(credential)}
        </p>
        <StatusText className="mt-0.5 text-xs" tone={statusOf(credential).tone}>
          {statusOf(credential).label}
        </StatusText>
      </div>
      {credential.status === "active" && (
        <ReplaceSecretDialog credential={credential} />
      )}
      <DropdownMenu>
        <DropdownMenuTrigger
          render={
            <Button
              aria-label={m.credential_actions_for({ name: credential.name })}
              className="text-muted-foreground"
              size="icon-sm"
              variant="ghost"
            />
          }
        >
          <IconDots />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          {credential.status === "active" && (
            <DropdownMenuItem onClick={() => setConfirming("revoke")}>
              {m.common_revoke()}
            </DropdownMenuItem>
          )}
          <DropdownMenuSeparator />
          <DropdownMenuItem
            onClick={() => setConfirming("delete")}
            variant="destructive"
          >
            <IconTrash aria-hidden="true" />
            {m.common_delete()}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <ConfirmAction
        confirmLabel={
          confirming === "delete" ? m.common_delete() : m.common_revoke()
        }
        description={
          confirming === "delete"
            ? m.credential_this_permanently_removes_the_secret_its_audit_events()
            : m.credential_it_can_no_longer_be_used_you_can()
        }
        onConfirm={() => {
          if (confirming) {
            act.mutate(confirming);
          }
        }}
        onOpenChange={(open) => {
          if (!open) {
            setConfirming(null);
          }
        }}
        open={confirming !== null}
        title={
          confirming === "delete"
            ? m.credential_delete({ name: credential.name })
            : m.credential_revoke({ name: credential.name })
        }
      />
    </li>
  );
}

export function CredentialRows({
  credentials,
  empty,
}: {
  credentials: Credential[];
  empty: ReactNode;
}) {
  if (credentials.length === 0) {
    return empty;
  }
  return (
    <ul className="divide-y">
      {credentials.map((credential) => (
        <CredentialRow credential={credential} key={credential.id} />
      ))}
    </ul>
  );
}

function NewCredentialDialog({
  chatId,
  agent,
  watch,
  open,
  onOpenChange,
}: {
  chatId: string | null;
  agent?: boolean;
  watch?: boolean;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const queryClient = useQueryClient();
  const create = useMutation({
    mutationFn: createCredential,
    onSuccess: async (credential) => {
      onOpenChange(false);
      toast.add({
        title: m.credential_added({ name: credential.name }),
        type: "success",
      });
      await queryClient.invalidateQueries({ queryKey: watchKeys.credentials });
    },
  });
  return (
    <Dialog
      onOpenChange={(next) => {
        onOpenChange(next);
        if (!next) {
          create.reset();
        }
      }}
      open={open}
    >
      <DialogContent className="overflow-hidden">
        <DialogHeader>
          <DialogTitle>{m.credential_add_a_credential()}</DialogTitle>
          <DialogDescription>
            {chatId
              ? m.credential_only_this_chat_can_use_it_and_only()
              : m.credential_all_your_chats_can_use_it_on_the()}
          </DialogDescription>
        </DialogHeader>
        {open && (
          <CredentialForm
            agent={agent}
            chatId={chatId}
            idPrefix={chatId ?? "global"}
            onSubmit={(body) => create.mutate(body)}
            pending={create.isPending}
            watch={watch}
          />
        )}
        {create.isError && <ErrorState error={create.error} />}
        <DialogFooter>
          <Button
            disabled={create.isPending}
            form={`${chatId ?? "global"}-form`}
            type="submit"
          >
            {create.isPending
              ? m.credential_saving_2()
              : m.credential_add_credential()}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** Credentials of every chat, managed from Watch. */
export function Credentials() {
  const credentials = useQuery(watchQueries.credentials());
  const [adding, setAdding] = useState(false);
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
  return (
    <div className="space-y-3">
      <CredentialRows
        credentials={credentials.data}
        empty={
          <p className="text-muted-foreground text-sm">
            <IconKey aria-hidden="true" className="mr-1 inline size-4" />
            {m.credential_no_credentials_yet()}
          </p>
        }
      />
      <Button onClick={() => setAdding(true)} size="sm" variant="outline">
        <IconPlus data-icon="inline-start" />
        {m.credential_add_credential()}
      </Button>
      <NewCredentialDialog
        chatId={null}
        onOpenChange={setAdding}
        open={adding}
        watch
      />
    </div>
  );
}

export { NewCredentialDialog };
