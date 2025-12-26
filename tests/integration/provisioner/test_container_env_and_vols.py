import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Iterator, List

import pytest
import redis
import yaml

from orchestration.models import ServiceStatus

# --- Helpers shared with other integration tests (trimmed) ---


def _docker_available() -> bool:
    return shutil.which("docker") is not None


def _ensure_image(image: str, dockerfile_path: str) -> None:
    """Ensure the Docker image exists locally; build if missing."""
    check = subprocess.run(
        ["docker", "image", "inspect", image],
        check=False,
        capture_output=True,
        text=True,
    )
    if check.returncode == 0:
        return

    build = subprocess.run(
        ["docker", "build", "-t", image, "-f", dockerfile_path, "."],
        check=False,
        capture_output=True,
        text=True,
    )
    if build.returncode != 0:
        raise RuntimeError(
            f"Failed to build image {{image}}: {build.stderr or build.stdout}",
        )


def _redis_connection_parameters() -> dict:
    # Prefer DEFAULT_PROVISIONER_REDIS_PORT; fall back to
    # OZWALD_PROVISIONER_REDIS_PORT; default 6479.
    port_env = (
        os.environ.get("DEFAULT_PROVISIONER_REDIS_PORT")
        or os.environ.get("OZWALD_PROVISIONER_REDIS_PORT")
        or "6479"
    )
    port = int(port_env)
    return {"host": "localhost", "port": port, "db": 0}


def _flush_redis(host: str, port: int, db: int = 0) -> None:
    client = redis.Redis(host=host, port=port, db=db, decode_responses=True)
    client.flushdb()


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


def _container_logs(name: str, tail: int = 200) -> str:
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
            "Docker CLI not available; skipping env/vols integration test",
        )

    repo_root = Path(__file__).resolve().parents[3]
    dockerfile = repo_root / "dockerfiles" / "Dockerfile.test_env_and_vols"
    _ensure_image("test_env_and_vols", str(dockerfile))


@pytest.fixture(scope="module")
def config_from_env() -> Path:
    cfg_path = os.environ.get("OZWALD_CONFIG")
    assert cfg_path, "OZWALD_CONFIG must be set for integration tests"
    p = Path(cfg_path)
    assert p.exists(), f"Settings file not found: {p}"
    return p


@pytest.fixture(scope="module")
def env_for_daemon(config_from_env: Path) -> dict:
    # Ensure provisioner is set; default to 'jamma' if not provided
    mp = pytest.MonkeyPatch()
    mp.setenv(
        "OZWALD_PROVISIONER",
        os.environ.get(
            "OZWALD_PROVISIONER",
            os.environ.get("DEFAULT_OZWALD_PROVISIONER", "jamma"),
        ),
    )
    try:
        yield os.environ.copy()
    finally:
        mp.undo()


@pytest.fixture(autouse=True)
def clear_cache_between_tests(env_for_daemon) -> Iterator[None]:
    params = _redis_connection_parameters()
    _flush_redis(params["host"], params["port"], db=params["db"])
    try:
        yield
    finally:
        _flush_redis(params["host"], params["port"], db=params["db"])


# --- Provisioner interaction helpers ---


def _update_services(service_updates: List[dict]):
    from orchestration.models import Cache
    from orchestration.provisioner import SystemProvisioner

    # Reset singletons to pick up env in this process
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

    infos = [ServiceInformation(**item) for item in service_updates]
    prov.update_services(infos)


# --- The test ---


@pytest.fixture
def started_instances():
    """Collect instance names started by a test for teardown."""
    items = []
    return items


@pytest.fixture(autouse=True)
def stop_started_services_after_test(started_instances):
    """After each test, request stop and ensure containers are gone.

    Uses the provisioner control plane first, then falls back to a
    forced Docker remove if needed, to keep the environment clean.
    """
    # Run the test
    yield

    # Ask provisioner to stop everything
    _update_services([])

    # Wait for known containers to disappear, then force-remove if not
    for inst in started_instances:
        cname = f"service-{inst}"
        try:
            _wait_for(
                lambda cname=cname: not _container_running(cname),
                timeout=60.0,
                interval=0.5,
                description=f"container {cname} to stop",
            )
        except AssertionError:
            # Last resort to avoid lingering containers
            subprocess.run(
                ["docker", "rm", "-f", cname],
                check=False,
                capture_output=True,
                text=True,
            )


def test_container_env_and_volumes(
    docker_prereq,
    env_for_daemon,
    started_instances,
):
    """Verify that the test_env_and_vols container runs with the
    configured environment and that the generated solar_system
    directory is mounted at /solar_system inside the container.
    """
    svc_name = "test_env_and_vols"
    instance_name = "it-test_env_and_vols-1"
    body = [
        {
            "name": instance_name,
            "service": svc_name,
            "profile": None,
            "status": ServiceStatus.STARTING,
        },
    ]
    _update_services(body)

    # Register instance for teardown
    started_instances.append(instance_name)

    # Wait for container to appear
    container = f"service-{instance_name}"
    _wait_for(
        lambda: _container_running(container),
        timeout=30,
        description=f"container {container} running",
    )

    # Grab early logs (entrypoint prints YAML once on start)
    logs = _container_logs(container, tail=500)
    assert logs.strip(), "No logs captured from test container"

    # Parse YAML. The script prints two top-level sections with a blank
    # line between; yaml.safe_load will handle the whole text.
    data = yaml.safe_load(logs)
    assert isinstance(data, dict), "YAML output should be a mapping"

    # Check environment variables set via settings.yml
    env_map = data.get("environment") or {}
    assert env_map.get("TEST_ENV_VAR") == "test_env_var_value"
    assert env_map.get("ANOTHER_TEST_ENV_VAR") == "another_test_env_var_value"
    # The container should see the listing paths env var
    assert env_map.get("FILE_LISTING_PATHS") == "/solar_system"

    # Check file listings reflect the mounted volume contents.
    listings = data.get("file_listings") or []
    assert isinstance(listings, list)
    # Find the entry for /solar_system
    target = None
    for item in listings:
        if item.get("directory") == "/solar_system":
            print("found solar system dir")
            target = item
            break
    assert target is not None, "No listing for /solar_system in YAML output"

    files = target.get("files") or []

    # Expect to see europa.txt and titan.txt somewhere under the mount.
    # The entrypoint is expected to list files under the directory. This
    # test is intentionally specific; the implementation may evolve to
    # satisfy it (e.g., recursive listing).
    names = {f.get("filename") for f in files}

    print(f"files: {files}")
    print(f"names: {names}")

    # Read expected filenames from dev resources for clarity and
    # drift resistance; we only care about file basenames.
    # repo_root = Path(__file__).resolve().parents[3]
    # solar_root = repo_root / "dev" / "resources" / "solar_system"
    expected = {"europa.txt", "titan.txt"}

    # At least these two expected files should be in the listing.
    missing = expected - names
    assert not missing, (
        "Missing expected files from container listing: " + ", ".join(missing)
    )
