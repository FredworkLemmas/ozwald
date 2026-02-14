import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Iterator, List

import pytest
import redis
import yaml
from dotenv import load_dotenv

from orchestration.models import Cache, ServiceStatus

load_dotenv()

external_redis_port = os.environ.get("DEFAULT_PROVISIONER_REDIS_PORT")
# --- Test configuration helpers ---


def _redis_connection_parameters() -> dict:
    repo_root = Path(__file__).resolve().parents[3]
    settings_path = repo_root / "dev" / "resources" / "settings.yml"
    with settings_path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    name = os.environ.get("OZWALD_PROVISIONER", "jamma")
    provs = cfg.get("provisioners", [])
    cache_params = {}
    for prov in provs:
        if prov.get("name") == name:
            cache_params = (prov.get("cache") or {}).get("parameters", {})
            break

    port_env = (
        os.environ.get("OZWALD_PROVISIONER_REDIS_PORT")
        or os.environ.get("DEFAULT_PROVISIONER_REDIS_PORT")
        or "6479"
    )
    db = cache_params.get("db", 0)
    return {"host": "localhost", "port": int(port_env), "db": db}


def _docker_available() -> bool:
    return shutil.which("docker") is not None


def _ensure_image(image: str, dockerfile_path: str) -> None:
    """Ensure the Docker image exists locally; build if missing."""
    # Check for image
    check = subprocess.run(
        ["docker", "image", "inspect", image],
        check=False,
        capture_output=True,
        text=True,
    )
    if check.returncode == 0:
        return

    # Build image
    build = subprocess.run(
        ["docker", "build", "-t", image, "-f", dockerfile_path, "."],
        check=False,
        capture_output=True,
        text=True,
    )
    if build.returncode != 0:
        raise RuntimeError(
            f"Failed to build image {image}: {build.stderr or build.stdout}",
        )


def _flush_redis(
    host: str,
    port: int,
    db: int = 0,
    password: str | None = None,
):
    client = redis.Redis(
        host=host,
        port=port,
        db=db,
        password=password,
        decode_responses=True,
    )
    client.flushdb()


def _active_services_snapshot(
    host: str,
    port: int,
    db: int = 0,
    password: str | None = None,
) -> list:
    client = redis.Redis(
        host=host,
        port=port,
        db=db,
        password=password,
        decode_responses=True,
    )
    data = client.get("active_services")
    if not data:
        return []
    try:
        return json.loads(data)
    except Exception:
        return []


def _container_running(name: str) -> bool:
    result = subprocess.run(
        [
            "docker",
            "ps",
            "--filter",
            f"name=^{name}$",
            "--format",
            "{{.Names}}",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    return any(line.strip() == name for line in result.stdout.splitlines())


def _container_logs(name: str, tail: int = 10) -> str:
    result = subprocess.run(
        ["docker", "logs", "--tail", str(tail), name],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout


def _wait_for(
    predicate,
    timeout: float,
    interval: float = 0.5,
    description: str = "condition",
):
    start = time.time()
    last_err = None
    while time.time() - start < timeout:
        try:
            if predicate():
                return True
        except Exception as e:
            last_err = e
        time.sleep(interval)
    if last_err:
        raise AssertionError(
            f"Timed out waiting for {description}: last error: {last_err}",
        )
    raise AssertionError(f"Timed out waiting for {description}")


# --- Pytest fixtures ---


@pytest.fixture(scope="module")
def docker_prereq():
    if not _docker_available():
        pytest.skip(
            "Docker CLI not available; skipping provisioner "
            "backend integration tests",
        )

    # Ensure simple_test_1 image exists (build if needed)
    repo_root = Path(__file__).resolve().parents[3]
    dockerfile = repo_root / "dockerfiles" / "Dockerfile.simple_test_1"
    _ensure_image("ozwald-simple_test_1", str(dockerfile))


@pytest.fixture(scope="module")
def dev_settings_path() -> Path:
    """Return the path to the dev settings file used by running containers.

    This replaces generating a temporary, contrived settings file. Tests should
    align with the development stack and use dev/resources/settings.yml.
    """
    repo_root = Path(__file__).resolve().parents[3]
    path = repo_root / "dev" / "resources" / "settings.yml"
    assert path.exists(), f"Dev settings file not found at {path}"
    return path


@pytest.fixture(scope="module")
def env_for_daemon(dev_settings_path: Path) -> dict:
    """Prepare environment for both the parent test process and the daemon
    subprocess.

    This sets OZWALD_CONFIG and OZWALD_PROVISIONER in the current process
    (so that in-process calls like SystemProvisioner.singleton() use the
    same config), and returns a full environment mapping to pass to the
    daemon subprocess.
    """
    # Use pytest.MonkeyPatch directly because this is a module-scoped fixture
    mp = pytest.MonkeyPatch()
    mp.setenv("OZWALD_CONFIG", str(dev_settings_path))
    # Use default provisioner name consistent with development containers
    default_prov = os.environ.get("DEFAULT_OZWALD_PROVISIONER", "jamma")
    mp.setenv("OZWALD_PROVISIONER", default_prov)

    # Build env for the subprocess with the same variables
    env = os.environ.copy()

    # Teardown to restore environment after the module's tests complete
    try:
        yield env
    finally:
        mp.undo()


def _get_cache_params_from_env() -> tuple[str, int, int | None]:
    cache_params = _redis_connection_parameters()
    return cache_params["host"], cache_params["port"], cache_params["db"]


@pytest.fixture(autouse=True)
def clear_cache_between_tests(env_for_daemon) -> Iterator[None]:
    # Use the same Redis params as configured in dev settings
    redis_params = _redis_connection_parameters()
    host = redis_params["host"]
    port = redis_params["port"]
    db = redis_params["db"]
    _flush_redis(host, port, db=db)
    try:
        yield
    finally:
        _flush_redis(host, port, db=db)


# Note: The backend daemon is assumed to already be running by the test
# task that invokes this suite. No local fixture is required here.


# --- Core test logic ---


def _update_services(service_updates: List[dict]):
    # Use the in-process provisioner to write to Redis cache
    # Ensure our environment for SystemProvisioner is set (fixtures do this
    # at module level)
    from orchestration.provisioner import SystemProvisioner

    # Reset singletons in this process to pick up env settings
    try:
        import config.reader as cfg_mod

        cfg_mod._system_config_reader = None  # type: ignore
    except Exception:
        pass
    try:
        import orchestration.provisioner as prov_mod

        prov_mod._system_provisioner = None  # type: ignore
    except Exception:
        pass

    cache = Cache(type="redis", parameters=_redis_connection_parameters())
    prov = SystemProvisioner.singleton(cache=cache)

    from orchestration.models import ServiceInformation

    objs = [ServiceInformation(**u) for u in service_updates]
    ok = prov.update_services(objs)
    assert ok, "update_services returned False"


def test_run_backend_daemon_start_stop_two_instances_individually(
    env_for_daemon,
):
    """Test basic functionality of the provisioner backend daemon:

    - Add a single simple_test_1 instance to cache with status STARTING
      and verify it starts
    - Add a second instance; verify both run and are AVAILABLE
    - Stop the first instance only; verify it stops and is removed from
      cache, second continues
    - Stop the second instance; verify cleanup
    - Verify containers log the expected lines
    """
    service_name = "simple_test_1"
    profile = None
    name_a = "it-simple_test_1-1"
    name_b = "it-simple_test_1-2"
    container_a = f"ozsvc--default--{name_a}"
    container_b = f"ozsvc--default--{name_b}"

    cache_host, cache_port, cache_db = _get_cache_params_from_env()

    # 1) Start A
    _update_services([
        {
            "name": name_a,
            "service": service_name,
            "profile": profile,
            "status": ServiceStatus.STARTING,
        },
    ])

    # Wait for container A running and AVAILABLE in cache
    _wait_for(
        lambda: _container_running(container_a),
        timeout=45,
        description=f"container {container_a} running",
    )

    def a_available():
        items = _active_services_snapshot(cache_host, cache_port, db=cache_db)
        for it in items:
            if (
                it.get("name") == name_a
                and it.get("status") == "available"
                and (it.get("info") or {}).get("container_id")
            ):
                return True
        return False

    _wait_for(
        a_available,
        timeout=45,
        description="service A AVAILABLE in cache",
    )

    # Verify logs have expected lines
    logs_a = _container_logs(container_a, tail=20)
    assert "hostname:" in logs_a
    assert "time_utc:" in logs_a

    # 2) Start B (keeping A)
    _update_services([
        {"name": name_a, "service": service_name, "profile": profile},
        {"name": name_b, "service": service_name, "profile": profile},
    ])

    _wait_for(
        lambda: _container_running(container_b),
        timeout=45,
        description=f"container {container_b} running",
    )

    def both_available():
        items = _active_services_snapshot(cache_host, cache_port, db=cache_db)
        have_a = have_b = False
        for it in items:
            if it.get("name") == name_a and it.get("status") == "available":
                have_a = True
            if it.get("name") == name_b and it.get("status") == "available":
                have_b = True
        return have_a and have_b

    _wait_for(
        both_available,
        timeout=45,
        description="both service_definitions AVAILABLE in cache",
    )

    logs_b = _container_logs(container_b, tail=20)
    assert "hostname:" in logs_b
    assert "time_utc:" in logs_b

    # 3) Stop A only: update list to just B
    _update_services([
        {"name": name_b, "service": service_name, "profile": profile},
    ])

    # Wait until container A is not running and removed from cache
    _wait_for(
        lambda: not _container_running(container_a),
        timeout=60,
        description=f"container {container_a} stopped",
    )

    def only_b_in_cache():
        items = _active_services_snapshot(cache_host, cache_port, db=cache_db)
        names = {it.get("name") for it in items}
        return names == {name_b}

    _wait_for(
        only_b_in_cache,
        timeout=45,
        description="only service B remains in cache",
    )

    # B should still be running
    assert _container_running(container_b)

    # 4) Stop B: update with empty list
    _update_services([])

    _wait_for(
        lambda: not _container_running(container_b),
        timeout=60,
        description=f"container {container_b} stopped",
    )

    def cache_empty():
        return (
            _active_services_snapshot(cache_host, cache_port, db=cache_db) == []
        )

    _wait_for(
        cache_empty,
        timeout=45,
        description="active service_definitions cache empty",
    )
