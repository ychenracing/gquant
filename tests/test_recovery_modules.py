"""Recovery-only contract for the lost account-continuation modules."""

from __future__ import annotations

import importlib.util


REQUIRED_MODULES = (
    "gquant.application.operations",
    "gquant.application.runtime",
    "gquant.application.state",
    "gquant.execution.reconciliation",
    "gquant.portfolio.accounting",
    "gquant.research.robustness",
)


def test_account_continuation_modules_exist() -> None:
    missing = [name for name in REQUIRED_MODULES if importlib.util.find_spec(name) is None]
    assert missing == []
