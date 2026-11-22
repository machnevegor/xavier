import { defineConfig } from "@inlang/paraglide-js";

/** One year, in seconds. The server sets the same lifetime on a first visit. */
const ONE_YEAR = 60 * 60 * 24 * 365;

/**
 * Locale comes from the device cookie, then the browser language, then English.
 * There is no URL strategy: changing language never changes the address.
 */
const paraglide = defineConfig({
  cookieMaxAge: ONE_YEAR,
  cookieName: "xavier_locale",
  cookieSameSite: "lax",
  emitGitIgnore: false,
  emitReadme: false,
  emitTsDeclarations: true,
  outdir: "./src/paraglide",
  outputStructure: "locale-modules",
  strategy: ["cookie", "preferredLanguage", "baseLocale"],
});

export default paraglide;
