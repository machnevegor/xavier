/** The part of an mdast node this plugin reads; the tree is otherwise left as remark built it. */
interface MdNode {
  type: string;
  value?: string;
  children?: MdNode[];
  position?: { start: { offset?: number }; end: { offset?: number } };
  data?: unknown;
}

function displayMath(value: string): MdNode {
  return {
    data: {
      hChildren: [
        {
          children: [{ type: "text", value }],
          properties: { className: ["language-math", "math-display"] },
          tagName: "code",
          type: "element",
        },
      ],
      hName: "pre",
    },
    type: "math",
    value,
  };
}

function rewrite(parent: MdNode, source: string): void {
  if (!parent.children) {
    return;
  }
  parent.children = parent.children.map((node) => {
    const [only, ...rest] = node.children ?? [];
    if (
      node.type === "paragraph" &&
      only?.type === "inlineMath" &&
      rest.length === 0 &&
      source.startsWith("$$", only.position?.start.offset ?? -1)
    ) {
      return displayMath((only.value ?? "").trim());
    }
    if (node.type === "inlineMath" && /^\s|\s$/.test(node.value ?? "")) {
      const start = node.position?.start.offset;
      const end = node.position?.end.offset;
      return {
        type: "text",
        value:
          start === undefined || end === undefined
            ? `$${node.value}$`
            : source.slice(start, end),
      };
    }
    rewrite(node, source);
    return node;
  });
}

/**
 * Runs after remark-math to read dollars the way people write them: `$$…$$` alone in a
 * paragraph is display mathematics, and a `$…$` span that starts or ends with a space is
 * text, as in "$5 and $10", following Pandoc's rule.
 */
export function remarkMathText() {
  return (tree: MdNode, file: { value: unknown }) => {
    rewrite(tree, String(file.value));
  };
}
