"""Explicit Gate 0 test allowlist. Never discover deployment-config tests."""
import pathlib
import sys
import unittest

TESTS = (
    "test_backend",
    "test_scheduler",
    "test_legacy_subscriptions",
    "test_legacy_recovery",
    "test_legacy_jobs_downloader",
    "test_legacy_reader_auth",
    "test_job_repository",
    "test_subscription_repository",
    "test_single_container",
    "test_intake",
)


def main():
    here = pathlib.Path(__file__).resolve().parent
    sys.path.insert(0, str(here))
    suite = unittest.defaultTestLoader.loadTestsFromNames(TESTS)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
