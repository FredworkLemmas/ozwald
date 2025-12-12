"""Test suite for `ActiveServicesCache` in `src/util/active_services_cache.py`.

These tests validate that `ActiveServicesCache`:
- initializes a Redis client with expected parameters,
- stores services atomically using a lock and handles lock contention/errors,
- and retrieves services as `ServiceInformation` objects from cached JSON.

The tests use pytest fixtures and the `mocker` fixture (no `patch` decorator).
"""

import json

import pytest

from src.orchestration.models import Cache, ServiceInformation, ServiceStatus
from src.util.active_services_cache import ActiveServicesCache, WriteCollision


@pytest.fixture
def cache_default() -> Cache:
    """Provide a `Cache` model with empty parameters to exercise defaults."""

    return Cache(type="redis", parameters={})


@pytest.fixture
def cache_with_params() -> Cache:
    """
    Provide a `Cache` model with explicit Redis parameters (including
    password).
    """

    return Cache(
        type="redis",
        parameters={
            "host": "example.local",
            "port": 6380,
            "db": 2,
            "password": "s3cr3t",
        },
    )


@pytest.fixture
def redis_mock(mocker):
    """
    Patch `redis.Redis` used by `ActiveServicesCache` and return the mock
    class.

    The returned value is the mocked constructor (i.e., the class), whose
    `.return_value` represents the client instance used by the cache class.
    """

    return mocker.patch("src.util.active_services_cache.redis.Redis")


@pytest.fixture
def active_cache_default(
    cache_default: Cache, redis_mock
) -> ActiveServicesCache:
    """
    Create an `ActiveServicesCache` bound to the mocked Redis with
    default params.
    """

    return ActiveServicesCache(cache_default)


@pytest.fixture
def active_cache_params(
    cache_with_params: Cache, redis_mock
) -> ActiveServicesCache:
    """Create an `ActiveServicesCache` with explicit Redis parameters."""

    return ActiveServicesCache(cache_with_params)


class TestInitialization:
    """Initialization and Redis client parameter handling."""

    def test_initializes_with_defaults(
        self, cache_default: Cache, redis_mock, active_cache_default
    ):
        """
        It constructs Redis with default host/port/db and
        decode_responses=True.
        """

        redis_mock.assert_called_once_with(
            host="localhost",
            port=6379,
            db=0,
            password=None,
            decode_responses=True,
        )

    def test_initializes_with_explicit_parameters(
        self, cache_with_params: Cache, redis_mock, active_cache_params
    ):
        """It forwards parameters from the `Cache` model to the Redis client."""

        params = cache_with_params.parameters or {}
        redis_mock.assert_called_once_with(
            host=params.get("host"),
            port=params.get("port"),
            db=params.get("db", 0),
            password=params.get("password"),
            decode_responses=True,
        )


class TestSetServices:
    """Behavior of `set_services` regarding locks and serialization."""

    def test_sets_services_when_lock_acquired(
        self, active_cache_default: ActiveServicesCache, redis_mock
    ):
        """
        When the lock is acquired, services are JSON-encoded and stored,
        and the lock is released.
        """

        redis_client = redis_mock.return_value
        lock = redis_client.lock.return_value
        lock.acquire.return_value = True

        services = [
            ServiceInformation(
                name="svc1",
                service="svc",
                profile="default",
                status=ServiceStatus.AVAILABLE,
            ),
            ServiceInformation(
                name="svc2",
                service="svc",
                profile="alt",
                status=ServiceStatus.STARTING,
            ),
        ]

        active_cache_default.set_services(services)

        # Verify lock interactions
        redis_client.lock.assert_called_once()
        lock.acquire.assert_called_once_with(blocking=False)
        assert lock.release.called, "Lock should be released in finally block"

        # Verify stored JSON payload
        args, kwargs = redis_client.set.call_args
        assert args[0] == active_cache_default.CACHE_KEY
        stored_json = args[1]
        decoded = json.loads(stored_json)
        assert isinstance(decoded, list)
        # Round-trip check against the original objects
        assert [ServiceInformation(**d).model_dump() for d in decoded] == [
            s.model_dump() for s in services
        ]

    def test_raises_write_collision_when_lock_not_acquired(
        self, active_cache_default: ActiveServicesCache, redis_mock
    ):
        """
        If the lock cannot be acquired, `WriteCollision` is raised and
        nothing is written.
        """

        redis_client = redis_mock.return_value
        lock = redis_client.lock.return_value
        lock.acquire.return_value = False

        with pytest.raises(WriteCollision):
            active_cache_default.set_services([])

        redis_client.set.assert_not_called()
        # No release expected since acquire returned False
        assert not lock.release.called

    def test_wraps_redis_lock_error_as_runtime_error(
        self, active_cache_default: ActiveServicesCache, redis_mock, mocker
    ):
        """
        If Redis lock operations raise `LockError`, it is wrapped as
        `RuntimeError`.
        """

        # Reconfigure the lock to raise redis.exceptions.LockError on acquire
        from redis.exceptions import LockError

        redis_client = redis_mock.return_value
        lock = redis_client.lock.return_value
        lock.acquire.side_effect = LockError("boom")

        with pytest.raises(RuntimeError) as exc:
            active_cache_default.set_services([])

        assert "Lock error while setting active services" in str(exc.value)
        redis_client.set.assert_not_called()


class TestGetServices:
    """Behavior of `get_services` data retrieval and decoding."""

    def test_returns_empty_list_when_no_data(
        self, active_cache_default: ActiveServicesCache, redis_mock
    ):
        """If the cache key is absent, an empty list is returned."""

        redis_client = redis_mock.return_value
        redis_client.get.return_value = None

        result = active_cache_default.get_services()
        assert result == []

    def test_returns_service_information_objects(
        self, active_cache_default: ActiveServicesCache, redis_mock
    ):
        """
        It decodes JSON and returns a list of `ServiceInformation`
        objects.
        """

        services = [
            ServiceInformation(
                name="svcA",
                service="svc",
                profile="p1",
                status=ServiceStatus.AVAILABLE,
            ),
            ServiceInformation(
                name="svcB",
                service="svc",
                profile="p2",
                status=ServiceStatus.AVAILABLE,
            ),
        ]
        payload = json.dumps([s.model_dump() for s in services])

        redis_client = redis_mock.return_value
        redis_client.get.return_value = payload

        result = active_cache_default.get_services()

        assert isinstance(result, list)
        assert [s.model_dump() for s in result] == [
            s.model_dump() for s in services
        ]
