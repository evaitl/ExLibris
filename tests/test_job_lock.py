"""Tests for library job lock."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from exlibris.job_lock import LibraryJobLockedError, library_job_lock


def test_library_job_lock_blocks_second_holder() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        lock_path = Path(tmp) / "library.lock"
        with library_job_lock(lock_path, job_name="test job"):
            with pytest.raises(LibraryJobLockedError):
                with library_job_lock(lock_path, job_name="test job"):
                    pass


def test_library_job_lock_skips_when_env_set() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        lock_path = Path(tmp) / "library.lock"
        os.environ["EXLIBRIS_JOB_LOCK_HELD"] = "1"
        try:
            with library_job_lock(lock_path, job_name="test job"):
                with library_job_lock(lock_path, job_name="test job"):
                    pass
        finally:
            os.environ.pop("EXLIBRIS_JOB_LOCK_HELD", None)
