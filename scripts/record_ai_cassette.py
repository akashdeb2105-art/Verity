#!/usr/bin/env python3
"""Re-record the committed model exchange used by the AI tests.

The cassette is keyed on the exact prompt, and the prompt contains the
recording. So when the reference recording changes, the cassette stops
matching -- correctly: it is a record of what a model said about a *specific*
question, and re-pointing an old answer at a new question would be a
fabrication, not a fixture.

Needs a provider configured in .env, and makes exactly one paid call. Run it
only when the reference recording has changed:

    python scripts/record_ai_cassette.py
    python -m pytest tests/integration/test_ai_loop.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SESSION = ROOT / "tests" / "fixtures" / "sessions" / "ap_invoice_to_po.session.json"
CASSETTE = ROOT / "tests" / "fixtures" / "ai" / "invoice_to_po.cassette.json"


def main() -> int:
    from verity_ai import AiCassette, AiCassetteMode, Budget, CassetteProvider, build_chain
    from verity_capture import read_session
    from verity_compiler import normalize, propose
    from verity_compiler.enrich import enrich

    provider = build_chain()
    if not provider.available():
        print(
            "No model provider is configured. Set VERITY_LLM_PROVIDER and the "
            "matching API key in .env, then run this again.",
            file=sys.stderr,
        )
        return 2

    steps = normalize(read_session(SESSION), SESSION)
    draft = propose(steps, name="invoice_to_po")

    CASSETTE.parent.mkdir(parents=True, exist_ok=True)
    if CASSETTE.exists():
        CASSETTE.unlink()

    recorder = CassetteProvider(provider, AiCassette(CASSETTE, AiCassetteMode.RECORD))
    result = enrich(draft, steps, recorder, budget=Budget())

    if not result.ok:
        print(f"The model call failed: {result.error}", file=sys.stderr)
        return 1

    print(f"Recorded to {CASSETTE.relative_to(ROOT)}")
    print(f"  {result.summary}")
    for suggestion in result.suggestions:
        print(f"    suggests   {suggestion.id}")
    for rejection in result.rejected:
        print(f"    discarded  {rejection}")
    print()
    print("The tests assert specific suggestion ids. If the model returned")
    print("different ones, update tests/integration/test_ai_loop.py to match")
    print("what it actually said -- do not edit the cassette.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
