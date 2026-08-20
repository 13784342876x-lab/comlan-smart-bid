from __future__ import annotations

import shutil
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parent
PYTEST_ARTIFACT_NAMES = (".pytest_cache",)
PYTEST_BASETEMP = BACKEND_ROOT / ".pytest_tmp_runtime"


def _remove_pytest_artifact(path: Path) -> None:
    resolved = path.resolve()
    if resolved.parent != BACKEND_ROOT:
        return
    if not (resolved.name == ".pytest_cache" or resolved.name.startswith(".pytest_tmp")):
        return
    shutil.rmtree(resolved, ignore_errors=True)


def pytest_configure(config) -> None:  # type: ignore[no-untyped-def]
    if config.option.basetemp is None:
        config.option.basetemp = str(PYTEST_BASETEMP)


def pytest_sessionfinish(session, exitstatus) -> None:  # type: ignore[no-untyped-def]
    for name in PYTEST_ARTIFACT_NAMES:
        _remove_pytest_artifact(BACKEND_ROOT / name)
    for path in BACKEND_ROOT.glob(".pytest_tmp*"):
        _remove_pytest_artifact(path)
