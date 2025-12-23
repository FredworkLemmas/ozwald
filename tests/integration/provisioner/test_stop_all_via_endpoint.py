import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest
import redis
import requests
import yaml
from dotenv import load_dotenv

load_dotenv()


def _load_settings() -> dict:
    settings_path = os.environ.get("DEFAULT_OZWALD_CONFIG") or os.environ.get(
        "OZWALD_CONFIG"
    )
    if not settings_path:
        raise RuntimeError(
            "DEFAULT_OZWALD_CONFIG (or OZWALD_CONFIG) must point to the "
            "settings YAML for integration tests"
        )
    p = Path(settings_path)
    if not p.exists():
        raise RuntimeError(f"Settings file not found: {p}")
    with p.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _get_cache_params(cfg: dict) -> tuple[str, int, int, str | None]:
    name = os.environ.get("OZWALD_PROVISIONER")
    if not name:
        raise RuntimeError(
            "OZWALD_PROVISIONER must select a provisioner in the config"
        )
    provs = cfg.get("provisioners", [])
    for prov in provs:
        if prov.get("name") == name:
            params = (prov.get("cache") or {}).get("parameters", {})
            host = "localhost"
            port = int(os.environ.get("OZWALD_PROVISIONER_REDIS_PORT", 6479))
            db = int(params.get("db", 0))
            password = params.get("password")
            return host, port, db, password
    raise RuntimeError(f"Provisioner '{name}' not found in settings")


@pytest.fixture(autouse=True)
def _clear_redis_each_test():
    cfg = _load_settings()
    host, port, db, password = _get_cache_params(cfg)
    client = redis.Redis(
        host=host, port=port, db=db, password=password, decode_responses=True
    )
    client.flushdb()
    try:
        yield
    finally:
        client.flushdb()


def _api_base() -> str:
    port = int(os.environ.get("OZWALD_PROVISIONER_PORT", 8000))
    return f"http://localhost:{port}"


def _auth_headers() -> dict:
    key = os.environ.get("OZWALD_SYSTEM_KEY", "jenny8675")
    return {"Authorization": f"Bearer {key}"}


def _active_services_snapshot(host: str, port: int, db: int, password=None):
    client = redis.Redis(
        host=host, port=port, db=db, password=password, decode_responses=True
    )
    data = client.get("active_services")
    if not data:
        return []
    try:
        return json.loads(data)
    except Exception:
        return []


def _wait_for(predicate, timeout: float, interval: float = 0.5):
    start = time.time()
    while time.time() - start < timeout:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _docker_available() -> bool:
    return shutil.which("docker") is not None


def _container_running(name: str) -> bool:
    if not _docker_available():
        return False
    result = subprocess.run(
        [
            "docker",
            "ps",
            "--filter",
            f"name=^{name}$",
            "--format",
            "{{.Names}}",
        ],
        capture_output=True,
        text=True,
    )
    return any(line.strip() == name for line in result.stdout.splitlines())


def _api_ready() -> bool:
    try:
        r = requests.get(_api_base() + "/health", timeout=2)
        return r.status_code == 200
    except Exception:
        return False


def _ensure_image(image: str, dockerfile_path: str) -> None:
    """Ensure a Docker image exists locally; build if missing."""
    if not _docker_available():
        return
    check = subprocess.run(
        ["docker", "image", "inspect", image],
        capture_output=True,
        text=True,
    )
    if check.returncode == 0:
        return
    build = subprocess.run(
        ["docker", "build", "-t", image, "-f", dockerfile_path, "."],
        capture_output=True,
        text=True,
    )
    if build.returncode != 0:
        raise RuntimeError(
            f"Failed to build image {image}: {build.stderr or build.stdout}"
        )


class TestStopAllViaEndpoint:
    def test_post_empty_list_stops_all_running_services(self):
        # Use the known simple service that other integration tests use
        service_name = "simple_test_1"
        profile = None
        name_a = "it-simple_test_1-1"
        name_b = "it-simple_test_1-2"
        container_a = f"service-{name_a}"
        container_b = f"service-{name_b}"

        cfg = _load_settings()
        host, port, db, password = _get_cache_params(cfg)

        # Ensure API is responsive before first POST to avoid startup race
        assert _wait_for(_api_ready, timeout=15.0)

        # Ensure required container image is available
        dockerfile = Path("dockerfiles/Dockerfile.simple_test_1")
        _ensure_image("ozwald-simple_test_1", str(dockerfile))

        # Start two instances via the API
        body = [
            {"name": name_a, "service": service_name, "profile": profile},
            {"name": name_b, "service": service_name, "profile": profile},
        ]
        resp = requests.post(
            _api_base() + "/srv/services/active/update/",
            headers=_auth_headers(),
            json=body,
            timeout=10,
        )
        assert resp.status_code == 202, resp.text

        def both_available():
            items = _active_services_snapshot(host, port, db, password)
            have_a = have_b = False
            for it in items:
                if (
                    it.get("name") == name_a
                    and it.get("status") == "available"
                    and (it.get("info") or {}).get("container_id")
                ):
                    have_a = True
                if (
                    it.get("name") == name_b
                    and it.get("status") == "available"
                    and (it.get("info") or {}).get("container_id")
                ):
                    have_b = True
            return have_a and have_b

        assert _wait_for(both_available, timeout=60.0)

        # Now post empty list to stop all
        resp = requests.post(
            _api_base() + "/srv/services/active/update/",
            headers=_auth_headers(),
            json=[],
            timeout=10,
        )
        assert resp.status_code == 202, resp.text

        def redis_empty():
            items = _active_services_snapshot(host, port, db, password)
            return items == []

        assert _wait_for(redis_empty, timeout=60.0)

        # Optionally, verify containers stopped if Docker is available
        if _docker_available():
            # Containers may take a moment to stop; wait up to 60s
            assert _wait_for(
                lambda: not _container_running(container_a), timeout=60.0
            )
            assert _wait_for(
                lambda: not _container_running(container_b), timeout=60.0
            )
