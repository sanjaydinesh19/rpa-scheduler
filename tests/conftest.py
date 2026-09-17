"""Session-scoped fixtures: one isolated Mock HMS per test run.

The server runs as a subprocess with a temp SQLite DB and demo endpoints
enabled, so tests can plant conflicts and reseed between cases without touching
the developer's mock_hms/hms.db.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tests.bot_engine import HmsClient  # noqa: E402


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def hms_base_url(tmp_path_factory) -> str:
    port = _free_port()
    db_path = tmp_path_factory.mktemp("hms") / "test.db"
    env = os.environ.copy()
    env.update({
        "DATABASE_URL": f"sqlite:///{db_path}",
        "REQUIRE_API_KEY": "false",
        "ALLOW_DEMO_ENDPOINTS": "true",
        "QUEUE_PUSH_ENABLED": "false",
        "PORT": str(port),
        "HOST": "127.0.0.1",
        "FLASK_DEBUG": "false",
        "LOG_LEVEL": "WARNING",
        "PYTHONPATH": str(REPO_ROOT),
    })

    proc = subprocess.Popen(
        [sys.executable, "-m", "mock_hms.app"],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )

    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 45
    last_err = None
    while time.time() < deadline:
        try:
            r = requests.get(f"{base}/api/v1/health", timeout=2)
            if r.status_code == 200:
                break
        except Exception as exc:
            last_err = exc
        time.sleep(0.3)
    else:
        proc.terminate()
        raise RuntimeError(f"HMS did not start within 45s: {last_err}")

    # Seed via the demo endpoint so tests get the reference dataset.
    r = requests.post(f"{base}/api/v1/admin/reseed", timeout=90)
    r.raise_for_status()

    yield base

    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture
def hms(hms_base_url) -> HmsClient:
    """Fresh state per test: reseed before each so tests don't cross-contaminate."""
    r = requests.post(f"{hms_base_url}/api/v1/admin/reseed", timeout=90)
    r.raise_for_status()
    return HmsClient(hms_base_url)
