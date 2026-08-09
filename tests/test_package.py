"""Smoke tests confirming the package is importable and correctly installed."""

import kicker_manager_analysis


def test_package_exposes_version() -> None:
    """The installed package reports a version string."""
    assert kicker_manager_analysis.__version__
