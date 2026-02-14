import os
import tempfile
from pathlib import Path
from typing import Optional, Tuple

import yaml
from invocate import task

from tasks import start_provisioner, stop_provisioner

OZWALD_PROVISIONER = os.environ.get("OZWALD_PROVISIONER", "unconfigured")


@task(namespace="test", name="unit")
def unit(c, path="tests/unit/"):
    """Run unit tests."""
    c.run(f"pytest {path}")


def _ensure_temp_assets(
    *,
    temp_root: Optional[str] = None,
    reuse: bool = False,
    provisioner_name: str = None,
) -> Tuple[Path, Path]:
    """Create (or reuse) a temp settings.yml and volume directory.

    Returns (root_dir, settings_path).
    """
    if temp_root:
        root = Path(temp_root).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
    else:
        root = Path(tempfile.mkdtemp(prefix="ozwald-it-"))

    settings_path = root / "settings.yml"
    solar_root = root / "solar_system"

    if not reuse or not (settings_path.exists() and solar_root.exists()):
        # Build sample volume tree
        (solar_root / "jupiter").mkdir(parents=True, exist_ok=True)
        (solar_root / "saturn").mkdir(parents=True, exist_ok=True)
        (solar_root / "jupiter" / "europa.txt").write_text(
            "icy world\n",
            encoding="utf-8",
        )
        (solar_root / "saturn" / "titan.txt").write_text(
            "hazy moon\n",
            encoding="utf-8",
        )

        provisioner_name = provisioner_name or os.environ.get(
            "OZWALD_PROVISIONER",
        )

        # Compose minimal settings
        cfg = {
            "provisioners": [
                {
                    "name": provisioner_name,
                    "host": provisioner_name,
                    "cache": {
                        "type": "redis",
                        "parameters": {
                            # The backend container reaches Redis by name
                            "host": "ozwald-provisioner-redis",
                            "port": 6379,
                            "db": 0,
                        },
                    },
                },
            ],
            "realms": {
                "default": {
                    "service-definitions": [
                        {
                            "name": "test_env_and_vols",
                            "type": "container",
                            "description": "Test environment and volumes",
                            "image": "test_env_and_vols",
                            "environment": {
                                "TEST_ENV_VAR": "test_env_var_value",
                                "ANOTHER_TEST_ENV_VAR": (
                                    "another_test_env_var_value"
                                ),
                                "FILE_LISTING_PATHS": "/solar_system",
                            },
                            "volumes": [
                                {
                                    "name": "solar_system",
                                    "target": "/solar_system",
                                    "read_only": True,
                                },
                            ],
                        },
                        {
                            "name": "simple_test_1",
                            "type": "container",
                            "description": "Simple test service",
                            "image": "simple_test_1",
                        },
                    ],
                }
            },
            "volumes": {
                "solar_system": {
                    "type": "bind",
                    "source": str(solar_root.resolve()),
                },
            },
        }
        settings_path.write_text(
            yaml.safe_dump(cfg, sort_keys=False),
            encoding="utf-8",
        )

    return root, settings_path


@task(namespace="test", name="integration")
def integration(
    c,
    path="tests/integration/",
    keep_temp: bool = False,
    reuse_temp: bool = False,
    temp_root: str = "",
    use_dev_settings: bool = False,
):
    """Run integration tests against provisioner service_definitions."""

    # Verify the API health endpoint is responsive (on host)
    port = int(os.environ.get("OZWALD_PROVISIONER_PORT", 8000))
    system_key = os.environ.get("OZWALD_SYSTEM_KEY")
    if not system_key:
        raise RuntimeError(
            "OZWALD_SYSTEM_KEY environment variable is not defined. "
            "This key is required for API authentication during integration "
            "tests.",
        )

    # Prepare settings: generate temp config unless opting into dev file
    if use_dev_settings:
        repo_root = Path(__file__).resolve().parents[1]
        settings_path = repo_root / "dev" / "resources" / "settings.yml"
        if not settings_path.exists():
            raise RuntimeError(f"Dev settings file not found: {settings_path}")
        root_dir = settings_path.parent
    else:
        root_dir, settings_path = _ensure_temp_assets(
            temp_root=(temp_root or None),
            reuse=reuse_temp,
            provisioner_name=OZWALD_PROVISIONER,
        )

    # Export env so both backend container and pytest see the same config
    os.environ["OZWALD_CONFIG"] = str(settings_path)
    os.environ["OZWALD_PROVISIONER"] = OZWALD_PROVISIONER

    # Stop/start provisioner stack with new config mounted
    stop_provisioner(c)
    start_provisioner(c, mount_source_dir=True)

    # Run the integration test suite
    # Expose env that tests may rely on
    env = {
        "OZWALD_PROVISIONER_PORT": str(port),
        "OZWALD_SYSTEM_KEY": system_key,
        # Pass through commonly used vars if set
        **{
            k: v
            for k, v in os.environ.items()
            if k
            in (
                "OZWALD_PROVISIONER_REDIS_PORT",
                "OZWALD_CONFIG",
                "OZWALD_PROVISIONER",
            )
        },
    }

    # Build env export string
    export_cmd = " ".join([f"{k}='{v}'" for k, v in env.items()])
    try:
        c.run(f'bash -lc "{export_cmd} pytest {path}"')
    finally:
        if not keep_temp and not use_dev_settings:
            try:
                # Best-effort cleanup of temp root
                import shutil

                shutil.rmtree(root_dir, ignore_errors=True)
            except Exception:
                pass


@task(namespace="test", name="coverage")
def coverage(
    c,
    path="tests/unit/",
    source="src",
    html=False,
    xml=False,
    fail_under=None,
):
    """Run tests with coverage measurement and print a coverage report.

    Args:
        c: invocate context (passed automatically).
        path: test path or pattern to run (default: "tests/").
        source: comma-separated package or directory paths to measure
            (default: "src").
        html: generate an HTML report (coverage html) if True.
        xml: generate an XML report (coverage xml) if True.
        fail_under: if provided (int/float), fail if total coverage is
            under this percent.

    """
    # Run pytest under coverage, measuring the specified source
    # directories/packages
    c.run(f"coverage run --source={source} -m pytest {path}")

    # Print a terminal report; optionally enforce a minimum coverage threshold
    report_cmd = "coverage report -m"
    if fail_under is not None:
        report_cmd += f" --fail-under={fail_under}"
    c.run(report_cmd)

    # Optionally generate additional report formats
    if html:
        c.run("coverage html")
    if xml:
        c.run("coverage xml")


@task(namespace="test", name="tox")
def dev_tox(c):
    """Run tox default environments locally."""
    c.run("tox -q", pty=True)
