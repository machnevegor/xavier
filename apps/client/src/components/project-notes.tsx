import { m } from "@/paraglide/messages.js";

const REPOSITORY = "https://github.com/machnevegor/xavier";
const TELEGRAM = "https://t.me/machnevegor";

function TextLink({ href, children }: { href: string; children: string }) {
  return (
    <a
      className="text-foreground underline underline-offset-2"
      href={href}
      rel="noopener noreferrer"
      target="_blank"
    >
      {children}
    </a>
  );
}

function ContributeSentence() {
  return (
    <>
      {m.common_found_a_bug_or_want_to_contribute_head()}{" "}
      <TextLink href={REPOSITORY}>{m.common_here()}</TextLink>.
    </>
  );
}

/** Beta invite and the repository, under the sign-in form. */
export function SignInNotes() {
  return (
    <footer className="text-muted-foreground space-y-2 border-t pt-6 text-sm">
      <p>
        {m.sign_in_want_beta_access_dm_me_at()}{" "}
        <TextLink href={TELEGRAM}>t.me/machnevegor</TextLink>.
      </p>
      <p>
        <ContributeSentence />
      </p>
    </footer>
  );
}

/** Where to report a bug or send a change. */
export function ContributeNote() {
  return (
    <p className="text-muted-foreground max-w-2xl text-sm">
      <ContributeSentence />
    </p>
  );
}
