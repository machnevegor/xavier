import { Children, isValidElement, useMemo } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import { CodeBlock } from "@/components/code-block.tsx";
import { CsvBlock } from "@/components/viz/csv-view.tsx";
import { InlineMath, MathBlock } from "@/components/viz/math.tsx";
import { MermaidBlock } from "@/components/viz/mermaid-block.tsx";
import { PlotlyBlock } from "@/components/viz/plotly-block.tsx";
import { artifactUrl } from "@/features/artifacts/api.ts";
import type { EmbedRef } from "@/features/chats/feed/answer-media.ts";
import { remarkMathText } from "@/lib/remark-math-text.ts";
import { m } from "@/paraglide/messages.js";

const NO_MEDIA: ReadonlyMap<string, string> = new Map();
const NO_EMBEDS: ReadonlyMap<string, EmbedRef> = new Map();

interface CodeProps {
  className?: string;
  children?: unknown;
}

const languageOf = (className: string | undefined) =>
  /language-([\w+#.-]+)/.exec(className ?? "")?.[1];

const textOf = (children: unknown) => String(children ?? "").replace(/\n$/, "");

/** A fence in the language of a drawing, chart or data view opens as one; the rest is code. */
function Fence({ code, language }: { code: string; language?: string }) {
  switch (language) {
    case "math":
      return <MathBlock tex={code} />;
    case "mermaid":
      return <MermaidBlock code={code} />;
    case "plotly":
      return <PlotlyBlock code={code} />;
    case "csv":
    case "tsv":
      return <CsvBlock code={code} language={language} />;
    default:
      return <CodeBlock code={code} language={language} />;
  }
}

function componentsOf(
  media: ReadonlyMap<string, string>,
  embeds: ReadonlyMap<string, EmbedRef>,
): Components {
  return {
    a: ({ href, children }) => {
      const embed = typeof href === "string" ? embeds.get(href) : undefined;
      if (embed?.status === "ok" && embed.artifact_id) {
        return (
          <a download href={artifactUrl(embed.artifact_id)}>
            {children}
          </a>
        );
      }
      if (embed && embed.status !== "ok") {
        return (
          <span className="text-muted-foreground">
            {children} ({embed.status})
          </span>
        );
      }
      return (
        <a href={href} rel="noopener noreferrer nofollow" target="_blank">
          {children}
        </a>
      );
    },
    code: ({ className, children }) =>
      className?.includes("math-inline") ? (
        <InlineMath tex={textOf(children)} />
      ) : (
        <code className={className}>{children}</code>
      ),
    img: ({ src, alt }) => {
      const url = typeof src === "string" ? src : undefined;
      const proxied = url ? media.get(url) : undefined;
      const embed = url ? embeds.get(url) : undefined;
      if (proxied) {
        return (
          <img
            alt={alt || ""}
            className="max-h-96 max-w-full rounded-md"
            loading="lazy"
            src={proxied}
          />
        );
      }
      if (embed?.status === "ok" && embed.artifact_id) {
        return (
          <img
            alt={alt || embed.name || ""}
            className="max-h-96 max-w-full rounded-md"
            loading="lazy"
            src={artifactUrl(embed.artifact_id)}
          />
        );
      }
      return (
        <a href={url} rel="noopener noreferrer nofollow" target="_blank">
          {alt || m.data_image()}
          {embed && embed.status !== "ok" ? ` (${embed.status})` : ""}
        </a>
      );
    },
    pre({ children }) {
      const [first] = Children.toArray(children);
      const props: CodeProps = isValidElement<CodeProps>(first)
        ? first.props
        : {};
      return (
        <Fence
          code={textOf(props.children)}
          language={languageOf(props.className)}
        />
      );
    },
    table: ({ children }) => (
      <div className="overflow-x-auto">
        <table>{children}</table>
      </div>
    ),
  };
}

/**
 * The agent's text is untrusted: raw HTML in it is shown as text, never run; unsafe link
 * targets are dropped by the renderer; links open without handing over this page. Remote
 * images load only through the same-origin media proxy or as stored artifacts.
 */
export function Markdown({
  children,
  media,
  embeds,
}: {
  children: string;
  media?: ReadonlyMap<string, string>;
  embeds?: ReadonlyMap<string, EmbedRef>;
}) {
  const components = useMemo(
    () => componentsOf(media ?? NO_MEDIA, embeds ?? NO_EMBEDS),
    [media, embeds],
  );
  return (
    <div className="prose-chat">
      <ReactMarkdown
        components={components}
        remarkPlugins={[remarkGfm, remarkMath, remarkMathText]}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
