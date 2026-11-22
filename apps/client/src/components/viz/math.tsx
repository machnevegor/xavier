import { useEffect, useMemo, useState } from "react";

type Katex = typeof import("katex").default;

let katex: Katex | null = null;
let loading: Promise<Katex> | null = null;

function loadKatex(): Promise<Katex> {
  loading ??= Promise.all([
    import("katex"),
    import("katex/dist/katex.min.css"),
  ]).then(([module]) => {
    katex = module.default;
    return katex;
  });
  return loading;
}

/** KaTeX, loaded with its stylesheet the first time a message holds mathematics. */
function useKatex(): Katex | null {
  const [lib, setLib] = useState(katex);
  useEffect(() => {
    if (!lib) {
      void loadKatex().then(setLib);
    }
  }, [lib]);
  return lib;
}

/**
 * The model's TeX is untrusted: commands that link, include or style by name stay disabled,
 * macro expansion is bounded, and an invalid expression is shown as its source in the error
 * colour rather than breaking the message.
 */
function toHtml(lib: Katex, tex: string, displayMode: boolean): string {
  return lib.renderToString(tex, {
    displayMode,
    maxExpand: 1000,
    maxSize: 40,
    strict: "ignore",
    throwOnError: false,
    trust: false,
  });
}

export function InlineMath({ tex }: { tex: string }) {
  const lib = useKatex();
  const html = useMemo(() => lib && toHtml(lib, tex, false), [lib, tex]);
  if (!html) {
    return <code>{tex}</code>;
  }
  return (
    <span className="math-inline" dangerouslySetInnerHTML={{ __html: html }} />
  );
}

export function MathBlock({ tex }: { tex: string }) {
  const lib = useKatex();
  const html = useMemo(() => lib && toHtml(lib, tex, true), [lib, tex]);
  if (!html) {
    return (
      <div className="math-display text-muted-foreground font-mono text-sm">
        {tex}
      </div>
    );
  }
  return (
    <div className="math-display" dangerouslySetInnerHTML={{ __html: html }} />
  );
}
