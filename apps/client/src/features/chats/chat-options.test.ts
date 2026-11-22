import { describe, expect, it } from "vitest";
import type { Chat } from "@/api/types.ts";
import {
  DEFAULT_OPTIONS,
  optionsOf,
  stepLimitReady,
  toChatSettings,
} from "@/features/chats/chat-options.tsx";

function chat(maxSteps: number | null): Chat {
  return {
    budget_usd: 5,
    execute_network: false,
    max_steps: maxSteps,
    mode: "supervised",
    model: "test",
    proactive: false,
    reasoning: "off",
    tool_groups: ["observe"],
  } as Chat;
}

describe("step limit", () => {
  it("opens a new chat on 100 steps in autonomous proactive mode", () => {
    const settings = toChatSettings(DEFAULT_OPTIONS);
    expect(settings.max_steps).toBe(100);
    expect(settings.mode).toBe("autonomous");
    expect(settings.proactive).toBe(true);
    expect(settings.tool_groups).toEqual(["observe", "execute", "compute"]);
    expect(stepLimitReady(DEFAULT_OPTIONS)).toBe(true);
  });

  it("sends a preset, a custom number, or no limit", () => {
    expect(
      toChatSettings({ ...DEFAULT_OPTIONS, max_steps: "250" }).max_steps,
    ).toBe(250);
    expect(
      toChatSettings({
        ...DEFAULT_OPTIONS,
        max_steps: "custom",
        max_steps_custom: "12",
      }).max_steps,
    ).toBe(12);
    expect(
      toChatSettings({ ...DEFAULT_OPTIONS, max_steps: "none" }).max_steps,
    ).toBeNull();
  });

  it("refuses an empty or fractional custom count", () => {
    expect(
      stepLimitReady({
        ...DEFAULT_OPTIONS,
        max_steps: "custom",
        max_steps_custom: "",
      }),
    ).toBe(false);
    expect(
      stepLimitReady({
        ...DEFAULT_OPTIONS,
        max_steps: "custom",
        max_steps_custom: "1.5",
      }),
    ).toBe(false);
  });

  it("reads a stored chat back into the same choice", () => {
    expect(optionsOf(chat(50)).max_steps).toBe("50");
    expect(optionsOf(chat(12))).toMatchObject({
      max_steps: "custom",
      max_steps_custom: "12",
    });
    expect(optionsOf(chat(null)).max_steps).toBe("none");
  });
});
