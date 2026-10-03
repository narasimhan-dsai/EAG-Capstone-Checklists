from __future__ import annotations

import pytest

CONTROL_TOKEN = "test-control-token"
COMPLETION_TOKEN = "test-completion-token"


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch, tmp_path):
    monkeypatch.setenv("CHECKLIST_DATA_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("CHECKLIST_A2A_GRPC_ENABLED", "0")
    monkeypatch.setenv("CHECKLIST_SANDBOX_ROOT", str(tmp_path / "sandbox"))
    # The control plane fails closed. Tests configure a token exactly as a real
    # deployment must; they do not get a bypass, so the gates stay exercised
    # rather than disabled for convenience.
    monkeypatch.setenv("CHECKLIST_CONTROL_TOKEN", CONTROL_TOKEN)
    monkeypatch.setenv("CHECKLIST_COMPLETION_TOKEN", COMPLETION_TOKEN)
    (tmp_path / "sandbox").mkdir()


# An `app_client` fixture (FastAPI TestClient over the running app) lived here
# in S17Code. Re-add it once checklist_agent/main.py exists — this backbone copy
# has no app yet, only the reusable modules under checklist_agent/.
