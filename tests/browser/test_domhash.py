"""The structural page hash: blind to cosmetics, awake to structure.

``dom_hash`` is what a ``DRIFT`` verdict and a replay diff both rest on. If it
tripped on a renamed class it would cry wolf every night once the M3 canary
exists; if it slept through a removed element a replay would call two different
runs identical. Both directions are pinned here.

The implementation is not tuned to pass these -- where it cannot be made blind
to a cosmetic change, that is recorded as a known limitation below rather than
asserted away. As of M2c there is no such case.
"""

from __future__ import annotations

import pytest
from verity_browser.domhash import DOM_HASH_JS, structural_hash

BASE = """
<html><head><title>t</title></head><body>
  <main class="wrap" id="root" data-x="1">
    <table data-testid="grid">
      <tbody><tr><th>Vendor</th><td data-testid="v">Acme</td></tr></tbody>
    </table>
    <button type="submit" data-testid="go">Find</button>
  </main>
</body></html>
"""


def test_a_renamed_class_does_not_change_the_hash() -> None:
    other = BASE.replace('class="wrap"', 'class="container flow-root pt-4"')
    assert structural_hash(other) == structural_hash(BASE)


def test_reflowed_whitespace_does_not_change_the_hash() -> None:
    other = " ".join(BASE.split()).replace("> <", "><")
    assert structural_hash(other) == structural_hash(BASE)


def test_reordered_attributes_do_not_change_the_hash() -> None:
    other = BASE.replace(
        '<main class="wrap" id="root" data-x="1">',
        '<main data-x="1" id="root" class="wrap">',
    )
    assert structural_hash(other) == structural_hash(BASE)


def test_changed_text_and_data_do_not_change_the_hash() -> None:
    other = BASE.replace("Acme", "Globex Corporation").replace("Find", "Search now")
    assert structural_hash(other) == structural_hash(BASE)


def test_a_removed_element_changes_the_hash() -> None:
    other = BASE.replace('<button type="submit" data-testid="go">Find</button>', "")
    assert structural_hash(other) != structural_hash(BASE)


def test_a_renamed_element_changes_the_hash() -> None:
    other = BASE.replace("<button", "<a").replace("</button>", "</a>")
    assert structural_hash(other) != structural_hash(BASE)


def test_reordered_siblings_change_the_hash() -> None:
    """An element that moves relative to its siblings is a structural change."""
    a = "<body><main><p>x</p><button>go</button></main></body>"
    b = "<body><main><button>go</button><p>x</p></main></body>"
    assert structural_hash(a) != structural_hash(b)


def test_an_element_relocated_to_a_different_branch_changes_the_hash() -> None:
    before = "<body><header><nav>n</nav></header><main><p>x</p></main></body>"
    after = "<body><header></header><main><p>x</p><nav>n</nav></main></body>"
    assert structural_hash(before) != structural_hash(after)


def test_known_limitation_pure_nesting_depth_is_not_seen() -> None:
    """A blind spot, stated rather than tuned away.

    The fingerprint is a depth-first pre-order stream of tag+role tokens with
    no depth or close markers -- the same algorithm the teaching recorder has
    always used. So wrapping an element around the trailing part of its
    siblings, without changing the order elements are first visited in, is
    invisible to it. Every *reordering* or *reparenting that changes visit
    order* is still caught (the tests above); a canary that needs to catch a
    pure re-nest needs a second signal, and M2c does not add one.
    """
    flat = "<body><main><p>x</p><button>go</button></main></body>"
    absorbed = "<body><main><p>x<button>go</button></p></main></body>"
    assert structural_hash(flat) == structural_hash(absorbed)


def test_an_added_role_changes_the_hash() -> None:
    other = BASE.replace('data-testid="grid"', 'data-testid="grid" role="grid"')
    assert structural_hash(other) != structural_hash(BASE)


def test_a_fragment_with_no_body_still_hashes_stably() -> None:
    frag = '<div><span>x</span></div>'
    assert structural_hash(frag) == structural_hash(frag)
    assert structural_hash(frag) != structural_hash('<div><span>x</span><span>y</span></div>')


def test_the_hash_is_the_documented_shape() -> None:
    value = structural_hash(BASE)
    assert value.startswith("dom1:")
    body = value.removeprefix("dom1:")
    assert len(body) == 16  # two 32-bit lanes, hex, zero-padded
    assert all(c in "0123456789abcdef" for c in body)


def test_the_js_and_python_definitions_are_the_same_source_of_truth() -> None:
    """Both packages read this one string; neither keeps its own copy."""
    from verity_capture import _inject

    assert "__verityStructuralHash" in DOM_HASH_JS
    assert DOM_HASH_JS in _inject.INIT_SCRIPT
    assert "__VERITY_DOM_HASH_JS__" not in _inject.INIT_SCRIPT


@pytest.mark.parametrize("html", [BASE, "<body><p>x</p></body>", "<body></body>"])
def test_repeated_hashing_is_deterministic(html: str) -> None:
    assert len({structural_hash(html) for _ in range(20)}) == 1
