// The prebuilt Plotly bundles ship without types; the chart uses only a small part of the API.
declare module "plotly.js-cartesian-dist-min" {
  import type { Plotly } from "@/lib/plotly-spec.ts";

  const plotly: Plotly;
  export default plotly;
}

declare module "plotly.js-strict-dist-min" {
  import type { Plotly } from "@/lib/plotly-spec.ts";

  const plotly: Plotly;
  export default plotly;
}
