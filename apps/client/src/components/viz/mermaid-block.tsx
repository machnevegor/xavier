import { lazy, Suspense, useEffect, useState } from "react";
import {
  VizBlock,
  VizFallback,
  VizLoading,
} from "@/components/viz/viz-block.tsx";
import { useResolvedTheme } from "@/hooks/use-resolved-theme.ts";
import { useSettled } from "@/hooks/use-settled.ts";
import type { ResolvedTheme } from "@/lib/theme.ts";
import { m } from "@/paraglide/messages.js";

const ZoomView = lazy(() => import("@/components/viz/zoom-view.tsx"));

type Mermaid = typeof import("mermaid").default;

let loading: Promise<Mermaid> | null = null;
let queue: Promise<unknown> = Promise.resolve();
let sequence = 0;

const loadMermaid = () => {
  loading ??= import("mermaid").then((module) => module.default);
  return loading;
};

/**
 * One diagram at a time, because Mermaid keeps its configuration and scratch elements global.
 * The source comes from the model, so it renders with `strict` security: labels are
 * sanitised, click handlers and scripts are dropped, and diagram directives cannot loosen it.
 */
function renderDiagram(code: string, theme: ResolvedTheme): Promise<string> {
  const job = queue.then(async () => {
    const mermaid = await loadMermaid();
    mermaid.initialize({
      fontFamily:
        '"IBM Plex Sans Variable", ui-sans-serif, system-ui, sans-serif',
      maxTextSize: 50_000,
      securityLevel: "strict",
      startOnLoad: false,
      suppressErrorRendering: true,
      theme: theme === "dark" ? "dark" : "neutral",
    });
    sequence += 1;
    const { svg } = await mermaid.render(`mermaid-${sequence}`, code);
    return svg;
  });
  queue = job.catch(() => undefined);
  return job;
}

function reason(error: unknown): string {
  const first = (error instanceof Error ? error.message : String(error))
    .split("\n")[0]
    ?.trim();
  return first
    ? m.data_this_diagram_could_not_be_drawn({ reason: first.slice(0, 160) })
    : m.data_this_diagram_could_not_be_drawn_2();
}

type Drawn = { source: string } & ({ svg: string } | { error: string });

function Svg({ svg, expanded }: { svg: string; expanded: boolean }) {
  return (
    <div
      className={
        expanded
          ? "[&_svg]:h-auto [&_svg]:w-[min(100%,80rem)] [&_svg]:max-w-none!"
          : "flex justify-center overflow-x-auto p-3 [&_svg]:h-auto [&_svg]:max-w-full"
      }
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  );
}

/** A `mermaid` fence, drawn once complete and redrawn when the theme changes. */
export function MermaidBlock({ code }: { code: string }) {
  const settled = useSettled(code);
  const theme = useResolvedTheme();
  const [drawn, setDrawn] = useState<Drawn | null>(null);

  useEffect(() => {
    if (settled === null) {
      return;
    }
    let live = true;
    renderDiagram(settled, theme).then(
      (svg) => live && setDrawn({ source: settled, svg }),
      (error: unknown) =>
        live && setDrawn({ error: reason(error), source: settled }),
    );
    return () => {
      live = false;
    };
  }, [settled, theme]);

  const current = drawn?.source === code ? drawn : null;
  return (
    <VizBlock code={code} label={m.data_diagram()} language="mermaid">
      {(expanded) =>
        !current ? (
          <VizLoading />
        ) : "error" in current ? (
          <VizFallback code={code} language="mermaid" reason={current.error} />
        ) : expanded ? (
          <Suspense fallback={<VizLoading />}>
            <ZoomView>
              <Svg expanded svg={current.svg} />
            </ZoomView>
          </Suspense>
        ) : (
          <Svg expanded={false} svg={current.svg} />
        )
      }
    </VizBlock>
  );
}
