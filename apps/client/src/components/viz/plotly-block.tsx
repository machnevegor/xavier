import { useEffect, useMemo, useRef, useState } from "react";
import {
  VizBlock,
  VizFallback,
  VizLoading,
} from "@/components/viz/viz-block.tsx";
import { useResolvedTheme } from "@/hooks/use-resolved-theme.ts";
import { useSettled } from "@/hooks/use-settled.ts";
import {
  type Plotly,
  type PlotlyBundle,
  type PlotlyFigure,
  parsePlotlySpec,
} from "@/lib/plotly-spec.ts";
import type { ResolvedTheme } from "@/lib/theme.ts";
import { m } from "@/paraglide/messages.js";

const bundles = new Map<PlotlyBundle, Promise<Plotly>>();

/** Plotly is loaded on first use, and only the 2D bundle unless the chart needs more. */
function loadPlotly(bundle: PlotlyBundle): Promise<Plotly> {
  let loading = bundles.get(bundle);
  if (!loading) {
    loading = (
      bundle === "cartesian"
        ? import("plotly.js-cartesian-dist-min")
        : import("plotly.js-strict-dist-min")
    ).then((module) => module.default);
    bundles.set(bundle, loading);
  }
  return loading;
}

const FONT = '"IBM Plex Sans Variable", ui-sans-serif, system-ui, sans-serif';

const INK: Record<ResolvedTheme, { text: string; grid: string; line: string }> =
  {
    dark: {
      grid: "rgba(255,255,255,0.1)",
      line: "rgba(255,255,255,0.28)",
      text: "#ebe9e5",
    },
    light: {
      grid: "rgba(0,0,0,0.08)",
      line: "rgba(0,0,0,0.25)",
      text: "#1d1d22",
    },
  };

/** The figure's layout on the app's surfaces; anything the figure sets itself wins. */
function themed(layout: PlotlyFigure["layout"], theme: ResolvedTheme) {
  const ink = INK[theme];
  const axis = {
    gridcolor: ink.grid,
    linecolor: ink.line,
    zerolinecolor: ink.line,
  };
  const sceneAxis = { ...axis, backgroundcolor: "rgba(0,0,0,0)" };
  const { width: _width, height: _height, ...rest } = layout;
  return {
    margin: { b: 40, l: 48, r: 16, t: layout.title ? 48 : 24 },
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(0,0,0,0)",
    template: {
      layout: {
        polar: {
          angularaxis: axis,
          bgcolor: "rgba(0,0,0,0)",
          radialaxis: axis,
        },
        scene: { xaxis: sceneAxis, yaxis: sceneAxis, zaxis: sceneAxis },
        xaxis: axis,
        yaxis: axis,
      },
    },
    ...rest,
    autosize: true,
    font: {
      color: ink.text,
      family: FONT,
      ...(typeof layout.font === "object" ? layout.font : {}),
    },
  };
}

function Chart({
  figure,
  bundle,
  height,
  expanded,
  code,
}: {
  figure: PlotlyFigure;
  bundle: PlotlyBundle;
  height: number;
  expanded: boolean;
  code: string;
}) {
  const root = useRef<HTMLDivElement>(null);
  const theme = useResolvedTheme();
  const [ready, setReady] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  useEffect(() => {
    const element = root.current;
    if (!element) {
      return;
    }
    let live = true;
    let plotly: Plotly | null = null;
    const resize = new ResizeObserver(() => {
      if (plotly && element.isConnected) {
        plotly.Plots.resize(element);
      }
    });
    loadPlotly(bundle)
      .then((loaded) => {
        plotly = loaded;
        return live
          ? loaded.react(element, {
              config: {
                displaylogo: false,
                responsive: true,
                scrollZoom: expanded,
                showEditInChartStudio: false,
                showLink: false,
                showSendToCloud: false,
              },
              data: structuredClone(figure.data),
              frames: structuredClone(figure.frames),
              layout: themed(figure.layout, theme),
            })
          : undefined;
      })
      .then(
        () => {
          if (live) {
            setReady(true);
            resize.observe(element);
          }
        },
        (error: unknown) =>
          live &&
          setFailure(
            m.data_this_chart_could_not_be_drawn({
              reason: error instanceof Error ? error.message : String(error),
            }),
          ),
      );
    return () => {
      live = false;
      resize.disconnect();
    };
  }, [figure, bundle, expanded, theme]);

  useEffect(() => {
    const element = root.current;
    return () => {
      if (element) {
        void loadPlotly(bundle).then((plotly) => plotly.purge(element));
      }
    };
  }, [bundle]);

  if (failure) {
    return <VizFallback code={code} language="json" reason={failure} />;
  }
  return (
    <div className="relative" style={{ height: expanded ? "100%" : height }}>
      <div className="size-full" ref={root} />
      {!ready && (
        <VizLoading className="bg-background/60 absolute inset-0 h-auto" />
      )}
    </div>
  );
}

/** A `plotly` fence: a declarative Plotly figure with its own zoom, pan, rotation and frames. */
export function PlotlyBlock({ code }: { code: string }) {
  const settled = useSettled(code);
  const spec = useMemo(
    () => (settled === null ? null : parsePlotlySpec(settled)),
    [settled],
  );
  const height =
    spec?.ok && typeof spec.figure.layout.height === "number"
      ? Math.min(Math.max(spec.figure.layout.height, 200), 640)
      : spec?.ok && spec.is3d
        ? 420
        : 340;
  return (
    <VizBlock code={code} label={m.data_chart()} language="json">
      {(expanded) =>
        settled !== code || !spec ? (
          <VizLoading />
        ) : !spec.ok ? (
          <VizFallback code={code} language="json" reason={spec.reason} />
        ) : (
          <Chart
            bundle={spec.bundle}
            code={code}
            expanded={expanded}
            figure={spec.figure}
            height={height}
          />
        )
      }
    </VizBlock>
  );
}
