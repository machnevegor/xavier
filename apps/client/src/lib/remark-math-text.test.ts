import { describe, expect, it } from "vitest";
import { remarkMathText } from "@/lib/remark-math-text.ts";

type Node = {
  type: string;
  value?: string;
  children?: Node[];
  position?: { start: { offset?: number }; end: { offset?: number } };
  data?: unknown;
};

function run(source: string, tree: Node): Node {
  remarkMathText()(tree, { value: source });
  return tree;
}

describe("remarkMathText", () => {
  it("promotes a lone $$ paragraph to display mathematics", () => {
    const source = "$$E=mc^2$$";
    const tree: Node = {
      children: [
        {
          children: [
            {
              position: { end: { offset: 10 }, start: { offset: 0 } },
              type: "inlineMath",
              value: "E=mc^2",
            },
          ],
          type: "paragraph",
        },
      ],
      type: "root",
    };
    const next = run(source, tree);
    expect(next.children?.[0]?.type).toBe("math");
    expect(next.children?.[0]?.value).toBe("E=mc^2");
  });

  it("keeps spaced dollars as ordinary text", () => {
    const source = "costs $5 and $10";
    const tree: Node = {
      children: [
        {
          children: [
            { type: "text", value: "costs " },
            {
              position: { end: { offset: 14 }, start: { offset: 6 } },
              type: "inlineMath",
              value: "5 and ",
            },
            { type: "text", value: "10" },
          ],
          type: "paragraph",
        },
      ],
      type: "root",
    };
    const next = run(source, tree);
    const nodes = next.children?.[0]?.children ?? [];
    expect(nodes.every((node) => node.type !== "inlineMath")).toBe(true);
    expect(nodes[1]?.type).toBe("text");
    expect(nodes[1]?.value).toBe("$5 and $");
  });
});
