import { describe, expect, it } from "vitest";
import {
  isAccountPath,
  isActiveSection,
  placeFor,
  SECTIONS,
} from "@/components/app-shell/sections.ts";

function active(pathname: string): string[] {
  return SECTIONS.filter((section) => isActiveSection(section, pathname)).map(
    (section) => section.id,
  );
}

describe("navigation highlight", () => {
  it("marks Work on the home page and on a chat", () => {
    expect(active("/")).toEqual(["work"]);
    expect(active("/chats/abc")).toEqual(["work"]);
    expect(placeFor("/")?.label()).toBe("Work");
  });

  it("does not mark Work for Account or a nested schedule", () => {
    expect(active("/account")).toEqual([]);
    expect(isAccountPath("/account")).toBe(true);
    expect(placeFor("/account")?.label()).toBe("Account");
    expect(active("/schedules/new")).toEqual(["schedules"]);
    expect(active("/watch/new")).toEqual(["watch"]);
    expect(active("/guardrails/new")).toEqual(["guardrails"]);
  });

  it("names nothing when the path is none of the destinations", () => {
    expect(placeFor("/missing")).toBeUndefined();
    expect(active("/missing")).toEqual([]);
  });
});
