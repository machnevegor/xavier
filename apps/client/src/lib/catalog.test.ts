import { readFileSync } from "node:fs";
import { expect, test } from "vitest";

/** Both catalogs are one set of keys; a missing translation fails here. */
test("english and russian have the same message keys", () => {
  const en: unknown = JSON.parse(
    readFileSync(new URL("../../messages/en.json", import.meta.url), "utf8"),
  );
  const ru: unknown = JSON.parse(
    readFileSync(new URL("../../messages/ru.json", import.meta.url), "utf8"),
  );
  expect(en && typeof en === "object" && !Array.isArray(en)).toBe(true);
  expect(ru && typeof ru === "object" && !Array.isArray(ru)).toBe(true);
  expect(Object.keys(ru as object).sort()).toEqual(
    Object.keys(en as object).sort(),
  );
});
