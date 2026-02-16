import os

import pytest


class DummyContext:
    def __init__(self):
        self.calls = []

    def run(self, cmd, warn=False):  # signature like invocate's Context
        self.calls.append((cmd, warn))


class TestPublishRelease:
    @pytest.fixture(autouse=True)
    def clear_env(self, monkeypatch):
        for k in ("PYPI_TOKEN", "TESTPYPI_TOKEN"):
            if k in os.environ:
                monkeypatch.delenv(k, raising=False)

    def test_uses_testpypi_when_flag_true(self, monkeypatch):
        from tasks import publish as pub

        monkeypatch.setenv("TESTPYPI_TOKEN", "test-token-123")
        c = DummyContext()

        pub._perform_release(c, use_testpypi=True, do_build=False)

        assert len(c.calls) == 1
        cmd = c.calls[0][0]
        assert "uv publish" in cmd
        assert "test-token-123" in cmd
        assert "--index testpypi" in cmd

    def test_uses_pypi_when_flag_false(self, monkeypatch):
        from tasks import publish as pub

        monkeypatch.setenv("PYPI_TOKEN", "real-token-xyz")
        c = DummyContext()

        pub._perform_release(c, use_testpypi=False, do_build=False)

        assert len(c.calls) == 1
        cmd = c.calls[0][0]
        assert "uv publish" in cmd
        assert "real-token-xyz" in cmd
        assert "--index" not in cmd

    def test_build_runs_before_publish(self, monkeypatch):
        from tasks import publish as pub

        monkeypatch.setenv("PYPI_TOKEN", "tok")
        c = DummyContext()

        pub._perform_release(c, use_testpypi=False, do_build=True)

        assert len(c.calls) == 2
        assert c.calls[0][0].startswith("uv build")
        assert c.calls[1][0].startswith("uv publish")

    def test_missing_token_raises(self, mocker):
        from tasks import publish as pub

        c = DummyContext()
        # Prevent loading tokens from .env during this test
        mocker.patch.object(pub, "_load_env", return_value=None)
        with pytest.raises(RuntimeError):
            pub._perform_release(c, use_testpypi=False, do_build=False)
