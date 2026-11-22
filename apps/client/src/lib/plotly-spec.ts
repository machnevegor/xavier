import { isRecord } from "@/api/types.ts";
import { m } from "@/paraglide/messages.js";

type Obj = Record<string, unknown>;

export interface PlotlyFigure {
  data: Obj[];
  layout: Obj;
  frames?: Obj[];
  config?: Obj;
}

/** The part of the Plotly API the chart uses. */
export interface Plotly {
  react(root: HTMLElement, figure: PlotlyFigure): Promise<HTMLElement>;
  purge(root: HTMLElement): void;
  Plots: { resize(root: HTMLElement): void };
}

/** The partial bundle with the common 2D traces, and the full CSP-safe ("strict") bundle. */
export type PlotlyBundle = "cartesian" | "strict";

export type PlotlySpec =
  | { ok: true; figure: PlotlyFigure; bundle: PlotlyBundle; is3d: boolean }
  | { ok: false; reason: string };

const CARTESIAN = new Set([
  "bar",
  "box",
  "contour",
  "heatmap",
  "histogram",
  "histogram2d",
  "histogram2dcontour",
  "image",
  "pie",
  "scatter",
  "scatterternary",
  "violin",
]);

const THREE_D = new Set([
  "cone",
  "isosurface",
  "mesh3d",
  "scatter3d",
  "streamtube",
  "surface",
  "volume",
]);

/** Map and geo traces are left out: they load tiles and outlines from outside servers. */
const OTHER = new Set([
  "barpolar",
  "candlestick",
  "carpet",
  "contourcarpet",
  "funnel",
  "funnelarea",
  "icicle",
  "indicator",
  "ohlc",
  "parcats",
  "parcoords",
  "sankey",
  "scattercarpet",
  "scattergl",
  "scatterpolar",
  "scatterpolargl",
  "scattersmith",
  "splom",
  "sunburst",
  "table",
  "treemap",
  "waterfall",
]);

export const MAX_SOURCE_CHARS = 2_000_000;
export const MAX_VALUES = 100_000;
export const MAX_FRAMES = 300;

/** Whether the arrays in a value hold more than `limit` entries in total. */
function exceeds(value: unknown, limit: number): boolean {
  let count = 0;
  const stack: unknown[] = [value];
  while (stack.length > 0) {
    const next = stack.pop();
    if (Array.isArray(next)) {
      count += next.length;
      if (count > limit) {
        return true;
      }
      for (const item of next) {
        if (typeof item === "object") {
          stack.push(item);
        }
      }
    } else if (isRecord(next)) {
      for (const item of Object.values(next)) {
        stack.push(item);
      }
    }
  }
  return false;
}

const traceType = (trace: Obj): string =>
  typeof trace.type === "string" ? trace.type : "scatter";

/**
 * A `plotly` fence read as a declarative figure: `data` traces with an optional `layout` and
 * animation `frames`. Only known trace types pass, the figure's own `config` is ignored for
 * the app's, layout images (remote URLs) are dropped, and the amount of data is bounded so
 * a chart cannot stall the page.
 */
export function parsePlotlySpec(source: string): PlotlySpec {
  if (source.length > MAX_SOURCE_CHARS) {
    return { ok: false, reason: m.data_this_chart_is_too_large_to_draw_here() };
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(source);
  } catch {
    return { ok: false, reason: m.data_this_chart_is_not_valid_json() };
  }
  if (!isRecord(parsed) || !Array.isArray(parsed.data)) {
    return {
      ok: false,
      reason: m.data_a_plotly_chart_needs_an_object_with_a(),
    };
  }
  const frames = parsed.frames ?? [];
  if (!Array.isArray(frames) || !frames.every(isRecord)) {
    return {
      ok: false,
      reason: m.data_frames_must_be_an_array_of_objects(),
    };
  }
  if (frames.length > MAX_FRAMES) {
    return {
      ok: false,
      reason: m.data_this_chart_has_more_than_animation_frames({
        n: MAX_FRAMES,
      }),
    };
  }
  const data = parsed.data;
  if (data.length === 0 || !data.every(isRecord)) {
    return {
      ok: false,
      reason: m.data_data_must_be_a_non_empty_array_of(),
    };
  }
  const frameTraces = frames.flatMap((frame) =>
    Array.isArray(frame.data) ? frame.data.filter(isRecord) : [],
  );
  const types = new Set([...data, ...frameTraces].map(traceType));
  const unknown = [...types].find(
    (type) => !CARTESIAN.has(type) && !THREE_D.has(type) && !OTHER.has(type),
  );
  if (unknown) {
    return {
      ok: false,
      reason: m.data_the_trace_type_is_not_supported_here({
        type: unknown.slice(0, 40),
      }),
    };
  }
  if (exceeds([data, frames], MAX_VALUES)) {
    return {
      ok: false,
      reason: m.data_this_chart_has_more_than_values_too_many({
        n: MAX_VALUES,
      }),
    };
  }
  const { images: _images, ...layout } = isRecord(parsed.layout)
    ? parsed.layout
    : {};
  return {
    bundle: [...types].every((type) => CARTESIAN.has(type))
      ? "cartesian"
      : "strict",
    figure: { data, frames, layout },
    is3d: [...types].some((type) => THREE_D.has(type)),
    ok: true,
  };
}
