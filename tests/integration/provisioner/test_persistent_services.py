import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest
import redis
import yaml
from dotenv import load_dotenv

load_dotenv()


def _redis_connection_parameters() -> dict:
    port_env = (
        os.environ.get("OZWALD_PROVISIONER_REDIS_PORT")
        or os.environ.get("DEFAULT_PROVISIONER_REDIS_PORT")
        or "6479"
    )
    return {"host": "localhost", "port": int(port_env), "db": 0}


def _flush_redis():
    params = _redis_connection_parameters()
    client = redis.Redis(**params)
    client.flushdb()


def _active_services_snapshot() -> list:
    params = _redis_connection_parameters()
    client = redis.Redis(**params, decode_responses=True)
    data = client.get("active_services")
    return json.loads(data) if data else []


@pytest.fixture
def env_for_persistent(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    config_data = {
        "realms": {
            "default": {
                "persistent-services": [{"name": "p1", "service": "sleep-svc"}],
                "service-definitions": [
                    {
                        "name": "sleep-svc",
                        "type": "container",
                        "image": "alpine",
                        "command": "sleep 10",
                    }
                ],
            }
        },
        "provisioners": [
            {
                "name": "jamma",
                "host": "jamma",
                "cache": {
                    "type": "redis",
                    "parameters": _redis_connection_parameters(),
                },
            }
        ],
    }

    config_file = config_dir / "ozwald.yml"
    with open(config_file, "w") as f:
        yaml.dump(config_data, f)

    footprint_data = tmp_path / "footprints.yml"
    with open(footprint_data, "w") as f:
        yaml.dump([], f)

    monkeypatch.setenv("OZWALD_CONFIG", str(config_file))
    monkeypatch.setenv("OZWALD_FOOTPRINT_DATA", str(footprint_data))
    monkeypatch.setenv("OZWALD_PROVISIONER", "jamma")
    monkeypatch.setenv("OZWALD_HOST", "jamma")

    # Ensure PYTHONPATH includes src
    current_pythonpath = os.environ.get("PYTHONPATH", "")
    src_path = str(Path.cwd() / "src")
    if current_pythonpath:
        monkeypatch.setenv("PYTHONPATH", f"{src_path}:{current_pythonpath}")
    else:
        monkeypatch.setenv("PYTHONPATH", src_path)

    _flush_redis()

    return {"config_file": config_file, "footprint_data": footprint_data}


@pytest.mark.integration
@pytest.mark.skipif(
    shutil.which("docker") is None, reason="docker not available"
)
def test_persistent_services_lifecycle(env_for_persistent):
    # Ensure alpine image is present
    subprocess.run(["docker", "pull", "alpine"], check=True)

    env = os.environ.copy()

    # Start the backend daemon
    backend_proc = subprocess.Popen(
        [
            "python3",
            "-m",
            "orchestration.provisioner",
        ],
        env=env,
    )

    try:
        # 1. Verify p1 starts on daemon startup
        found = False
        for _ in range(30):
            active = _active_services_snapshot()
            if any(
                s["name"] == "p1" and s["status"] == "available" for s in active
            ):
                found = True
                p1 = next(s for s in active if s["name"] == "p1")
                assert p1["persistent"] is True
                break
            time.sleep(1)

        assert found, (
            "Persistent service p1 did not become available. "
            f"Active: {_active_services_snapshot()}"
        )

        # 2. Verify update_dynamic_services does not stop p1
        # We call it via a separate process to simulate API call/CLI
        cmd = [
            "python3",
            "-c",
            "from orchestration.provisioner import SystemProvisioner; "
            "prov = SystemProvisioner.singleton(); "
            "prov.update_active_services([], persistent=False)",
        ]
        subprocess.run(cmd, env=env, check=True)

        active = _active_services_snapshot()
        assert any(s["name"] == "p1" for s in active), (
            "Persistent service was stopped by dynamic update"
        )

    finally:
        # 3. Verify SIGTERM triggers shutdown of persistent services
        backend_proc.terminate()

        # Wait for p1 to be removed from Redis
        stopped = False
        for _ in range(70):
            active = _active_services_snapshot()
            if not any(s["name"] == "p1" for s in active):
                stopped = True
                break
            time.sleep(1)

        assert stopped, "Persistent service was not stopped on daemon shutdown"

        if backend_proc.poll() is None:
            backend_proc.kill()
