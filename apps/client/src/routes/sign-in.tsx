import { useMutation, useQueryClient } from "@tanstack/react-query";
import { createFileRoute, useRouter } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { explain, isUnauthorized } from "@/api/errors.ts";
import { Logo } from "@/components/brand/logo.tsx";
import { FormField } from "@/components/form-field.tsx";
import { SignInNotes } from "@/components/project-notes.tsx";
import { Button } from "@/components/ui/button.tsx";
import { Input } from "@/components/ui/input.tsx";
import { signIn } from "@/features/auth/api.ts";
import { LocaleSelect } from "@/lib/locale.tsx";
import { m } from "@/paraglide/messages.js";

/** Only paths inside this app; anything else (another origin, `//host`) goes to Work. */
function safeRedirect(value: unknown): string {
  return typeof value === "string" &&
    value.startsWith("/") &&
    !value.startsWith("//") &&
    !value.startsWith("/sign-in")
    ? value
    : "/";
}

export const Route = createFileRoute("/sign-in")({
  component: SignIn,
  head: () => ({ meta: [{ title: m.sign_in_sign_in_xavier() }] }),
  validateSearch: (search: Record<string, unknown>): { redirect?: string } =>
    typeof search.redirect === "string" ? { redirect: search.redirect } : {},
});

function signInError(error: unknown): string {
  return isUnauthorized(error)
    ? m.sign_in_that_username_or_password_was_not_accepted()
    : explain(error).title;
}

function SignIn() {
  const redirect = safeRedirect(Route.useSearch().redirect);
  const router = useRouter();
  const queryClient = useQueryClient();
  const [ready, setReady] = useState(false);
  useEffect(() => setReady(true), []);
  const mutation = useMutation({
    mutationFn: (body: { username: string; password: string }) =>
      signIn(body.username, body.password),
    onSuccess: () => {
      queryClient.clear();
      router.history.push(redirect);
    },
  });

  return (
    <main className="flex min-h-dvh items-center justify-center px-4 py-12">
      <div className="w-full max-w-sm space-y-8">
        <div className="space-y-3">
          <Logo className="text-primary size-8" />
          <h1 className="type-title">{m.sign_in_sign_in_to_xavier()}</h1>
          <p className="text-muted-foreground">
            {m.meta_supervise_an_agent_that_observes_runs_code_and()}
          </p>
        </div>
        <form
          className="space-y-6"
          onSubmit={(event) => {
            event.preventDefault();
            const data = new FormData(event.currentTarget);
            mutation.mutate({
              password: String(data.get("password") ?? ""),
              username: String(data.get("username") ?? ""),
            });
          }}
        >
          <FormField id="username" label={m.sign_in_username()}>
            <Input
              autoComplete="username"
              autoFocus
              id="username"
              name="username"
              required
              spellCheck={false}
            />
          </FormField>
          <FormField
            error={mutation.isError ? signInError(mutation.error) : undefined}
            id="password"
            label={m.sign_in_password()}
          >
            <Input
              aria-invalid={mutation.isError || undefined}
              autoComplete="current-password"
              id="password"
              name="password"
              required
              type="password"
            />
          </FormField>
          <Button
            className="w-full"
            disabled={!ready || mutation.isPending}
            type="submit"
          >
            {mutation.isPending ? m.sign_in_checking() : m.sign_in_sign_in()}
          </Button>
        </form>
        <FormField id="language" label={m.sign_in_language()}>
          <LocaleSelect id="language" />
        </FormField>
        <SignInNotes />
      </div>
    </main>
  );
}
