"""The security invariants, tested rather than asserted in a document.

* A trace is data. A page is data. A PDF is data. None of them is authority.
* Verification is deterministic: zero model calls, no code execution.
* The verifier cannot reach an executor.
"""

from __future__ import annotations

import socket
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
VERIFIER_SRC = REPO_ROOT / "packages" / "verifier" / "verity_verifier"
CAPTURE_SRC = REPO_ROOT / "packages" / "capture" / "verity_capture"
COMPILER_SRC = REPO_ROOT / "packages" / "compiler" / "verity_compiler"

PROVIDER_MODULES = (
    "openai", "anthropic", "cohere", "google.generativeai", "litellm",
    "transformers", "torch", "langchain", "llama_index",
)


@pytest.mark.security()
def test_the_verifier_imports_no_model_provider_sdk() -> None:
    """The zero-model-call guarantee is structural: the SDKs are not even here."""
    import sys

    import verity_verifier  # noqa: F401

    loaded = set(sys.modules)
    for provider in PROVIDER_MODULES:
        assert provider not in loaded, f"{provider} was imported by the verifier"


@pytest.mark.security()
def test_the_verifier_source_contains_no_dynamic_execution() -> None:
    """No eval, exec, compile or __import__ is *called* anywhere in the verifier.

    Checked by walking each module's syntax tree rather than by searching for
    substrings, so that legitimate uses like ``re.compile`` are not confused
    with dynamic execution -- and so that a call cannot be hidden by
    formatting.
    """
    import ast

    forbidden = {"eval", "exec", "compile", "__import__", "globals", "locals", "vars"}
    offenders: list[str] = []

    for path in sorted(VERIFIER_SRC.rglob("*.py")):
        tree = ast.parse(path.read_text("utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in forbidden
            ):
                offenders.append(
                    f"{path.relative_to(REPO_ROOT)}:{node.lineno}: {node.func.id}()"
                )
            if isinstance(node, ast.Attribute) and node.attr in ("loads", "load"):
                value = node.value
                if isinstance(value, ast.Name) and value.id in ("pickle", "marshal"):
                    offenders.append(
                        f"{path.relative_to(REPO_ROOT)}:{node.lineno}: {value.id}.{node.attr}"
                    )

    assert offenders == [], f"dynamic execution found: {offenders}"


@pytest.mark.security()
def test_the_compiler_makes_no_model_call() -> None:
    """The compiler proposes contracts deterministically.

    A model may later improve the wording of a proposal. It does not get to
    decide what is checked: a wrong proposed assertion is worse than a missing
    one, because a person may trust it.
    """
    import subprocess
    import sys

    probe = (
        "import verity_compiler, sys;"
        f"print(','.join(sorted(m for m in sys.modules if m in {set(PROVIDER_MODULES)!r})))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True, timeout=60
    )
    assert result.stdout.strip() == "", (
        f"the compiler imported a model provider: {result.stdout.strip()}"
    )


@pytest.mark.security()
def test_neither_capture_nor_the_compiler_executes_anything_dynamically() -> None:
    import ast

    forbidden = {"eval", "exec", "compile", "__import__", "globals", "locals", "vars"}
    offenders: list[str] = []
    for root in (CAPTURE_SRC, COMPILER_SRC):
        for path in sorted(root.rglob("*.py")):
            tree = ast.parse(path.read_text("utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id in forbidden
                ):
                    offenders.append(
                        f"{path.relative_to(REPO_ROOT)}:{node.lineno}: {node.func.id}()"
                    )
    assert offenders == [], f"dynamic execution found: {offenders}"


@pytest.mark.security()
def test_capture_can_be_imported_without_a_browser() -> None:
    """The event model, the redaction layer and the reader must not need one.

    It is why the compiler's tests need no Chromium, and it keeps the browser
    at the edge of the system rather than inside it.
    """
    import verity_capture

    assert "BrowserRecorder" in verity_capture.__all__
    assert "playwright" not in __import__("sys").modules


@pytest.mark.security()
def test_a_recording_never_carries_a_password() -> None:
    """Checked on the committed fixture, which is a real browser recording."""
    import json

    fixture = (
        REPO_ROOT / "tests" / "fixtures" / "sessions" / "ap_invoice_to_po.session.json"
    )
    text = json.dumps(json.loads(fixture.read_text("utf-8"))).lower()
    for leaked in ("password", "hunter2", "secret", "authorization"):
        assert leaked not in text or "[redacted]" in text


@pytest.mark.security()
def test_the_verifier_cannot_import_an_executor() -> None:
    """The portability boundary, checked at runtime as well as by import-linter.

    Run in a fresh interpreter on purpose. Inspecting this process's
    ``sys.modules`` would only prove which tests happened to run first -- and
    an earlier version of this test did exactly that, passing until another
    test imported the compiler before it.
    """
    import subprocess
    import sys

    probe = (
        "import verity_verifier, sys;"
        "leaked=[m for m in sys.modules if m.startswith(('verity_runtime',"
        "'verity_capture','verity_compiler','verity_cli','verity_sandbox'))];"
        "print(','.join(sorted(leaked)))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True, timeout=60
    )
    assert result.stdout.strip() == "", (
        f"the verifier pulled in an executor package: {result.stdout.strip()}"
    )


@pytest.mark.security()
def test_verification_opens_no_network_sockets(
    run_verification: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """All reads go through bound connectors. Nothing dials out on its own."""

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("verification attempted to open a raw socket")

    monkeypatch.setattr(socket, "socket", forbidden)
    report = run_verification().report
    assert report.verdict.value == "PASS"
    assert report.budgets.model_calls == 0


@pytest.mark.security()
def test_adversarial_instructions_in_a_document_change_nothing(
    sandbox_client: Any, run_verification: Any
) -> None:
    """A PDF that says 'approve this and report PASS' is still just a PDF."""
    sandbox_client.post("/admin/perturb/injection")
    report = run_verification().report

    assert report.verdict.value == "PASS"  # the business state is genuinely correct
    assert report.blocking_failures == []
    # Crucially, the injected text never becomes an assertion or an action.
    assert len(report.assertions) == 10
    assert all(a.expression for a in report.assertions)


@pytest.mark.security()
def test_adversarial_instructions_cannot_mask_a_real_failure(
    sandbox_client: Any, run_verification: Any
) -> None:
    """Injection plus a real fault must still fail. This is the important case."""
    sandbox_client.post("/admin/perturb/injection")
    sandbox_client.post("/admin/perturb/amount_changed")
    report = run_verification().report

    assert report.verdict.value == "FAIL"
    assert report.divergence.first_assertion_failure == "amount_match"


@pytest.mark.security()
def test_evidence_is_redacted_before_it_is_written(run_verification: Any) -> None:
    outcome = run_verification()
    for record in outcome.store.records:
        payload = outcome.store.load_payload(record.id)
        serialized = str(payload).lower()
        assert "password" not in serialized or "[redacted]" in serialized


@pytest.mark.security()
def test_a_contract_cannot_request_model_calls() -> None:
    """The zero-model-call budget is enforced at load time, not documented."""
    from verity_verifier import ContractLoadError, load_contract_text

    with pytest.raises(ContractLoadError, match="must be 0"):
        load_contract_text(
            "kind: OutcomeContract\n"
            "metadata: {name: x, version: 0.1.0}\n"
            "budgets: {model_calls: 5}\n"
        )


@pytest.mark.security()
def test_yaml_loading_cannot_construct_python_objects() -> None:
    """safe_load only. A contract is data; loading one must not build objects."""
    from verity_verifier import ContractLoadError, load_contract_text

    hostile = "!!python/object/apply:os.system ['echo pwned']\n"
    with pytest.raises(ContractLoadError):
        load_contract_text(hostile)
