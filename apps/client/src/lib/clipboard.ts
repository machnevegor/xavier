import { m } from "@/paraglide/messages.js";

/**
 * Copy text to the clipboard. The Clipboard API exists only in secure contexts, and Xavier is
 * often opened over plain HTTP on a local network, so a selected off-screen field is copied
 * there instead.
 */
export async function copyText(text: string): Promise<void> {
  if (navigator.clipboard) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const field = document.createElement("textarea");
  field.value = text;
  field.setAttribute("readonly", "");
  field.style.position = "fixed";
  field.style.opacity = "0";
  document.body.append(field);
  field.select();
  // The Clipboard API is missing over plain HTTP on a LAN; execCommand is the fallback.
  const copied = document.execCommand("copy");
  field.remove();
  if (!copied) {
    throw new Error(m.common_clipboard_access_was_denied());
  }
}
