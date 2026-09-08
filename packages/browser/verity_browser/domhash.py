"""One definition of a page's structural fingerprint. Used from two packages.

``dom_hash`` is a comparison primitive that a ``DRIFT`` verdict and a replay
diff both rest on. If two packages each carried their own copy of the
algorithm, the copies would drift -- and the drift would surface as a
structural difference nobody could explain, on a nightly canary, at 3am. So
this lives here, and :mod:`verity_capture._inject` imports it rather than
keeping a second copy. (The repo has shipped a bug of exactly this shape
before: a list that existed in four places, passed everywhere it was run, and
was wrong on a bare clone -- commit 802f6e3.)

**What it is.** Walk the element tree inside ``<body>`` in document order.
For each element emit its tag name and its explicit ``role`` attribute, and
nothing else -- no classes, no ids, no other attributes, no text. Join those
tokens with ``>`` and hash the string.

**What that buys.** The fingerprint is blind to everything cosmetic: a
renamed class, reflowed whitespace, reordered attributes, changed copy. It
changes only when the *structure* does -- an element added, removed, renamed,
or reordered. Those sensitivities are pinned in
``tests/browser/test_domhash.py``, in both directions.

**Why not SHA-256.** A browser page has no synchronous SHA-256:
``crypto.subtle.digest`` returns a promise, and the teaching recorder computes
this value inside a DOM event handler that must not stall or go async
(``verity_capture._inject``). Sending the raw skeleton string to Python to be
hashed there would add kilobytes to every recorded event. So the hash is two
independent non-cryptographic passes -- djb2 and FNV-1a, each 32 bits, both
exact in a browser -- concatenated into 64 bits. ADR-0014 has the collision
math; the short version is that ``dom_hash`` is only ever compared *pairwise*
(a baseline step against the same step in another run), never used for set
membership, so the relevant bound is 2**-64 per comparison, and a collision
can only ever *hide* a real change, never invent one.

**Known limits**, stated rather than tuned away:

* The token stream has no depth or close markers -- it is a pre-order
  ``tag+role`` walk. So wrapping an element around the *trailing* part of its
  siblings, without changing the order elements are first visited in, does not
  change the hash. Every reordering, and every reparent that changes visit
  order, still does. ``tests/browser/test_domhash.py`` pins both the
  sensitivity and this gap.
* A ``role`` added by script after load is seen; one that exists only in the
  accessibility tree is not.
* Non-BMP text is counted by code point here and by UTF-16 code unit in the
  browser. Page *structure* is element names, so this is theoretical.
"""

from __future__ import annotations

from html.parser import HTMLParser

#: HTML void elements: they never open a nesting level.
_VOID = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
})

#: Matches the in-page walker's guards, so a pathologically deep or wide DOM
#: is truncated the same way on both sides.
_MAX_DEPTH = 12
_MAX_PARTS = 4000

_MASK32 = 0xFFFFFFFF
_FNV_OFFSET = 0x811C9DC5
_FNV_PRIME = 0x01000193

#: The in-page implementation, as a function declaration. Byte-identical
#: algorithm to :func:`structural_hash`. The browser executor runs this one and
#: :mod:`verity_capture._inject` splices it into its page script, so a live
#: page and an offline string produce the same value;
#: ``tests/browser/test_domhash.py`` pins the two together against a real page.
DOM_HASH_JS = r"""
function __verityStructuralHash() {
  const MAX_DEPTH = 12, MAX_PARTS = 4000;
  const parts = [];
  const walk = (node, depth) => {
    if (depth > MAX_DEPTH || parts.length > MAX_PARTS) return;
    for (const child of node.children) {
      parts.push(child.tagName.toLowerCase() + (child.getAttribute("role") || ""));
      walk(child, depth + 1);
    }
  };
  walk(document.body || document.documentElement, 0);
  const joined = parts.join(">");
  let djb2 = 5381, fnv = 0x811c9dc5;
  for (let i = 0; i < joined.length; i++) {
    const c = joined.charCodeAt(i);
    djb2 = (djb2 * 33 + c) % 4294967296;
    fnv = Math.imul(fnv ^ c, 0x01000193) >>> 0;
  }
  const hex8 = (n) => (n >>> 0).toString(16).padStart(8, "0");
  return "dom1:" + hex8(djb2) + hex8(fnv);
}
"""

#: A ready-to-evaluate expression form, for ``page.evaluate``.
DOM_HASH_JS_CALL = f"() => {{ {DOM_HASH_JS}\n return __verityStructuralHash(); }}"


class _Skeleton(HTMLParser):
    """Collects the tag+role token stream, ignoring everything cosmetic.

    ``scoped`` mirrors the in-page walker, which starts from ``document.body``:
    only elements inside ``<body>`` are counted. A fragment with no ``<body>``
    is parsed unscoped so it still yields a stable value.
    """

    def __init__(self, *, scoped: bool) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._scoped = scoped
        self._active = not scoped
        self._depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._scoped and tag == "body":
            self._active = True
            self._depth = 0
            return
        if not self._active:
            return
        self._emit(tag, attrs)
        if tag not in _VOID:
            self._depth += 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._active and not (self._scoped and tag == "body"):
            self._emit(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if self._scoped and tag == "body":
            self._active = False
            return
        if self._active and tag not in _VOID and self._depth > 0:
            self._depth -= 1

    def _emit(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._depth > _MAX_DEPTH or len(self.parts) > _MAX_PARTS:
            return
        role = next((v for k, v in attrs if k == "role" and v), "")
        self.parts.append(tag + role)


def structural_hash(html: str) -> str:
    """The structural fingerprint of an HTML document.

    A document with no ``<body>`` hashes its whole element tree instead, so a
    fragment still produces a stable value rather than an empty one.
    """
    scoped = "<body" in html.lower()
    skeleton = _Skeleton(scoped=scoped)
    skeleton.feed(html)
    return _hash_skeleton(">".join(skeleton.parts))


def _hash_skeleton(text: str) -> str:
    """Two independent 32-bit passes -- djb2 and FNV-1a -- concatenated to 64
    bits. The same arithmetic the JS does; see the module docstring for why it
    is not SHA-256."""
    djb2 = 5381
    fnv = _FNV_OFFSET
    for char in text:
        code = ord(char)
        djb2 = (djb2 * 33 + code) & _MASK32
        fnv = ((fnv ^ code) * _FNV_PRIME) & _MASK32
    return f"dom1:{djb2:08x}{fnv:08x}"


__all__ = ["DOM_HASH_JS", "DOM_HASH_JS_CALL", "structural_hash"]
