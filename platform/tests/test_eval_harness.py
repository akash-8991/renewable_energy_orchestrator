"""The eval harness itself must behave correctly, independent of whether
the underlying model actually reasons well: structural cases must run and
be scored under the mock gateway (no API key needed to smoke-test the
harness), and behavioral cases — which mock can never genuinely pass —
must be reported as skipped, never as a misleading failure."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "evaluation"))
sys.path.insert(0, str(Path(__file__).parent.parent / "agent"))  # eval_harness.py imports agents.* / context flat, same as agent-worker's own PYTHONPATH

from eval_harness import EVAL_CASES, run_eval_suite  # noqa: E402
from reo_common.model_gateway import MockModelGateway  # noqa: E402


def test_eval_suite_runs_under_mock_gateway():
    summary = run_eval_suite(MockModelGateway())
    assert summary["model_provider"] == "mock"
    assert summary["total_cases"] == sum(1 for c in EVAL_CASES if c.tier == "structural")


def test_behavioral_cases_are_skipped_not_failed_under_mock():
    summary = run_eval_suite(MockModelGateway())
    behavioral_outcomes = {r["case"]: r["outcome"] for r in summary["results"] if r["tier"] == "behavioral"}
    assert behavioral_outcomes  # the suite actually has behavioral cases
    assert all(outcome == "skipped" for outcome in behavioral_outcomes.values())


def test_structural_cases_pass_under_mock_gateway():
    summary = run_eval_suite(MockModelGateway())
    structural = [r for r in summary["results"] if r["tier"] == "structural"]
    assert structural
    assert all(r["outcome"] == "passed" for r in structural)
    assert summary["passed_cases"] == len(structural)


def test_every_case_name_is_unique():
    names = [c.name for c in EVAL_CASES]
    assert len(names) == len(set(names))
