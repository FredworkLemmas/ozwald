import threading
import time
from typing import List

import pytest

from orchestration.models import Cache, ServiceInformation, ServiceStatus
from services.container import ContainerService


class FakeActiveServicesCache:
    def __init__(self, cache: Cache):
        self._services = []
        self.set_calls = []

    def get_services(self) -> List[ServiceInformation]:
        return self._services

    def set_services(self, services: List[ServiceInformation]):
        self.set_calls.append((services,))
        self._services = services


class SyncThread(threading.Thread):
    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        super().__init__(daemon=daemon)
        self.target = target
        self.args = args
        self.kwargs = kwargs or {}

    def start(self):
        if self.target:
            self.target(*self.args, **self.kwargs)

    def join(self, timeout=None):
        pass


class DummyProvisioner:
    def __init__(self, cache: Cache):
        self.cache = cache

    def get_cache(self):
        return self.cache


def _si(name: str, status: ServiceStatus | None, profile: str = "default"):
    return ServiceInformation(
        name=name,
        service="test",
        profile=profile,
        status=status,
    )


class TestService(ContainerService):
    service_type = "test"

    def __init__(self, service_info: ServiceInformation):
        from orchestration.provisioner import SystemProvisioner

        self._cache = SystemProvisioner.singleton().get_cache()
        self._service_info = service_info

    def get_container_image(self):
        return "alpine:latest"

    def get_container_name(self):
        return f"service-{self._service_info.name}"

    def get_container_start_command(self, image: str) -> list[str]:
        return [
            "docker",
            "run",
            "--name",
            self.get_container_name(),
            image,
        ]


class TestContainerServiceHealth:
    @pytest.fixture(autouse=True)
    def patch_system(self, monkeypatch):
        import services.container as cont_mod

        monkeypatch.setattr(
            cont_mod, "ActiveServicesCache", FakeActiveServicesCache
        )
        monkeypatch.setattr(cont_mod.threading, "Thread", SyncThread)

        class MockProcess:
            def __init__(self):
                from unittest.mock import MagicMock

                self.stdout = MagicMock()
                self.stdout.readline.return_value = ""
                self.poll = lambda: 0
                self.returncode = 0

        monkeypatch.setattr(
            cont_mod.subprocess, "Popen", lambda *a, **k: MockProcess()
        )

        import orchestration.provisioner as prov_mod

        dummy_cache = Cache(type="memory", parameters={})

        def fake_singleton():
            return DummyProvisioner(dummy_cache)

        monkeypatch.setattr(
            prov_mod.SystemProvisioner,
            "singleton",
            staticmethod(fake_singleton),
        )

    def test_start_waits_for_healthy(self, monkeypatch):
        svc = TestService(_si("svc1", ServiceStatus.STARTING))

        cache = FakeActiveServicesCache(Cache(type="memory"))
        cache._services = [_si("svc1", ServiceStatus.STARTING)]

        import services.container as cont_mod

        monkeypatch.setattr(cont_mod, "ActiveServicesCache", lambda c: cache)

        class CP:
            def __init__(self, returncode=0, stdout="", stderr=""):
                self.returncode = returncode
                self.stdout = stdout
                self.stderr = stderr

        inspect_calls = []

        def fake_run(cmd, capture_output=False, text=False, check=False):
            cmd_str = " ".join(cmd)
            if cmd[:2] == ["docker", "ps"]:
                return CP(0, stdout="")
            if cmd[:2] == ["docker", "run"]:
                return CP(0, stdout="abc123\n")
            if "inspect" in cmd_str and ".Id" in cmd_str:
                return CP(0, stdout="abc123\n")
            if "inspect" in cmd_str and ".State.Status" in cmd_str:
                inspect_calls.append(cmd)
                # First two calls return "starting", third returns "healthy"
                if len(inspect_calls) == 1:
                    return CP(0, stdout="running true starting\n")
                if len(inspect_calls) == 2:
                    return CP(0, stdout="running true starting\n")
                return CP(0, stdout="running true healthy\n")
            raise AssertionError(f"Unexpected command: {cmd}")

        monkeypatch.setattr(cont_mod.subprocess, "run", fake_run)
        # Patch time.sleep to speed up test
        monkeypatch.setattr(time, "sleep", lambda x: None)

        svc.start()

        # Should have called inspect at least 3 times if it waits for healthy
        assert len(inspect_calls) >= 3
        assert cache.get_services()[0].status == ServiceStatus.AVAILABLE

    def test_start_running_no_healthcheck(self, monkeypatch):
        svc = TestService(_si("svc1", ServiceStatus.STARTING))

        cache = FakeActiveServicesCache(Cache(type="memory"))
        cache._services = [_si("svc1", ServiceStatus.STARTING)]

        import services.container as cont_mod

        monkeypatch.setattr(cont_mod, "ActiveServicesCache", lambda c: cache)

        class CP:
            def __init__(self, returncode=0, stdout="", stderr=""):
                self.returncode = returncode
                self.stdout = stdout
                self.stderr = stderr

        def fake_run(cmd, capture_output=False, text=False, check=False):
            cmd_str = " ".join(cmd)
            if cmd[:2] == ["docker", "ps"]:
                return CP(0, stdout="")
            if cmd[:2] == ["docker", "run"]:
                return CP(0, stdout="abc123\n")
            if "inspect" in cmd_str and ".Id" in cmd_str:
                return CP(0, stdout="abc123\n")
            if "inspect" in cmd_str and ".State.Status" in cmd_str:
                # Returns "true none" because no healthcheck defined
                return CP(0, stdout="running true none\n")
            raise AssertionError(f"Unexpected command: {cmd}")

        monkeypatch.setattr(cont_mod.subprocess, "run", fake_run)
        monkeypatch.setattr(time, "sleep", lambda x: None)

        svc.start()

        assert cache.get_services()[0].status == ServiceStatus.AVAILABLE
