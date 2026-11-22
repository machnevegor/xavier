// Page snapshot and element helpers. Runs in an isolated world, so the page cannot see or
// change it. Element ids ("e12") are stable for the life of a document and never repeat in a
// tab: computerd passes the first id of each new document to `start`.
(() => {
  if (globalThis.__xv) return;

  const ids = new WeakMap();
  const elements = new Map();
  let next = 1;

  const INTERACTIVE = [
    "a[href]", "button", "input:not([type=hidden])", "select", "textarea", "summary",
    "[contenteditable=''], [contenteditable=true]", "[tabindex]:not([tabindex='-1'])",
    "[role=button], [role=link], [role=checkbox], [role=radio], [role=tab], [role=menuitem]",
    "[role=option], [role=switch], [role=textbox], [role=combobox], [role=searchbox]",
    "[role=slider], [role=spinbutton], [role=treeitem], [onclick]",
  ].join(", ");
  const MAX_ELEMENTS = 300;
  const INPUT_ROLES = {
    button: "button", submit: "button", reset: "button", image: "button", checkbox: "checkbox",
    radio: "radio", range: "slider", number: "spinbutton", search: "searchbox",
  };

  function idOf(element) {
    let id = ids.get(element);
    if (!id) {
      id = `e${next++}`;
      ids.set(element, id);
      elements.set(id, new WeakRef(element));
    }
    return id;
  }

  function resolve(id) {
    const element = elements.get(id)?.deref();
    return element && element.isConnected ? element : null;
  }

  function clean(text, limit) {
    return (text || "").replace(/\s+/g, " ").trim().slice(0, limit);
  }

  function role(element) {
    const explicit = element.getAttribute("role");
    if (explicit) return explicit.split(" ")[0];
    const tag = element.tagName.toLowerCase();
    if (tag === "a") return "link";
    if (tag === "select") return "combobox";
    if (tag === "textarea") return "textbox";
    if (tag === "button" || tag === "summary") return "button";
    if (tag === "input") return INPUT_ROLES[element.type] || "textbox";
    if (element.isContentEditable) return "textbox";
    return "generic";
  }

  function name(element) {
    const label = element.getAttribute("aria-label");
    if (label) return clean(label, 80);
    const labelledBy = element.getAttribute("aria-labelledby");
    if (labelledBy) {
      const text = labelledBy.split(" ").map((id) => document.getElementById(id)?.innerText)
        .join(" ");
      if (clean(text, 80)) return clean(text, 80);
    }
    if (element.labels && element.labels.length) return clean(element.labels[0].innerText, 80);
    for (const attribute of ["placeholder", "title", "alt"]) {
      const value = element.getAttribute(attribute);
      if (value) return clean(value, 80);
    }
    const tag = element.tagName.toLowerCase();
    if (tag === "input" && ["button", "submit", "reset"].includes(element.type)) {
      return clean(element.value, 80);
    }
    if (tag === "input" || tag === "textarea" || tag === "select") return "";
    return clean(element.innerText || element.textContent, 80);
  }

  function isPassword(element) {
    return element.tagName === "INPUT" && element.type === "password";
  }

  function value(element) {
    if (isPassword(element)) return undefined;
    const tag = element.tagName.toLowerCase();
    if (tag === "select") return clean(element.selectedOptions[0]?.text, 100);
    if (tag === "input" && ["checkbox", "radio", "button", "submit", "reset"].includes(element.type)) {
      return undefined;
    }
    if (tag === "input" || tag === "textarea") return clean(element.value, 100) || undefined;
    return undefined;
  }

  function states(element) {
    const found = [];
    if (element.disabled || element.getAttribute("aria-disabled") === "true") found.push("disabled");
    if (element.checked || element.getAttribute("aria-checked") === "true") found.push("checked");
    if (element.getAttribute("aria-selected") === "true") found.push("selected");
    const expanded = element.getAttribute("aria-expanded");
    if (expanded) found.push(expanded === "true" ? "expanded" : "collapsed");
    if (element.required) found.push("required");
    if (element.readOnly) found.push("readonly");
    if (document.activeElement === element) found.push("focused");
    return found;
  }

  function visibleRect(element) {
    const rect = element.getBoundingClientRect();
    if (rect.width < 1 || rect.height < 1) return null;
    const left = Math.max(rect.left, 0);
    const top = Math.max(rect.top, 0);
    const right = Math.min(rect.right, innerWidth);
    const bottom = Math.min(rect.bottom, innerHeight);
    if (right <= left || bottom <= top) return null;
    const style = getComputedStyle(element);
    if (style.visibility === "hidden" || style.display === "none" || Number(style.opacity) === 0) {
      return null;
    }
    return { left, top, width: right - left, height: bottom - top };
  }

  function screenOffset() {
    return {
      x: screenX + Math.max(outerWidth - innerWidth, 0) / 2,
      y: screenY + Math.max(outerHeight - innerHeight, 0),
    };
  }

  function visibleText(limit) {
    if (!limit || !document.body) return "";
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    const parts = [];
    let length = 0;
    for (let node = walker.nextNode(); node && length < limit; node = walker.nextNode()) {
      const text = clean(node.textContent, limit);
      if (!text || !node.parentElement || !visibleRect(node.parentElement)) continue;
      const tag = node.parentElement.tagName;
      if (tag === "SCRIPT" || tag === "STYLE" || tag === "NOSCRIPT") continue;
      parts.push(text);
      length += text.length + 1;
    }
    return parts.join(" ").slice(0, limit);
  }

  function snapshot(textLimit) {
    const offset = screenOffset();
    const found = [];
    for (const element of document.querySelectorAll(INTERACTIVE)) {
      if (found.length >= MAX_ELEMENTS) break;
      const rect = visibleRect(element);
      if (!rect) continue;
      const entry = { id: idOf(element), role: role(element), name: name(element) };
      const current = value(element);
      if (current !== undefined) entry.value = current;
      const flags = states(element);
      if (flags.length) entry.states = flags;
      if (isPassword(element)) entry.password = true;
      entry.screen = [
        Math.round(offset.x + rect.left), Math.round(offset.y + rect.top),
        Math.round(rect.width), Math.round(rect.height),
      ];
      found.push(entry);
    }
    const scrolling = document.scrollingElement || document.documentElement;
    return {
      url: location.href,
      title: document.title,
      elements: found,
      truncated: found.length >= MAX_ELEMENTS,
      text: visibleText(textLimit),
      scroll: {
        y: Math.round(scrolling.scrollTop),
        height: Math.round(scrolling.scrollHeight),
        viewport: innerHeight,
      },
      next,
    };
  }

  function describe(element) {
    return `${role(element)} "${name(element)}"`;
  }

  function point(id) {
    const element = resolve(id);
    if (!element) {
      return { error: "stale", message: `element ${id} is gone; take a new snapshot` };
    }
    let rect = visibleRect(element);
    if (!rect) {
      element.scrollIntoView({ block: "center", inline: "center", behavior: "instant" });
      rect = visibleRect(element);
    }
    if (!rect) return { error: "not_visible", message: `element ${id} is not visible` };
    const x = rect.left + rect.width / 2;
    const y = rect.top + rect.height / 2;
    const hit = document.elementFromPoint(x, y);
    if (hit && hit !== element && !element.contains(hit) && !hit.contains(element)) {
      const label = hit.closest("label");
      if (!label || label.control !== element) {
        return { error: "occluded", message: `element ${id} is covered by ${describe(hit)}` };
      }
    }
    return { x, y, password: isPassword(element) };
  }

  function focus(id, clear) {
    const element = resolve(id);
    if (!element) return { error: "stale", message: `element ${id} is gone; take a new snapshot` };
    element.focus();
    if (!clear) return {};
    if (typeof element.select === "function") {
      element.select();
    } else if (element.isContentEditable) {
      const range = document.createRange();
      range.selectNodeContents(element);
      const selection = getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
    }
    return {};
  }

  function select(id, wanted) {
    const element = resolve(id);
    if (!element) return { error: "stale", message: `element ${id} is gone; take a new snapshot` };
    if (element.tagName !== "SELECT") {
      return { error: "invalid_args", message: `element ${id} is not a select` };
    }
    const option = [...element.options].find(
      (candidate) => candidate.value === wanted || clean(candidate.text, 200) === wanted,
    );
    if (!option) return { error: "invalid_args", message: `no option ${JSON.stringify(wanted)}` };
    element.value = option.value;
    element.dispatchEvent(new Event("input", { bubbles: true }));
    element.dispatchEvent(new Event("change", { bubbles: true }));
    return { selected: clean(option.text, 100) };
  }

  function extract(id, limit) {
    const element = id ? resolve(id) : document.body;
    if (!element) return { error: "stale", message: `element ${id} is gone; take a new snapshot` };
    const text = (element.innerText || "").trim();
    return { text: text.slice(0, limit), truncated: text.length > limit };
  }

  globalThis.__xv = {
    start(first) {
      next = Math.max(next, first);
      return { next };
    },
    snapshot,
    point,
    focus,
    select,
    extract,
    center: () => ({ x: innerWidth / 2, y: innerHeight / 2 }),
    contains: (text) => (document.body?.innerText || "").includes(text),
    html: (limit) => {
      const html = document.documentElement.outerHTML;
      return {
        html: html.slice(0, limit),
        truncated: html.length > limit,
        url: location.href,
        title: document.title,
      };
    },
    frames: () => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done))),
  };
})();
