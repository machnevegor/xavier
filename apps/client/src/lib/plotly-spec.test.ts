import { describe, expect, it } from "vitest";
import { parsePlotlySpec } from "@/lib/plotly-spec.ts";

describe("parsePlotlySpec", () => {
  it("accepts a simple 2D figure on the cartesian bundle", () => {
    const spec = parsePlotlySpec(
      JSON.stringify({
        data: [{ type: "scatter", x: [0, 1], y: [0, 1] }],
        layout: { title: { text: "Line" } },
      }),
    );
    expect(spec.ok).toBe(true);
    if (spec.ok) {
      expect(spec.bundle).toBe("cartesian");
      expect(spec.is3d).toBe(false);
      expect(spec.figure.data).toHaveLength(1);
    }
  });

  it("loads the strict bundle for 3D traces", () => {
    const spec = parsePlotlySpec(
      JSON.stringify({
        data: [{ type: "scatter3d", x: [0], y: [0], z: [0] }],
      }),
    );
    expect(spec.ok).toBe(true);
    if (spec.ok) {
      expect(spec.bundle).toBe("strict");
      expect(spec.is3d).toBe(true);
    }
  });

  it("rejects invalid JSON and unknown traces", () => {
    const invalid = parsePlotlySpec("{");
    expect(invalid.ok).toBe(false);
    if (!invalid.ok) {
      expect(invalid.reason).toBe("This chart is not valid JSON.");
    }
    expect(
      parsePlotlySpec(JSON.stringify({ data: [{ type: "not-a-trace" }] })).ok,
    ).toBe(false);
  });

  it("drops layout images that would load remote URLs", () => {
    const spec = parsePlotlySpec(
      JSON.stringify({
        data: [{ type: "bar", x: ["a"], y: [1] }],
        layout: { images: [{ source: "https://evil.example/x.png" }] },
      }),
    );
    expect(spec.ok).toBe(true);
    if (spec.ok) {
      expect(spec.figure.layout.images).toBeUndefined();
    }
  });
});
