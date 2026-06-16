"""Cooperative shutdown flag (``bubo.signals``).

The poll loop checks :func:`shutdown_requested` between MRs and exits cleanly
when a SIGTERM/SIGINT flips the flag. These tests drive that flag without
actually sending a signal to the test process: ``install_signal_handlers``
registers a handler, and we invoke the registered handler object directly so
the assertion is deterministic and never races the OS signal machinery.
"""

from __future__ import annotations

import signal

import pytest

from bubo import signals


@pytest.fixture(autouse=True)
def _restore_handlers() -> None:
    """Snapshot and restore SIGTERM/SIGINT dispositions + the flag.

    ``install_signal_handlers`` mutates process-global state; without this a
    test that installs handlers would leak them into the rest of the suite.
    """
    saved = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
    signals.reset_for_tests()
    yield
    for sig, handler in saved.items():
        signal.signal(sig, handler)
    signals.reset_for_tests()


def test_flag_starts_false() -> None:
    assert signals.shutdown_requested() is False


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
def test_handler_sets_flag_and_is_idempotent_per_signal(sig: signal.Signals) -> None:
    signals.install_signal_handlers()
    handler = signal.getsignal(sig)
    assert callable(handler)

    assert signals.shutdown_requested() is False
    handler(int(sig), None)  # simulate the OS delivering the signal
    assert signals.shutdown_requested() is True


def test_install_is_idempotent() -> None:
    # Calling twice must not raise and must leave a working handler in place.
    signals.install_signal_handlers()
    signals.install_signal_handlers()
    handler = signal.getsignal(signal.SIGTERM)
    assert callable(handler)
    handler(int(signal.SIGTERM), None)
    assert signals.shutdown_requested() is True


def test_reset_for_tests_clears_flag() -> None:
    signals.install_signal_handlers()
    signal.getsignal(signal.SIGINT)(int(signal.SIGINT), None)
    assert signals.shutdown_requested() is True

    signals.reset_for_tests()
    assert signals.shutdown_requested() is False
