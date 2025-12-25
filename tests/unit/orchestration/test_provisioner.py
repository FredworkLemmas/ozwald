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
def provisioner_env(monkeypatch):
    """Patch orchestration.provisioner dependencies for isolated daemon
    tests.
    """
    import orchestration.provisioner as prov_mod
    from orchestration.provisioner import SystemProvisioner

    # Replace the ActiveServicesCache used by SystemProvisioner with our fake
    monkeypatch.setattr(
        prov_mod,
        "ActiveServicesCache",
        FakeActiveServicesCache,
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

        def get_service_by_name(self, name: str):
            return StubServiceDef(self._type_value)

        # The daemon may read .services elsewhere in class; provide empty
        # default
        @property
        def services(self):
            return []

    cache = Cache(type="memory", parameters={})
    config_reader = StubConfigReader()
    prov = SystemProvisioner(config_reader=config_reader, cache=cache)

    # Expose internals for tests to seed/inspect cache
    fake_cache: FakeActiveServicesCache = prov._active_services_cache  # type: ignore[attr-defined]

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
    # With current semantics, stopped services are removed from cache.
    # The final persisted list should no longer include the service.
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

        # Seed two AVAILABLE services
        a = _svc_info("svc-a", ServiceStatus.AVAILABLE)
        b = _svc_info("svc-b", ServiceStatus.AVAILABLE)
        fake_cache._services = [a, b]

        ok = prov.update_services([])
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
        ok = prov.update_services([])
        assert ok
        # A write with an empty list is still a valid operation
        assert fake_cache.set_calls
        assert fake_cache.set_calls[-1] == []

    def test_empty_list_retry_on_collision(self, provisioner_env):
        _prov_mod, prov, fake_cache = provisioner_env

        fake_cache._services = []
        fake_cache.raise_write_collision_once = True

        ok = prov.update_services([])
        assert ok
        # Only the successful write is recorded
        assert len(fake_cache.set_calls) == 1
