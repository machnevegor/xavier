import { describe, expect, it } from "vitest";
import {
  agentText,
  resolveWithAgent,
} from "@/features/approvals/credential-request-model.ts";

describe("resolveWithAgent", () => {
  it("keeps operator input", () => {
    expect(resolveWithAgent("  mine  ", "agent")).toBe("mine");
  });

  it("falls back to the agent suggestion when empty", () => {
    expect(resolveWithAgent("", "Bearer ")).toBe("Bearer ");
    expect(resolveWithAgent("   ", undefined)).toBe("");
  });
});

describe("agentText", () => {
  it("reads string fields from action arguments", () => {
    expect(agentText({ header: "Authorization" }, "header")).toBe("Authorization");
    expect(agentText({ header: "" }, "header")).toBeUndefined();
  });
});
