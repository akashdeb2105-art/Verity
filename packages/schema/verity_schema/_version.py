"""The single source of truth for Verity's version.

The version is declared once, in ``pyproject.toml``, and read back from the
installed distribution's metadata. Nothing else in the repository hardcodes a
version string, so a release cannot ship components that disagree about which
version they are.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _distribution_version

DISTRIBUTION_NAME = "verity"

#: Fallback used only when running from a source tree that was never installed
#: (for example a bare ``PYTHONPATH`` run). Kept deliberately obvious so that a
#: report produced that way is not mistaken for a released build.
_FALLBACK = "0.0.0+unknown"


def get_version() -> str:
    try:
        return _distribution_version(DISTRIBUTION_NAME)
    except PackageNotFoundError:
        return _FALLBACK


__version__ = get_version()
