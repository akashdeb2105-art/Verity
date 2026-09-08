"""The CLI contract: exit codes, machine-readable output, CI annotations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import pytest
from verity_cli.main import main

from tests.conftest import CONTRACT_PATH, REPO_ROOT

BASE_ARGS = [
    "--contract", str(CONTRACT_PATH), "--live",
    "--input", "invoice_number=INV-4471", "--input", "po_number=PO-2211",
    "--no-color",
]


@pytest.fixture()
def cli(monkeypatch: pytest.MonkeyPatch, sandbox_client: Any, tmp_path: Path) -> Any:
    """Run the CLI with its connectors pointed at the in-process sandbox."""
    from verity_connectors import build_sandbox_registry

    def _registry(args: Any) -> Any:
        return build_sandbox_registry("http://sandbox", client=sandbox_client)

    monkeypatch.setattr("verity_cli.main._build_registry", _registry)

    counter = {"n": 0}

    def _run(*extra: str) -> int:
        counter["n"] += 1
        return main([
            "verify", *BASE_ARGS,
            "--evidence-dir", str(tmp_path / f"evidence-{counter['n']}"),
            *extra,
        ])

    return _run


def test_pass_exits_zero(cli: Any) -> None:
    assert cli() == 0


def test_fail_exits_one(cli: Any, sandbox_client: Any) -> None:
    sandbox_client.post("/admin/perturb/amount_changed")
    assert cli() == 1


def test_inconclusive_exits_three(cli: Any, sandbox_client: Any) -> None:
    sandbox_client.post("/admin/perturb/ambiguous_record")
    assert cli() == 3


def test_drift_warns_by_default_but_can_break_the_build(
    cli: Any, sandbox_client: Any
) -> None:
    """DRIFT is a warning unless a team opts into failing on it."""
    sandbox_client.post("/admin/perturb/pdf_format_shift")
    assert cli() == 0
    assert cli("--fail-on", "fail,drift,inconclusive") == 2


def test_json_report_is_written_and_parses(cli: Any, tmp_path: Path) -> None:
    target = tmp_path / "report.json"
    cli("--json", str(target))
    payload = json.loads(target.read_text("utf-8"))
    assert payload["verdict"] == "PASS"
    assert payload["reports"][0]["contract_name"] == "invoice_to_po"


def test_junit_xml_is_valid_and_marks_failures(
    cli: Any, sandbox_client: Any, tmp_path: Path
) -> None:
    sandbox_client.post("/admin/perturb/amount_changed")
    target = tmp_path / "junit.xml"
    cli("--junit", str(target))

    root = ET.parse(target).getroot()
    suite = root.find("testsuite")
    assert suite is not None
    assert suite.get("failures") == "2"
    failing = {c.get("name") for c in suite.iter("testcase") if c.find("failure") is not None}
    assert failing == {"amount_match", "amount_persisted"}


def test_github_annotations_anchor_to_the_contract_line(
    cli: Any, sandbox_client: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    """The annotation must land on the line the developer wrote."""
    sandbox_client.post("/admin/perturb/amount_changed")
    cli("--github-annotations")
    output = capsys.readouterr().out

    annotations = [line for line in output.splitlines() if line.startswith("::error")]
    assert annotations
    assert "invoice_to_po.yaml" in annotations[0]
    assert ",line=" in annotations[0]
    assert "amount_match" in annotations[0]

    line_number = int(annotations[0].split(",line=")[1].split("::")[0])
    source = CONTRACT_PATH.read_text("utf-8").splitlines()
    assert "amount_match" in source[line_number - 1]


def test_markdown_summary_carries_the_delta(
    cli: Any, sandbox_client: Any, tmp_path: Path
) -> None:
    sandbox_client.post("/admin/perturb/amount_changed")
    target = tmp_path / "summary.md"
    cli("--summary", str(target))
    text = target.read_text("utf-8")
    assert "**FAIL**" in text
    assert "+133,200.00" in text


def test_an_evidence_bundle_can_be_written_and_verified(cli: Any, tmp_path: Path) -> None:
    from verity_evidence import check_bundle

    bundle = tmp_path / "bundle"
    cli("--bundle", str(bundle))
    manifest = check_bundle(bundle)
    assert manifest.entries


def test_lint_accepts_the_reference_contract() -> None:
    assert main(["lint", str(CONTRACT_PATH)]) == 0


def test_lint_rejects_a_broken_contract(tmp_path: Path) -> None:
    broken = tmp_path / "broken.yaml"
    broken.write_text(
        "kind: OutcomeContract\n"
        "metadata: {name: broken, version: 0.1.0}\n"
        "expected:\n"
        "  - id: a\n"
        "    assert: 'ghost.total > 0'\n",
        encoding="utf-8",
    )
    assert main(["lint", str(broken)]) == 1


def test_eval_runs_a_whole_suite(cli: Any, monkeypatch: pytest.MonkeyPatch,
                                 sandbox_client: Any, tmp_path: Path) -> None:
    from verity_connectors import build_sandbox_registry

    monkeypatch.setattr(
        "verity_cli.main._build_registry",
        lambda args: build_sandbox_registry("http://sandbox", client=sandbox_client),
    )
    code = main([
        "eval", "--suite", str(REPO_ROOT / "examples" / "contracts"),
        "--input", "invoice_number=INV-4471", "--input", "po_number=PO-2211",
        "--evidence-dir", str(tmp_path / "suite"), "--no-color",
    ])
    assert code == 0


def test_unknown_verdict_in_fail_on_is_a_usage_error(cli: Any) -> None:
    assert cli("--fail-on", "nonsense") == 4


def test_malformed_input_is_a_usage_error(tmp_path: Path) -> None:
    assert main([
        "verify", "--contract", str(CONTRACT_PATH), "--live", "--input", "novalue",
        "--evidence-dir", str(tmp_path), "--no-color",
    ]) == 4


def test_no_subcommand_prints_help_and_exits_four() -> None:
    assert main([]) == 4


# ------------------------------------------------- degrading, not collapsing

_HIDE_RUNTIME = """
import sys, builtins
_real = builtins.__import__
def _blocked(name, *a, **k):
    if name == "verity_runtime" or name.startswith("verity_runtime."):
        raise ModuleNotFoundError("No module named 'verity_runtime'", name="verity_runtime")
    return _real(name, *a, **k)
builtins.__import__ = _blocked
for mod in [m for m in sys.modules if m.startswith("verity_runtime")]:
    del sys.modules[mod]
from verity_cli.main import main
sys.exit(main(sys.argv[1:]))
"""


def _without_runtime(*argv: str) -> Any:
    """Run the CLI in a fresh interpreter where verity_runtime cannot be imported.

    A subprocess rather than monkeypatching, because the failure being
    reproduced happens at module import time and patching inside an
    already-imported process would prove the wrong thing.
    """
    import subprocess
    import sys

    return subprocess.run(
        [sys.executable, "-c", _HIDE_RUNTIME, *argv],
        capture_output=True, text=True, cwd=REPO_ROOT, check=False,
    )


def test_a_missing_package_does_not_take_down_the_whole_cli() -> None:
    """A checkout whose editable install predates a package used to be fatal.

    Every command died on an import traceback naming a module the user had
    never heard of -- including `doctor`, whose entire job is to explain what
    is wrong. Losing the diagnostic to the fault it diagnoses is the worst
    possible time to lose it.
    """
    result = _without_runtime("doctor")
    combined = result.stdout + result.stderr

    assert "Traceback" not in combined
    assert "verity_runtime MISSING" in combined
    assert 'pip install -e ".[dev,sandbox,extract]"' in combined
    assert result.returncode == 1


def test_commands_that_do_not_need_the_runtime_still_work_without_it() -> None:
    result = _without_runtime("--help")

    assert result.returncode == 0
    assert "verify" in result.stdout
    assert "lint" in result.stdout
    # ...and the ones that do need it are absent rather than broken.
    assert "dry-run" not in result.stdout
