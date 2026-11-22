import { expect, it } from "vitest";
import { presentJson, recoverJson } from "@/lib/json.ts";

it("reads a page snapshot the feed cut off inside a string", () => {
  const whole = JSON.stringify({
    action_id: "01a2046f-0c21-774d-82f5-a240c681f578",
    results: [{ action: "navigate", ok: true }],
    snapshot: {
      elements: 'e1 link ""\ne2 menuitem "Прогноз погоды"',
      text: "Главная\nНа месяц",
      url: "https://yandex.ru/pogoda/moscow",
    },
  });
  const cut = whole.slice(0, whole.indexOf("Главная") + 4);
  const recovered = recoverJson(cut);

  expect(recovered).toMatchObject({
    action_id: "01a2046f-0c21-774d-82f5-a240c681f578",
    results: [{ action: "navigate", ok: true }],
  });

  const shown = presentJson(recovered);
  const lines = shown.split("\n");
  expect(lines.some((line) => line.includes("e1 link"))).toBe(true);
  expect(lines.some((line) => line.includes("e2 menuitem"))).toBe(true);
  expect(lines.some((line) => line.includes("e1") && line.includes("e2"))).toBe(
    false,
  );

  const marker = "e2 menuitem";
  const at = whole.indexOf(marker);
  const partial = recoverJson(whole.slice(0, at + marker.length + 6));
  const partialLines = presentJson(partial).split("\n");
  expect(partialLines.some((line) => line.includes("e1 link"))).toBe(true);
  expect(partialLines.some((line) => line.includes("e2 menuitem"))).toBe(true);
  expect(
    partialLines.some((line) => line.includes("e1") && line.includes("e2")),
  ).toBe(false);
});

it("leaves a value that is not JSON alone", () => {
  expect(recoverJson("still running")).toBeUndefined();
  expect(recoverJson("")).toBeUndefined();
});
