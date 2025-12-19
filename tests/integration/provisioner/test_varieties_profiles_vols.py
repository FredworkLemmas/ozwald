import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest
import yaml

from orchestration.models import ServiceStatus


def _docker_available() -> bool:
    return shutil.which("docker") is not None


def _ensure_image(image: str, dockerfile_path: str) -> None:
    # Ensure base tag exists
    check = subprocess.run(
        ["docker", "image", "inspect", image],
        capture_output=True,
        text=True,
    )
    if check.returncode != 0:
        build = subprocess.run(
            [
                "docker",
                "build",
                "-t",
                image,
                "-f",
                dockerfile_path,
                ".",
            ],
            capture_output=True,
            text=True,
        )
        if build.returncode != 0:
            raise RuntimeError(
                f"Failed to build image {image}: {build.stderr or build.stdout}"
            )

    # Ensure the runtime prefix tag expected by code is present
    prefixed = f"ozwald-{image}"
    check2 = subprocess.run(
        ["docker", "image", "inspect", prefixed],
        capture_output=True,
        text=True,
    )
    if check2.returncode != 0:
        tag = subprocess.run(
            ["docker", "tag", image, prefixed],
            capture_output=True,
            text=True,
        )
        if tag.returncode != 0:
            raise RuntimeError(
                f"Failed to tag image {image} as {prefixed}: "
                f"{tag.stderr or tag.stdout}"
            )


def _wait_for(predicate, timeout: float, interval: float = 0.5):
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
        raise AssertionError(f"Timed out waiting: last error: {last_err}")
    raise AssertionError("Timed out waiting for condition")


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
        capture_output=True,
        text=True,
    )
    return any(line.strip() == name for line in result.stdout.splitlines())


def _container_logs(name: str, tail: int = 200) -> str:
    result = subprocess.run(
        ["docker", "logs", "--tail", str(tail), name],
        capture_output=True,
        text=True,
    )
    return result.stdout


def _exec_in_container(name: str, cmd: str) -> int:
    res = subprocess.run(
        ["docker", "exec", name, "sh", "-c", cmd],
        capture_output=True,
        text=True,
    )
    return res.returncode


def _redis_connection_parameters() -> dict:
    port_env = (
        os.environ.get("DEFAULT_PROVISIONER_REDIS_PORT")
        or os.environ.get("OZWALD_PROVISIONER_REDIS_PORT")
        or "6479"
    )
    db_env = os.environ.get("TEST_REDIS_DB") or "14"
    return {"host": "localhost", "port": int(port_env), "db": int(db_env)}


def _flush_redis(host: str, port: int, db: int = 0) -> None:
    import redis

    client = redis.Redis(host=host, port=port, db=db, decode_responses=True)
    client.flushdb()


def _update_services(service_updates: list[dict]):
    from orchestration.models import Cache, ServiceInformation
    from orchestration.provisioner import SystemProvisioner

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

    infos = [ServiceInformation(**item) for item in service_updates]
    prov.update_services(infos)


def _start_services_locally(service_updates: list[dict]):
    """
    Start services immediately in-process without relying on a background
    daemon. This avoids interference from any externally running
    provisioner that may be using a different settings file.
    """
    from orchestration.models import ServiceInformation
    from services.container import ContainerService

    # Ensure singletons refer to this process config/cache
    _update_services(service_updates)

    infos = [ServiceInformation(**item) for item in service_updates]
    for si in infos:
        svc = ContainerService(service_info=si)
        svc.start()


@pytest.fixture(scope="module")
def docker_prereq():
    if not _docker_available():
        pytest.skip("Docker not available; skipping integration tests")
    repo_root = Path(__file__).resolve().parents[3]
    dockerfile = repo_root / "dockerfiles" / "Dockerfile.test_env_and_vols"
    _ensure_image("test_env_and_vols", str(dockerfile))


@pytest.fixture(scope="module")
def temp_settings_file(tmp_path_factory):
    root = tmp_path_factory.mktemp("vp-settings")
    # Prepare directories and files
    solar = root / "solar_system"
    extras = root / "extras"
    third = root / "third"
    solar.mkdir()
    extras.mkdir()
    third.mkdir()
    # seed files
    (solar / "europa.txt").write_text("europa\n")
    (solar / "titan.txt").write_text("titan\n")
    (extras / "extra.txt").write_text("extra\n")
    (third / "third.txt").write_text("third\n")

    cfg = {
        "hosts": [{"name": "localhost", "ip": "127.0.0.1"}],
        "services": [
            {
                "name": "test_env_and_vols",
                "type": "container",
                "image": "test_env_and_vols",
                "environment": {
                    "FILE_LISTING_PATHS": "/solar_system",
                },
                "volumes": [
                    {
                        "name": "solar_system",
                        "target": "/solar_system",
                        "read_only": True,
                    }
                ],
                "varieties": {
                    "A": {
                        "environment": {
                            "FILE_LISTING_PATHS": "/solar_system:/extras",
                        },
                        "volumes": [
                            {
                                "name": "solar_extras",
                                "target": "/extras",
                                "read_only": False,
                            }
                        ],
                    },
                    "B": {
                        "volumes": [
                            {
                                "name": "solar_system",
                                "target": "/solar_system",
                                "read_only": False,
                            }
                        ]
                    },
                },
                "profiles": {
                    "P": {
                        "environment": {
                            "FILE_LISTING_PATHS": (
                                "/solar_system:/extras:/third"
                            ),
                        },
                        "volumes": [
                            {
                                "name": "solar_system",
                                "target": "/solar_system",
                                "read_only": True,
                            },
                            {
                                "name": "solar_third",
                                "target": "/third",
                                "read_only": False,
                            },
                        ],
                    }
                },
            }
        ],
        "provisioners": [],
        "volumes": {
            "solar_system": {
                "type": "bind",
                "source": "${SETTINGS_FILE_DIR}/solar_system",
            },
            "solar_extras": {
                "type": "bind",
                "source": "${SETTINGS_FILE_DIR}/extras",
            },
            "solar_third": {
                "type": "bind",
                "source": "${SETTINGS_FILE_DIR}/third",
            },
        },
    }

    cfg_path = root / "settings.yml"
    cfg_path.write_text(yaml.safe_dump(cfg))
    return cfg_path


@pytest.fixture(scope="module")
def env_setup(temp_settings_file):
    # Ensure provisioner and config env are set
    os.environ.setdefault(
        "OZWALD_PROVISIONER",
        os.environ.get("DEFAULT_OZWALD_PROVISIONER", "jamma"),
    )
    os.environ["OZWALD_CONFIG"] = str(temp_settings_file)
    # Ensure a host is set for BaseProvisionableService
    os.environ.setdefault("OZWALD_HOST", "localhost")
    return os.environ.copy()


@pytest.fixture(autouse=True)
def clear_cache_between_tests(env_setup):
    params = _redis_connection_parameters()
    _flush_redis(params["host"], params["port"], db=params["db"])
    try:
        yield
    finally:
        _flush_redis(params["host"], params["port"], db=params["db"])


class TestVarietiesProfilesVolumes:
    def test_variety_union(self, docker_prereq, env_setup):
        """It should include volumes from both base and variety."""
        name = f"it-vp-A-{int(time.time()) % 100000}"
        svc = "test_env_and_vols"
        body = [
            {
                "name": name,
                "service": svc,
                "variety": "A",
                "profile": None,
                "status": ServiceStatus.STARTING,
            }
        ]
        _start_services_locally(body)
        container = f"service-{name}"
        _wait_for(lambda: _container_running(container), 30)
        logs = _container_logs(container, tail=500)
        data = yaml.safe_load(logs)
        listings = data.get("file_listings") or []
        dirs = {item.get("directory") for item in listings}
        assert "/solar_system" in dirs
        assert "/extras" in dirs

    def test_variety_overrides_base_rw(self, docker_prereq, env_setup):
        """Variety volume definition (rw) should override base (ro)."""
        name = f"it-vp-B-{int(time.time()) % 100000}"
        svc = "test_env_and_vols"
        body = [
            {
                "name": name,
                "service": svc,
                "variety": "B",
                "profile": None,
                "status": ServiceStatus.STARTING,
            }
        ]
        _start_services_locally(body)
        container = f"service-{name}"
        _wait_for(lambda: _container_running(container), 30)
        rc = _exec_in_container(container, "echo x > /solar_system/_w")
        assert rc == 0

    def test_profile_overrides_variety_and_unions(
        self, docker_prereq, env_setup
    ):
        """It should overwrite base and variety volumes with profile volumes."""
        name = f"it-vp-BP-{int(time.time()) % 100000}"
        svc = "test_env_and_vols"
        body = [
            {
                "name": name,
                "service": svc,
                "variety": "B",
                "profile": "P",
                "status": ServiceStatus.STARTING,
            }
        ]
        _start_services_locally(body)
        container = f"service-{name}"
        _wait_for(lambda: _container_running(container), 30)
        # profile P sets solar_system back to ro
        rc = _exec_in_container(container, "echo x > /solar_system/_w")
        assert rc != 0
        logs = _container_logs(container, tail=500)
        data = yaml.safe_load(logs)
        listings = data.get("file_listings") or []
        dirs = {item.get("directory") for item in listings}
        assert "/solar_system" in dirs
        assert "/extras" in dirs
        assert "/third" in dirs
