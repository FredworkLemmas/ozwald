from typing import List

import pytest

from orchestration.models import Cache, ServiceInformation, ServiceStatus
from orchestration.service import BaseProvisionableService
from services.container import ContainerService


class FakeActiveServicesCache:
    """A simple in-memory stand-in for ActiveServicesCache used by tests."""

    def __init__(self, cache: Cache):
        self.cache = cache
        self._services: List[ServiceInformation] = []
        self.set_calls: List[List[ServiceInformation]] = []

    def get_services(self) -> List[ServiceInformation]:
        # Return copies to avoid accidental mutation by caller
        return [ServiceInformation(**s.model_dump()) for s in self._services]

    def set_services(self, services: List[ServiceInformation]) -> None:
        # Record call and replace the internal list with copies
        self.set_calls.append(services)
        self._services = [
            ServiceInformation(**s.model_dump()) for s in services
        ]


class SyncThread:
    """Drop-in replacement for threading.Thread that runs target
    synchronously.
    """

    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}
        self.daemon = daemon

    def start(self):
        if self._target:
            self._target(*self._args, **self._kwargs)

    def join(self, timeout=None):
        # Already executed synchronously
        return


class DummyProvisioner:
    def __init__(self, cache: Cache):
        self._cache = cache

    def get_cache(self):
        return self._cache


class TestService(ContainerService):
    __test__ = False  # prevent pytest from treating this as a test container
    service_type = "test"
    container_image: str = "alpine:latest"

    # Override to avoid Pydantic BaseModel initialization on Service
    def __init__(self, service_info: ServiceInformation):
        from orchestration.provisioner import SystemProvisioner

        # do not call super().__init__()
        self._cache = SystemProvisioner.singleton().get_cache()
        self._service_info = service_info

    # Avoid Pydantic attribute access; return a literal image
    def get_container_image(self):
        return "alpine:latest"

    def get_container_start_command(self, image: str) -> list[str]:
        # Minimal command that avoids accessing base class attributes
        return [
            "docker",
            "run",
            "-d",
            "--name",
            f"service-{self._service_info.name}",
            image,
        ]


@pytest.fixture(autouse=True)
def patch_system(monkeypatch):
    """Patch out external systems that BaseProvisionableService touches."""
    # Ensure we never talk to real Redis-backed cache
    import orchestration.service as svc_mod
    import services.container as cont_mod

    # Patch in container module; Base no longer uses ActiveServicesCache
    monkeypatch.setattr(
        cont_mod,
        "ActiveServicesCache",
        FakeActiveServicesCache,
    )

    # Short-circuit GPU detection for container module
    monkeypatch.setattr(cont_mod.HostResources, "installed_gpu_drivers", list)

    # Make threads synchronous for deterministic tests (container module)
    monkeypatch.setattr(cont_mod.threading, "Thread", SyncThread)

    # Provide a dummy provisioner singleton with a benign Cache
    import orchestration.provisioner as prov_mod

    dummy_cache = Cache(type="memory", parameters={})

    def fake_singleton():
        return DummyProvisioner(dummy_cache)

    monkeypatch.setattr(
        prov_mod.SystemProvisioner,
        "singleton",
        staticmethod(fake_singleton),
    )

    return {
        "svc_mod": svc_mod,
        "prov_mod": prov_mod,
    }


def _si(
    name: str,
    status: ServiceStatus | None,
    profile: str = "default",
) -> ServiceInformation:
    return ServiceInformation(
        name=name,
        service="test",
        profile=profile,
        status=status,
    )


class TestBaseProvisionableServiceLifecycle:
    def test_start_raises_when_service_not_in_cache(self, monkeypatch):
        svc = TestService(_si("svc1", ServiceStatus.STARTING))

        # The FakeActiveServicesCache starts empty; expect RuntimeError
        with pytest.raises(RuntimeError) as ei:
            svc.start()
        assert "not found" in str(ei.value)

    def test_start_raises_when_status_not_starting(self, monkeypatch):
        svc = TestService(_si("svc1", ServiceStatus.AVAILABLE))

        # Seed cache with AVAILABLE instead of STARTING
        cache = FakeActiveServicesCache(Cache(type="memory"))
        cache._services = [_si("svc1", ServiceStatus.AVAILABLE)]

        # Inject our pre-seeded cache instance for this service object by
        # replacing
        # the ActiveServicesCache initializer to return our instance.
        import services.container as cont_mod

        monkeypatch.setattr(cont_mod, "ActiveServicesCache", lambda c: cache)

        with pytest.raises(RuntimeError) as ei:
            svc.start()
        assert "expected ServiceStatus.STARTING" in str(ei.value)

    def test_start_success_updates_cache_and_sets_container_id(
        self,
        monkeypatch,
    ):
        svc = TestService(_si("svc1", ServiceStatus.STARTING))

        # Pre-seed active services with STARTING entry
        cache = FakeActiveServicesCache(Cache(type="memory"))
        cache._services = [_si("svc1", ServiceStatus.STARTING)]

        import services.container as cont_mod

        monkeypatch.setattr(cont_mod, "ActiveServicesCache", lambda c: cache)

        # Mock subprocess.run behavior for docker run and inspect
        class CP:
            def __init__(self, returncode=0, stdout="", stderr=""):
                self.returncode = returncode
                self.stdout = stdout
                self.stderr = stderr

        def fake_run(cmd, capture_output=False, text=False, check=False):
            # Distinguish by first two args
            if cmd[:2] == ["docker", "run"]:
                # return container id
                return CP(0, stdout="abc123\n")
            if cmd[:2] == ["docker", "inspect"]:
                # indicate running
                return CP(0, stdout="true\n")
            raise AssertionError(f"Unexpected command: {cmd}")

        import services.container as cont_mod

        monkeypatch.setattr(cont_mod.subprocess, "run", fake_run)

        # Act
        svc.start()

        # After synchronous thread execution, the cache should contain the
        # service as AVAILABLE
        services = cache.get_services()
        assert len(services) == 1
        s = services[0]
        assert s.status == ServiceStatus.AVAILABLE
        assert s.info is not None
        assert s.info.get("container_id") == "abc123"
        assert s.info.get("start_completed") is not None

    def test_stop_raises_when_service_not_in_cache(self, monkeypatch):
        svc = TestService(_si("svc1", ServiceStatus.STOPPING))
        # No services in cache -> error
        with pytest.raises(RuntimeError) as ei:
            svc.stop()
        assert "not found" in str(ei.value)

    def test_stop_raises_when_status_not_stopping(self, monkeypatch):
        svc = TestService(_si("svc1", ServiceStatus.AVAILABLE))

        cache = FakeActiveServicesCache(Cache(type="memory"))
        cache._services = [_si("svc1", ServiceStatus.AVAILABLE)]

        import services.container as cont_mod

        monkeypatch.setattr(cont_mod, "ActiveServicesCache", lambda c: cache)

        with pytest.raises(RuntimeError) as ei:
            svc.stop()
        assert ["expected ", "ServiceStatus.STOPPING}"][0] in str(ei.value)

    def test_stop_success_stops_container_and_removes_from_cache(
        self,
        monkeypatch,
    ):
        svc = TestService(_si("svc1", ServiceStatus.STOPPING))

        # Seed cache with one running service, including a container_id
        si = _si("svc1", ServiceStatus.STOPPING)
        si.info = {"container_id": "abc123"}
        cache = FakeActiveServicesCache(Cache(type="memory"))
        cache._services = [si]

        import services.container as cont_mod

        monkeypatch.setattr(cont_mod, "ActiveServicesCache", lambda c: cache)

        class CP:
            def __init__(self, returncode=0, stdout="", stderr=""):
                self.returncode = returncode
                self.stdout = stdout
                self.stderr = stderr

        def fake_run(cmd, capture_output=False, text=False, check=False):
            if cmd[:2] == ["docker", "stop"]:
                return CP(0, stdout="abc123\n")
            if cmd[:2] == ["docker", "inspect"]:
                # Return false -> container is not running
                return CP(0, stdout="false\n")
            if cmd[:2] == ["docker", "rm"]:
                return CP(0, stdout="")
            raise AssertionError(f"Unexpected command: {cmd}")

        import services.container as cont_mod

        monkeypatch.setattr(cont_mod.subprocess, "run", fake_run)

        # Act
        svc.stop()

        # The service should be removed from the cache
        services = cache.get_services()
        assert services == []


class TestServiceRegistry:
    def test_build_service_registry_discovers_services_package(
        self,
        monkeypatch,
    ):
        # Reset cache
        BaseProvisionableService._service_registry = None

        # Build the registry
        registry = BaseProvisionableService._build_service_registry()

        # SimpleTestOneService is defined in services.testing with
        # service_type 'simple_test_one'
        assert "simple_test_one" in registry
        cls = registry["simple_test_one"]
        assert issubclass(cls, BaseProvisionableService)

    def test_lookup_service_uses_cached_registry(self, monkeypatch):
        # Ensure first, a real registry is built
        BaseProvisionableService._service_registry = None
        BaseProvisionableService._lookup_service("simple_test_one")

        # Now, monkeypatch the builder to raise if called again
        call_count = {"n": 0}

        def boom():
            call_count["n"] += 1
            raise AssertionError(
                "_build_service_registry should not be called when "
                "cache exists",
            )

        monkeypatch.setattr(
            BaseProvisionableService,
            "_build_service_registry",
            classmethod(lambda cls: boom()),
        )

        # Perform another lookup; should use cached dict and not call builder
        found = BaseProvisionableService._lookup_service("simple_test_one")
        assert found is not None
        assert call_count["n"] == 0

    def test_build_service_registry_includes_dynamically_added_module(
        self,
        monkeypatch,
    ):
        # Create a dynamic module under services namespace
        import sys as _sys
        import types as _types

        module_name = "services._dyn_test_mod"
        dyn_mod = _types.ModuleType(module_name)

        # Define a subclass with a unique service_type
        class DynService(BaseProvisionableService):
            service_type = "dyn_service_type_xyz"

        # Make it look like it's defined in the dynamic services module
        DynService.__module__ = module_name

        dyn_mod.DynService = DynService
        _sys.modules[module_name] = dyn_mod

        # Reset registry and build
        BaseProvisionableService._service_registry = None
        registry = BaseProvisionableService._build_service_registry()

        assert "dyn_service_type_xyz" in registry
        assert registry["dyn_service_type_xyz"] is DynService


class TestEffectiveConfigResolution:
    def _build_service_def(self):
        from orchestration.models import (
            ServiceDefinition,
            ServiceDefinitionProfile,
            ServiceDefinitionVariety,
        )

        # Base service definition
        svc = ServiceDefinition(
            service_name="test",
            type="container",
            description="test svc",
            image="svc-img",
            depends_on=["svcdep"],
            command=["svc-cmd"],
            entrypoint=["svc-entry"],
            env_file=["svc.env"],
            environment={"A": "svc", "X": "svc"},
        )

        # Varieties
        svc.varieties = {
            "nvidia": ServiceDefinitionVariety(
                image="var-img",
                depends_on=["vardep"],
                command=["var-cmd"],
                entrypoint=["var-entry"],
                env_file=["var.env"],
                environment={"A": "var", "V": "var"},
            ),
        }

        # Profiles (now a dict of profiles)
        svc.profiles = {
            "gpu": ServiceDefinitionProfile(
                name="gpu",
                description="",
                image="prof-img",
                depends_on=["profdep"],
                command=["prof-cmd"],
                entrypoint=["prof-entry"],
                env_file=["prof.env"],
                environment={"A": "prof", "P": "prof"},
            ),
        }

        return svc

    @pytest.fixture
    def mock_reader(self, monkeypatch):
        # Fake SystemConfigReader.singleton() -> object with get_service_by_name

        from config import reader as reader_mod

        class DummyReader:
            def __init__(self, svc_def):
                self._svc = svc_def

            def get_service_by_name(self, name: str):
                return self._svc

        svc_def = self._build_service_def()
        dummy = DummyReader(svc_def)
        monkeypatch.setattr(
            reader_mod.SystemConfigReader,
            "singleton",
            classmethod(lambda cls: dummy),
        )
        # Ensure required env var for BaseProvisionableService init
        monkeypatch.setenv("OZWALD_HOST", "localhost")
        return svc_def

    class _Svc(ContainerService):
        __test__ = False

        def __init__(self, si):
            # Call ContainerService initializer to properly set up
            # the Pydantic model and base state
            ContainerService.__init__(self, si)

        def get_container_start_command(self, image: str) -> list[str]:
            return ["docker", "run", image]

        def get_variety(self):
            # Delegate to base implementation which checks both
            # ServiceInformation and the runtime Service.parameters
            return super().get_variety()

    def test_effective_service_only(self, mock_reader):
        si = ServiceInformation(
            name="n1",
            service="test",
            profile=None,
            status=None,
        )
        svc = type(self)._Svc(si)

        # No variety, no profile -> base service values
        assert svc.get_container_image() == "svc-img"
        assert svc.get_container_environment()["A"] == "svc"
        assert svc.get_effective_depends_on() == ["svcdep"]
        assert svc.get_effective_env_file() == ["svc.env"]
        assert svc.get_effective_command() == ["svc-cmd"]
        assert svc.get_effective_entrypoint() == ["svc-entry"]

    def test_effective_with_variety_only(self, mock_reader):
        # Select variety via ServiceInformation (runtime parameters are
        # being removed)
        si = ServiceInformation(
            name="n1",
            service="test",
            profile=None,
            status=None,
            variety="nvidia",
        )
        svc = type(self)._Svc(si)

        # Variety overrides service where provided
        assert svc.get_container_image() == "var-img"
        env = svc.get_container_environment()
        # env merge: service < variety
        assert env["A"] == "var"
        assert env["V"] == "var"
        assert env["X"] == "svc"
        assert svc.get_effective_depends_on() == ["vardep"]
        assert svc.get_effective_env_file() == ["var.env"]
        assert svc.get_effective_command() == ["var-cmd"]
        assert svc.get_effective_entrypoint() == ["var-entry"]

    def test_effective_with_profile_only(self, mock_reader):
        si = ServiceInformation(
            name="n1",
            service="test",
            profile="gpu",
            status=None,
        )
        svc = type(self)._Svc(si)

        # Profile overrides service
        assert svc.get_container_image() == "prof-img"
        env = svc.get_container_environment()
        # env merge: service < profile
        assert env["A"] == "prof"
        assert env["P"] == "prof"
        assert env["X"] == "svc"
        assert svc.get_effective_depends_on() == ["profdep"]
        assert svc.get_effective_env_file() == ["prof.env"]
        assert svc.get_effective_command() == ["prof-cmd"]
        assert svc.get_effective_entrypoint() == ["prof-entry"]

    def test_effective_with_profile_and_variety_profile_wins(self, mock_reader):
        si = ServiceInformation(
            name="n1",
            service="test",
            profile="gpu",
            status=None,
            variety="nvidia",
        )
        svc = type(self)._Svc(si)

        # Image: profile > variety > service
        assert svc.get_container_image() == "prof-img"
        env = svc.get_container_environment()
        # env merge: service < variety < profile
        assert env["A"] == "prof"
        assert env["P"] == "prof"
        assert env["V"] == "var"
        assert env["X"] == "svc"
        # other attributes precedence
        assert svc.get_effective_depends_on() == ["profdep"]
        assert svc.get_effective_env_file() == ["prof.env"]
        assert svc.get_effective_command() == ["prof-cmd"]
        assert svc.get_effective_entrypoint() == ["prof-entry"]
