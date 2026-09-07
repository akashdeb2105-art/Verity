"""JavaScript injected into every page of a teaching session.

Two rules govern this script.

It never sends the value of a password field. The check happens here, in the
page, so a password does not travel from the browser to Python at all -- Python
classifies again afterwards, but this is the first of the two barriers rather
than the only one.

It reports what it saw and never interprets it. No selectors are invented, no
intent is guessed; that is the compiler's work, and keeping it out of the page
means a recording stays valid when the compiler changes.
"""

INIT_SCRIPT = r"""
(() => {
  if (window.__verityInstalled) return;
  window.__verityInstalled = true;

  const MAX_TEXT = 200;
  const trim = (s) => (s == null ? null : String(s).slice(0, MAX_TEXT));

  // Values are never read from these. The barrier is here, in the page.
  const isSecretField = (el) => {
    if (!el) return false;
    const type = (el.type || "").toLowerCase();
    if (type === "password") return true;
    const auto = (el.getAttribute("autocomplete") || "").toLowerCase();
    return /current-password|new-password|one-time-code|cc-number|cc-csc|cc-exp/.test(auto);
  };

  const accessibleName = (el) => {
    if (!el) return null;
    const aria = el.getAttribute("aria-label");
    if (aria) return trim(aria);
    const labelledBy = el.getAttribute("aria-labelledby");
    if (labelledBy) {
      const target = document.getElementById(labelledBy);
      if (target) return trim(target.innerText);
    }
    if (el.id) {
      const label = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (label) return trim(label.innerText);
    }
    const wrapping = el.closest("label");
    if (wrapping) return trim(wrapping.innerText);
    if (el.tagName === "BUTTON" || el.tagName === "A") return trim(el.innerText);
    return null;
  };

  const cssPath = (el) => {
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && parts.length < 5) {
      let part = node.tagName.toLowerCase();
      if (node.id) { parts.unshift(`${part}#${CSS.escape(node.id)}`); break; }
      const testid = node.getAttribute && node.getAttribute("data-testid");
      if (testid) { parts.unshift(`[data-testid="${testid}"]`); break; }
      const cls = (node.className && typeof node.className === "string")
        ? node.className.trim().split(/\s+/).filter(Boolean).slice(0, 2) : [];
      if (cls.length) part += "." + cls.map((c) => CSS.escape(c)).join(".");
      const parent = node.parentElement;
      if (parent) {
        const siblings = Array.from(parent.children).filter((s) => s.tagName === node.tagName);
        if (siblings.length > 1) part += `:nth-of-type(${siblings.indexOf(node) + 1})`;
      }
      parts.unshift(part);
      node = node.parentElement;
    }
    return parts.join(" > ");
  };

  const describe = (el) => {
    if (!el) return null;
    return {
      role: el.getAttribute("role") || implicitRole(el),
      name: accessibleName(el),
      testid: el.getAttribute("data-testid"),
      label: accessibleName(el),
      tag: el.tagName.toLowerCase(),
      // The raw attribute, exactly as the page wrote it. Deliberately not
      // el.href: reading the resolved property stalls the recorder, and the
      // literal string is the better record anyway -- it is resolved against
      // the page address in Python, where the rule is visible and testable.
      href: el.getAttribute ? el.getAttribute("href") : null,
      input_type: (el.type || "").toLowerCase() || null,
      element_id: el.id || null,
      element_name: el.getAttribute("name") || null,
      placeholder: el.getAttribute("placeholder") || null,
      autocomplete: el.getAttribute("autocomplete") || null,
      aria_label: el.getAttribute("aria-label") || null,
      css: [cssPath(el)].filter(Boolean),
      text: trim(el.innerText || el.value === undefined ? el.innerText : null),
    };
  };

  const implicitRole = (el) => {
    const tag = el.tagName.toLowerCase();
    const type = (el.type || "").toLowerCase();
    if (tag === "a" && el.href) return "link";
    if (tag === "button") return "button";
    if (tag === "select") return "combobox";
    if (tag === "textarea") return "textbox";
    if (tag === "input") {
      if (["submit", "button", "reset"].includes(type)) return "button";
      if (type === "checkbox") return "checkbox";
      if (type === "radio") return "radio";
      if (type === "search") return "searchbox";
      return "textbox";
    }
    if (/^h[1-6]$/.test(tag)) return "heading";
    return null;
  };

  // Structural fingerprint: tags and roles only, all text discarded. Two pages
  // showing different data hash the same; a redesign does not.
  const domHash = () => {
    const parts = [];
    const walk = (node, depth) => {
      if (depth > 12 || parts.length > 4000) return;
      for (const child of node.children) {
        parts.push(child.tagName.toLowerCase() + (child.getAttribute("role") || ""));
        walk(child, depth + 1);
      }
    };
    walk(document.body || document.documentElement, 0);
    let hash = 5381;
    const joined = parts.join(">");
    for (let i = 0; i < joined.length; i++) hash = ((hash << 5) + hash + joined.charCodeAt(i)) | 0;
    return "djb2:" + (hash >>> 0).toString(16);
  };

  // Values the screen is actually showing. This is the raw material for
  // noticing that the person was comparing two numbers.
  const visibleValues = () => {
    const out = {};
    document.querySelectorAll("[data-testid]").forEach((el) => {
      const text = (el.innerText || "").trim();
      if (text && text.length <= MAX_TEXT && el.children.length === 0) {
        out[el.getAttribute("data-testid")] = text;
      }
    });
    document.querySelectorAll("tr").forEach((row) => {
      const header = row.querySelector("th");
      const cell = row.querySelector("td");
      if (header && cell) {
        const key = (header.innerText || "").trim();
        const value = (cell.innerText || "").trim();
        if (key && value && value.length <= MAX_TEXT) out[key] = value;
      }
    });
    return out;
  };

  const send = (kind, el, value) => {
    try {
      window.__verityEvent({
        kind,
        url: location.href,
        title: document.title,
        element: describe(el),
        value: value === undefined ? null : value,
        value_withheld_in_page: isSecretField(el),
        visible_text: visibleValues(),
        dom_hash: domHash(),
        ts: new Date().toISOString(),
      });
    } catch (err) { /* never let recording break the page */ }
  };

  // Links Verity opens through its own document channel rather than letting
  // the browser follow them.
  const DOCUMENT_HREF = /\.(pdf|docx?|xlsx?|csv|png|jpe?g|tiff?)(\?|$)/i;

  document.addEventListener("click", (e) => {
    // Cancel first, record second. A document link is handled by Verity, not
    // by the browser: the demonstration should not be derailed into a PDF
    // viewer the person then has to click Back out of, and a browser that
    // leaves the page to render or download a file takes the recorder with
    // it. The click itself is still recorded below, href and all, and the
    // file is fetched when the session ends -- so nothing is lost.
    const link = e.target.closest("a[href]");
    if (link && DOCUMENT_HREF.test(link.getAttribute("href") || "")) {
      e.preventDefault();
      e.stopPropagation();
    }

    const el = e.target.closest("a,button,input,select,textarea,[role],[data-testid]") || e.target;
    send("click", el, null);
  }, true);

  document.addEventListener("change", (e) => {
    const el = e.target;
    if (!el || !("value" in el)) return;
    const secret = isSecretField(el);
    const kind = el.tagName === "SELECT" ? "select" : "input";
    send(kind, el, secret ? null : el.value);
  }, true);

  document.addEventListener("submit", (e) => send("submit", e.target, null), true);

  window.__verityReady = true;
})();
"""
