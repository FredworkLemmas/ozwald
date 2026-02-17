import os
import types
from datetime import datetime, timedelta

import pytest

from orchestration.models import Cache, ServiceInformation, ServiceStatus


class StopLoop(Exception):
    pass


class FakeActiveServicesCache:
    """A simple in-memory stand-in for ActiveServicesCache used by tests.

    It records calls to set_services and returns deep-copied ServiceInformation
    instances from get_services so tests can assert on persisted results.
    """

    def __init__(self, cache: Cache):
        self.cache = cache
        self._services: list[ServiceInformation] = []
        self.set_calls: list[list[ServiceInformation]] = []
        # Optional hook: raise once, then succeed (used by retry test)
        self.raise_write_collision_once = False
        self._raised_once = False

    def get_services(self) -> list[ServiceInformation]:
        return [ServiceInformation(**s.model_dump()) for s in self._services]

    def set_services(self, services: list[ServiceInformation]) -> None:
        # Simulate optional one-time collision for retry path
        from util.active_services_cache import WriteCollision

        if self.raise_write_collision_once and not self._raised_once:
            self._raised_once = True
            raise WriteCollision("simulated collision")

        # Record call and store deep copies
        self.set_calls.append([
            ServiceInformation(**s.model_dump()) for s in services
        ])
        self._services = [
            ServiceInformation(**s.model_dump()) for s in services
        ]


def _svc_info(
    name: str,
    status: ServiceStatus,
    service_name: str = "svcdef",
    profile: str = "default",
) -> ServiceInformation:
    return ServiceInformation(
        name=name,
        service=service_name,
        profile=profile,
        status=status,
        info={},
    )


@pytest.fixture
def provisioner_env(monkeypatch, tmp_path):
    """Patch orchestration.provisioner dependencies for isolated daemon
    tests.
    """
    import orchestration.provisioner as prov_mod
    from orchestration.provisioner import SystemProvisioner

    # Set mandatory footprint data env var
    footprint_file = tmp_path / "footprints.yml"
    monkeypatch.setenv("OZWALD_FOOTPRINT_DATA", str(footprint_file))

    # Replace the ActiveServicesCache used by SystemProvisioner with our fake
    monkeypatch.setattr(
        prov_mod,
        "ActiveServicesCache",
        FakeActiveServicesCache,
    )

    # Mock ClassCRegistry to avoid calling SystemProvisioner.singleton()
    monkeypatch.setattr(
        "util.class_c_registry.ClassCRegistry.singleton",
        lambda: None,
    )

    # Install a tiny sleep at the end of loop that aborts the daemon after
    # one pass
    def sleep_patch(seconds: float):
        # Only abort on the bottom-of-loop sleep which uses
        # BACKEND_DAEMON_SLEEP_TIME
        if seconds == prov_mod.BACKEND_DAEMON_SLEEP_TIME:
            raise StopLoop
        # For other sleeps (e.g., retry 0.5s), do nothing to keep tests fast

    monkeypatch.setattr(prov_mod.time, "sleep", sleep_patch)

    # Provide a minimal config_reader stub that returns a service
    # definition with a 'type'
    class StubServiceDef:
        def __init__(self, type_value: str):
            # Mimic Enum-like object with .value
            self.type = types.SimpleNamespace(value=type_value)

    class StubConfigReader:
        def __init__(self, type_value: str = "dummy-type"):
            self._type_value = type_value
            self.services = []
            self.persistent_services = []
            self.provisioners = []
            self.defined_networks = []

        def get_service_by_name(self, name: str, realm: str):
            return StubServiceDef(self._type_value)

        def get_effective_service_definition(
            self, service, profile, variety, realm=None
        ):
            from orchestration.models import EffectiveServiceDefinition

            return EffectiveServiceDefinition(
                image="dummy-image",
                properties={"resolved-prop": "val"},
                environment={},
            )

    cache = Cache(type="memory", parameters={})
    config_reader = StubConfigReader()

    # Patch SystemConfigReader.singleton to return our stub
    import config.reader as reader_mod

    monkeypatch.setattr(
        reader_mod.SystemConfigReader,
        "singleton",
        staticmethod(lambda: config_reader),
    )

    prov = SystemProvisioner(config_reader=config_reader, cache=cache)

    # Expose internals for tests to seed/inspect cache
    fake_cache: FakeActiveServicesCache = prov._active_services_cache  # type: ignore[attr-defined]

    # Mock out service initialization to avoid
    # side effects (Docker, Redis singleton calls)
    monkeypatch.setattr(prov, "_init_services", lambda: None)
    monkeypatch.setattr(prov, "_init_networks", lambda: None)
    monkeypatch.setattr(prov, "_deinit_services", lambda: None)
    monkeypatch.setattr(prov, "_init_persistent_services", lambda: None)
    monkeypatch.setattr(prov, "_shutdown_persistent_services", lambda: None)

    return prov_mod, prov, fake_cache


def test_daemon_start_flow_sets_timestamps_and_persists(
    monkeypatch,
    provisioner_env,
):
    prov_mod, prov, fake_cache = provisioner_env

    # Prepare one active service in STARTING state
    si = _svc_info("svc1", ServiceStatus.STARTING)
    fake_cache._services = [si]

    # Dummy service implementation returned by lookup that asserts
    # initiation precondition
    start_called = {"count": 0}

    class DummyService:
        def __init__(self, service_info: ServiceInformation):
            self.service_info = service_info

        def start(self):
            # The provisioner should have set 'start_initiated' before
            # calling start()
            assert "start_initiated" in self.service_info.info
            start_called["count"] += 1
            # Mark as completed and available so the loop persists it
            self.service_info.status = ServiceStatus.AVAILABLE
            self.service_info.info["start_completed"] = (
                datetime.now().isoformat()
            )

    # Stub the registry lookup to return our dummy
    lookup_args = {"seen": []}

    def fake_lookup(service_type: str):
        lookup_args["seen"].append(service_type)
        return DummyService

    monkeypatch.setattr(
        prov_mod.BaseProvisionableService,
        "_lookup_service",
        staticmethod(fake_lookup),
    )

    # Run one loop iteration
    with pytest.raises(StopLoop):
        prov.run_backend_daemon()

    # One start was invoked
    assert start_called["count"] == 1

    # Cache persisted at least once with updated timestamps
    assert fake_cache.set_calls, "expected cache write"
    persisted = fake_cache.set_calls[-1][0]
    assert "start_initiated" in persisted.info
    assert "start_completed" in persisted.info

    # Lookup should have been called with our stub type value
    assert lookup_args["seen"], "lookup was not called"
    assert lookup_args["seen"][0] == "dummy-type"


def test_daemon_ignores_duplicate_start_within_timeout(
    monkeypatch,
    provisioner_env,
):
    prov_mod, prov, fake_cache = provisioner_env

    # Prepare service with a recent 'start_initiated'
    recent = datetime.now() - timedelta(seconds=1)
    si = _svc_info("svc2", ServiceStatus.STARTING)
    si.info["start_initiated"] = recent.isoformat()
    fake_cache._services = [si]

    # Make lookup raise if called, to ensure it's not invoked
    def fail_lookup(_: str):
        raise AssertionError(
            "lookup should not be called for duplicate start within timeout",
        )

    monkeypatch.setattr(
        prov_mod.BaseProvisionableService,
        "_lookup_service",
        staticmethod(fail_lookup),
    )

    with pytest.raises(StopLoop):
        prov.run_backend_daemon()

    # No writes expected since nothing changed
    assert not fake_cache.set_calls, (
        "cache should not be written for duplicate start"
    )


def test_daemon_stop_flow_sets_timestamps_and_persists(
    monkeypatch,
    provisioner_env,
):
    prov_mod, prov, fake_cache = provisioner_env

    si = _svc_info("svc3", ServiceStatus.STOPPING)
    fake_cache._services = [si]

    stop_called = {"count": 0}

    class DummyService:
        def __init__(self, service_info: ServiceInformation):
            self.service_info = service_info

        def stop(self):
            # Provisioner should set 'stop_initiated' before calling stop()
            assert "stop_initiated" in self.service_info.info
            stop_called["count"] += 1

    monkeypatch.setattr(
        prov_mod.BaseProvisionableService,
        "_lookup_service",
        staticmethod(lambda _: DummyService),
    )

    with pytest.raises(StopLoop):
        prov.run_backend_daemon()

    assert stop_called["count"] == 1
    assert fake_cache.set_calls, "expected cache write"
    # With current semantics, stopped service_definitions are removed from
    # cache. The final persisted list should no longer include the service.
    persisted_list = fake_cache.set_calls[-1]
    assert isinstance(persisted_list, list)
    assert persisted_list == []


def test_daemon_ignores_duplicate_stop_within_timeout(
    monkeypatch,
    provisioner_env,
):
    prov_mod, prov, fake_cache = provisioner_env

    recent = datetime.now() - timedelta(seconds=1)
    si = _svc_info("svc4", ServiceStatus.STOPPING)
    si.info["stop_initiated"] = recent.isoformat()
    fake_cache._services = [si]

    def fail_lookup(_: str):
        raise AssertionError(
            "lookup should not be called for duplicate stop within timeout",
        )

    monkeypatch.setattr(
        prov_mod.BaseProvisionableService,
        "_lookup_service",
        staticmethod(fail_lookup),
    )

    with pytest.raises(StopLoop):
        prov.run_backend_daemon()

    assert not fake_cache.set_calls, (
        "cache should not be written for duplicate stop"
    )


def test_daemon_persists_with_retry_on_write_collision(
    monkeypatch,
    provisioner_env,
):
    prov_mod, prov, fake_cache = provisioner_env

    # Seed service that will transition (STARTING)
    si = _svc_info("svc5", ServiceStatus.STARTING)
    fake_cache._services = [si]

    # Arrange to raise a WriteCollision once, then succeed
    fake_cache.raise_write_collision_once = True

    class DummyService:
        def __init__(self, service_info: ServiceInformation):
            self.service_info = service_info

        def start(self):
            return

    monkeypatch.setattr(
        prov_mod.BaseProvisionableService,
        "_lookup_service",
        staticmethod(lambda _: DummyService),
    )

    with pytest.raises(StopLoop):
        prov.run_backend_daemon()

    # Expect at least two attempts: first raises, second succeeds
    assert len(fake_cache.set_calls) >= 1, (
        "final successful write should be recorded"
    )
    # The one-time collision does not record a set_call; only the success
    # is recorded by our fake


class TestUpdateServicesBehaviorEmptyList:
    def test_empty_list_marks_all_active_as_stopping_and_persists(
        self,
        provisioner_env,
    ):
        _prov_mod, prov, fake_cache = provisioner_env

        # Seed two AVAILABLE service_definitions
        a = _svc_info("svc-a", ServiceStatus.AVAILABLE)
        b = _svc_info("svc-b", ServiceStatus.AVAILABLE)
        fake_cache._services = [a, b]

        ok = prov.update_active_services([])
        assert ok
        assert fake_cache.set_calls, "expected cache write"
        persisted: list[ServiceInformation] = fake_cache.set_calls[-1]
        assert len(persisted) == 2
        assert all(s.status == ServiceStatus.STOPPING for s in persisted)

    def test_empty_list_with_no_active_services_persists_empty_list(
        self,
        provisioner_env,
    ):
        _prov_mod, prov, fake_cache = provisioner_env

        fake_cache._services = []
        ok = prov.update_active_services([])
        assert ok
        assert fake_cache.set_calls
        assert fake_cache.set_calls[-1] == []


class TestRunBackendDaemonChecks:
    def test_run_backend_daemon_missing_env(self, provisioner_env, mocker):
        prov_mod, prov, _ = provisioner_env
        mocker.patch.dict(os.environ, {}, clear=True)
        mock_logger = mocker.patch.object(prov_mod, "logger")

        prov.run_backend_daemon()

        mock_logger.error.assert_any_call(
            "OZWALD_FOOTPRINT_DATA environment variable is not defined; "
            "backend daemon cannot run",
        )

    def test_run_backend_daemon_not_writable(
        self, provisioner_env, mocker, tmp_path
    ):
        prov_mod, prov, _ = provisioner_env
        footprint_file = tmp_path / "footprints.yml"
        footprint_file.touch()
        footprint_file.chmod(0o444)

        mocker.patch.dict(
            os.environ, {"OZWALD_FOOTPRINT_DATA": str(footprint_file)}
        )
        mock_logger = mocker.patch.object(prov_mod, "logger")

        if os.access(footprint_file, os.W_OK):
            pytest.skip("File is still writable even after chmod 444")

        prov.run_backend_daemon()

        mock_logger.error.assert_any_call(
            f"Footprint data file '{footprint_file}' is not writable; "
            "backend daemon cannot run",
        )

    def test_run_backend_daemon_parent_not_writable(
        self, provisioner_env, mocker, tmp_path
    ):
        prov_mod, prov, _ = provisioner_env
        read_only_dir = tmp_path / "readonly"
        read_only_dir.mkdir()
        footprint_file = read_only_dir / "footprints.yml"

        # Make dir read-only
        read_only_dir.chmod(0o555)

        mocker.patch.dict(
            os.environ, {"OZWALD_FOOTPRINT_DATA": str(footprint_file)}
        )
        mock_logger = mocker.patch.object(prov_mod, "logger")

        if os.access(read_only_dir, os.W_OK):
            pytest.skip("Directory is still writable even after chmod 555")

        prov.run_backend_daemon()

        mock_logger.error.assert_any_call(
            f"Footprint data directory '{read_only_dir}' is not writable; "
            "backend daemon cannot run",
        )

    def test_empty_list_retry_on_collision(self, provisioner_env):
        _prov_mod, prov, fake_cache = provisioner_env

        fake_cache._services = []
        fake_cache.raise_write_collision_once = True

        ok = prov.update_active_services([])
        assert ok
        # Only the successful write is recorded
        assert len(fake_cache.set_calls) == 1


class TestInitServiceProperties:
    def test_init_service_attaches_properties(self, provisioner_env):
        _, prov, _ = provisioner_env
        si = _svc_info("inst1", ServiceStatus.STARTING, service_name="svc1")
        # Ensure properties is empty initially
        si.properties = {}

        # Call _init_service
        # (It uses get_effective_service_definition from our StubConfigReader)
        initialized = prov._init_service(si)

        assert initialized.properties == {"resolved-prop": "val"}
        assert initialized.status == ServiceStatus.STARTING


class TestSystemProvisionerPersistence:
    def test_get_active_services_filtering(self, provisioner_env):
        _, prov, fake_cache = provisioner_env

        # Seed cache with mixed services
        p1 = _svc_info("persistent-1", ServiceStatus.AVAILABLE)
        p1.persistent = True
        n1 = _svc_info("non-persistent-1", ServiceStatus.AVAILABLE)
        n1.persistent = False

        fake_cache._services = [p1, n1]

        # All services
        assert len(prov.get_active_services()) == 2
        assert len(prov.get_active_services(persistent=None)) == 2

        # Persistent only
        persistent = prov.get_active_services(persistent=True)
        assert len(persistent) == 1
        assert persistent[0].name == "persistent-1"

        # Non-persistent only
        non_persistent = prov.get_active_services(persistent=False)
        assert len(non_persistent) == 1
        assert non_persistent[0].name == "non-persistent-1"

    def test_update_active_services_validation(self, provisioner_env):
        _, prov, _ = provisioner_env

        p1 = _svc_info("p1", ServiceStatus.AVAILABLE)
        p1.persistent = True
        n1 = _svc_info("n1", ServiceStatus.AVAILABLE)
        n1.persistent = False

        # Expecting persistent=True but got non-persistent service
        with pytest.raises(ValueError, match="is not persistent"):
            prov.update_active_services([n1], persistent=True)

        # Expecting persistent=False but got persistent service
        with pytest.raises(ValueError, match="is persistent"):
            prov.update_active_services([p1], persistent=False)

    def test_selective_removal_persistent(self, provisioner_env):
        _, prov, fake_cache = provisioner_env

        p1 = _svc_info("p1", ServiceStatus.AVAILABLE)
        p1.persistent = True
        p2 = _svc_info("p2", ServiceStatus.AVAILABLE)
        p2.persistent = True
        n1 = _svc_info("n1", ServiceStatus.AVAILABLE)
        n1.persistent = False

        fake_cache._services = [p1, p2, n1]

        # Update persistent services, omitting p2
        # This should mark p2 as STOPPING but leave n1 alone
        ok = prov.update_active_services([p1], persistent=True)
        assert ok

        persisted = fake_cache.set_calls[-1]

        # n1 should still be AVAILABLE (unchanged)
        # p1 should still be AVAILABLE (in update list)
        # p2 should be STOPPING (omitted from update list but in scope)
        names_to_status = {s.name: s.status for s in persisted}
        assert names_to_status["p1"] == ServiceStatus.AVAILABLE
        assert names_to_status["p2"] == ServiceStatus.STOPPING
        assert names_to_status["n1"] == ServiceStatus.AVAILABLE

    def test_selective_removal_non_persistent(self, provisioner_env):
        _, prov, fake_cache = provisioner_env

        p1 = _svc_info("p1", ServiceStatus.AVAILABLE)
        p1.persistent = True
        n1 = _svc_info("n1", ServiceStatus.AVAILABLE)
        n1.persistent = False
        n2 = _svc_info("n2", ServiceStatus.AVAILABLE)
        n2.persistent = False

        fake_cache._services = [p1, n1, n2]

        # Update non-persistent services, omitting n2
        ok = prov.update_active_services([n1], persistent=False)
        assert ok

        persisted = fake_cache.set_calls[-1]

        names_to_status = {s.name: s.status for s in persisted}
        assert names_to_status["p1"] == ServiceStatus.AVAILABLE
        assert names_to_status["n1"] == ServiceStatus.AVAILABLE
        assert names_to_status["n2"] == ServiceStatus.STOPPING

    def test_full_reconciliation(self, provisioner_env):
        _, prov, fake_cache = provisioner_env

        p1 = _svc_info("p1", ServiceStatus.AVAILABLE)
        p1.persistent = True
        n1 = _svc_info("n1", ServiceStatus.AVAILABLE)
        n1.persistent = False

        fake_cache._services = [p1, n1]

        # Update with empty list and persistent=None
        # Both should be marked as STOPPING
        ok = prov.update_active_services([], persistent=None)
        assert ok

        persisted = fake_cache.set_calls[-1]
        assert all(s.status == ServiceStatus.STOPPING for s in persisted)
