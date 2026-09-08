"""Verity's Tier-2 browser executor: drive a workflow, record what was seen.

The runtime walks a graph's read steps through a :class:`~verity_runtime.ports.BrowserDriver`
protocol and never imports this package; :mod:`verity_cli` adapts the two. What
this package owns is the browser itself and one shared definition of a page's
structural fingerprint (:mod:`verity_browser.domhash`), which
:mod:`verity_capture` imports rather than copying.
"""

from .domhash import DOM_HASH_JS, structural_hash
from .session import BrowserError, BrowserSession, PageObservation

__all__ = [
    "DOM_HASH_JS",
    "BrowserError",
    "BrowserSession",
    "PageObservation",
    "structural_hash",
]
