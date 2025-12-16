import os
import subprocess
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv

BASE_DIR = os.getcwd()
env_path = os.path.join(BASE_DIR, ".env")
load_dotenv(dotenv_path=env_path)

PROVISIONER_NETWORK = "provisioner_network"


def _run(
    cmd: str, *, check: bool = False, capture: bool = False
) -> subprocess.CompletedProcess:
    """Run a shell command using subprocess.

    Args:
        cmd: Command string to execute.
        check: If True, raise on non-zero return code.
        capture: If True, capture stdout/stderr.
    """
    kwargs = {
        "shell": True,
        "text": True,
    }
    if capture:
        kwargs.update({"stdout": subprocess.PIPE, "stderr": subprocess.PIPE})
    return subprocess.run(cmd, check=check, **kwargs)


def _get_installed_gpu_drivers() -> List[str]:
    """Inspect loaded kernel modules to detect GPU drivers."""
    try:
        result = _run("lsmod", capture=True)
        output = result.stdout or ""
    except Exception:
        output = ""
    drivers: List[str] = []
    for line in output.splitlines():
        if not line or line.startswith("Module"):
            continue
        name = line.split()[0]
        if name == "amdgpu":
            drivers.append("amdgpu")
        elif name == "nvidia":
            drivers.append("nvidia")
    return drivers


def ensure_provisioner_network() -> None:
    """Create the shared docker network if it does not already exist."""
    result = _run(
        "docker network ls "
        f"--filter name=^{PROVISIONER_NETWORK}$ "
        "--format '{{.Name}}'",
        capture=True,
    )
    print(f"result: {result.stdout}")
    if (result.stdout or "").strip() != PROVISIONER_NETWORK:
        print(f"Creating docker network '{PROVISIONER_NETWORK}'...")
        _run(f"docker network create {PROVISIONER_NETWORK}", check=True)
        print(f"✓ Network '{PROVISIONER_NETWORK}' created")
    else:
        print(f"Network '{PROVISIONER_NETWORK}' already exists")


def remove_provisioner_network() -> None:
    """Remove the shared docker network for provisioner containers."""
    print(f"Removing docker network '{PROVISIONER_NETWORK}'...")
    result = _run(f"docker network rm {PROVISIONER_NETWORK}")
    if result.returncode == 0:
        print(f"✓ Network '{PROVISIONER_NETWORK}' removed")
    else:
        print(
            "Could not remove network "
            f"'{PROVISIONER_NETWORK}'. "
            "It may not exist or is still in use."
        )


def _compose_gpu_opts() -> str:
    drivers = _get_installed_gpu_drivers()
    if "amdgpu" in drivers:
        return (
            "--device /dev/kfd --device /dev/dri "
            "--security-opt seccomp=unconfined "
        )
    if "nvidia" in drivers:
        return "--gpus all "
    return ""


def start_provisioner_api(
    *, port: int = None, restart: bool = True, mount_source_dir=False
) -> None:
    container_name = "ozwald-provisioner-api-arch"
    image_tag = "ozwald-provisioner-api-arch:latest"
    port = int(
        port
        if port is not None
        else os.environ.get("OZWALD_PROVISIONER_PORT", 8000)
    )

    # stop/remove on restart if exists
    exists = _run(
        f"docker ps -a --filter name={container_name} --format "
        + "'{{.Names}}'",
        capture=True,
    )
    if (exists.stdout or "").strip() == container_name:
        running = _run(
            f"docker ps --filter name={container_name} --format "
            + "'{{.Names}}'",
            capture=True,
        )
        if (running.stdout or "").strip() == container_name:
            if restart:
                print(f"Stopping container {container_name}...")
                _run(f"docker stop {container_name}")
                print(f"Removing container {container_name}...")
                _run(f"docker rm {container_name}")
                print(f"✓ Container {container_name} stopped and removed")
            else:
                print(f"Container {container_name} is already running")
                return
        else:
            print(f"Removing existing container {container_name}...")
            _run(f"docker rm {container_name}")
            print(f"✓ Container {container_name} removed")

    print(f"Creating and starting container {container_name} on port {port}...")
    gpu_opts = _compose_gpu_opts()
    src_dir = Path("src").absolute()

    # Config mount
    default_rel_or_abs = os.environ.get(
        "OZWALD_CONFIG", "dev/resources/settings.yml"
    )
    config_path = (
        default_rel_or_abs
        if str(default_rel_or_abs).startswith("/")
        else str(Path(__file__).resolve().parents[2] / default_rel_or_abs)
    )
    src_mount = ""
    if mount_source_dir:
        src_mount = f"-v {src_dir}:/app "
    config_mount = f"{src_mount} -v {config_path}:/etc/ozwald.yml "

    ensure_provisioner_network()
    system_key = os.environ.get("OZWALD_SYSTEM_KEY", "jenny8675")
    provisioner_name = os.environ.get(
        "DEFAULT_OZWALD_PROVISIONER",
        os.environ.get("OZWALD_PROVISIONER", "unconfigured"),
    )
    cmd = (
        f"docker run -d --name {container_name} "
        f"--network {PROVISIONER_NETWORK} "
        f"-p {port}:8000 "
        f"-e OZWALD_SYSTEM_KEY={system_key} "
        f"-e OZWALD_PROVISIONER={provisioner_name} "
        f"-e OZWALD_CONFIG=/etc/ozwald.yml "
        f"-e PROVISIONER_HOST={container_name} "
        f"{config_mount}{gpu_opts}{image_tag}"
    )
    _run(cmd, check=True)
    print(f"✓ Container {container_name} created and started on port {port}")


def stop_provisioner_api() -> None:
    container_name = "ozwald-provisioner-api-arch"
    running = _run(
        f"docker ps --filter name={container_name} --format '{{{{.Names}}}}'",
        capture=True,
    )
    if (running.stdout or "").strip() == container_name:
        print(f"Stopping container {container_name}...")
        _run(f"docker stop {container_name}")
        print(f"✓ Container {container_name} stopped")
    else:
        print(f"Container {container_name} is not running")


def _user_id() -> int:
    try:
        return os.getuid()
    except Exception:
        return 0


def _docker_group_id() -> Optional[int]:
    try:
        import grp

        return grp.getgrnam("docker").gr_gid  # type: ignore[attr-defined]
    except Exception:
        return None


def start_provisioner_backend(
    *, restart: bool = True, mount_source_dir=False
) -> None:
    container_name = "ozwald-provisioner-backend"
    image_tag = "ozwald-provisioner-backend:latest"

    exists = _run(
        f"docker ps -a --filter name={container_name} --format "
        + "'{{.Names}}'",
        capture=True,
    )
    if (exists.stdout or "").strip() == container_name:
        running = _run(
            f"docker ps --filter name={container_name} --format "
            + "'{{.Names}}'",
            capture=True,
        )
        if (running.stdout or "").strip() == container_name:
            if restart:
                print(f"Stopping container {container_name}...")
                _run(f"docker stop {container_name}")
                print(f"Removing container {container_name}...")
                _run(f"docker rm {container_name}")
                print(f"✓ Container {container_name} stopped and removed")
            else:
                print(f"Container {container_name} is already running")
                return
        else:
            print(f"Removing existing container {container_name}...")
            _run(f"docker rm {container_name}")
            print(f"✓ Container {container_name} removed")

    print(f"Creating and starting container {container_name}...")
    gpu_opts = _compose_gpu_opts()
    src_dir = Path("src").absolute()

    default_rel_or_abs = os.environ.get(
        "OZWALD_CONFIG", "dev/resources/settings.yml"
    )
    config_path = (
        default_rel_or_abs
        if str(default_rel_or_abs).startswith("/")
        else str(Path(__file__).resolve().parents[2] / default_rel_or_abs)
    )
    src_mount = ""
    if mount_source_dir:
        src_mount = f"-v {src_dir}:/app "
    config_mount = f"{src_mount} -v {config_path}:/etc/ozwald.yml "

    ensure_provisioner_network()
    system_key = os.environ.get("OZWALD_SYSTEM_KEY", "jenny8675")
    provisioner_name = os.environ.get(
        "DEFAULT_OZWALD_PROVISIONER",
        os.environ.get("OZWALD_PROVISIONER", "unconfigured"),
    )
    host_name = os.environ.get(
        "DEFAULT_OZWALD_HOST", os.environ.get("OZWALD_HOST", "localhost")
    )

    user_id = _user_id()
    docker_gid = _docker_group_id()
    user_flag = f"-u {user_id}:{docker_gid} " if docker_gid is not None else ""

    cmd = (
        f"docker run -d --name {container_name} "
        f"--network {PROVISIONER_NETWORK} "
        f"-e OZWALD_SYSTEM_KEY={system_key} "
        f"-e OZWALD_CONFIG=/etc/ozwald.yml "
        f"-e OZWALD_PROVISIONER={provisioner_name} "
        f"-e OZWALD_HOST={host_name} "
        f"-v /var/run/docker.sock:/var/run/docker.sock "
        f"{user_flag}{config_mount}{gpu_opts}{image_tag}"
    )
    _run(cmd, check=True)
    print(f"✓ Container {container_name} created and started")


def stop_provisioner_backend() -> None:
    container_name = "ozwald-provisioner-backend"
    running = _run(
        f"docker ps --filter name={container_name} --format '{{{{.Names}}}}'",
        capture=True,
    )
    if (running.stdout or "").strip() == container_name:
        print(f"Stopping container {container_name}...")
        _run(f"docker stop {container_name}")
        print(f"✓ Container {container_name} stopped")
    else:
        print(f"Container {container_name} is not running")


def start_provisioner_redis(*, port: int = None, restart: bool = True) -> None:
    container_name = "ozwald-provisioner-redis"
    image_tag = "redis:alpine"
    port = int(
        port
        if port is not None
        else os.environ.get("OZWALD_PROVISIONER_REDIS_PORT", 6479)
    )

    exists = _run(
        f"docker ps -a --filter name={container_name} --format "
        + "'{{.Names}}'",
        capture=True,
    )
    if (exists.stdout or "").strip() == container_name:
        running = _run(
            f"docker ps --filter name={container_name} --format "
            + "'{{.Names}}'",
            capture=True,
        )
        if (running.stdout or "").strip() == container_name:
            if restart:
                print(f"Stopping container {container_name}...")
                _run(f"docker stop {container_name}")
                print(f"Removing container {container_name}...")
                _run(f"docker rm {container_name}")
                print(f"✓ Container {container_name} stopped and removed")
            else:
                print(
                    f"Container {container_name} is already running on port "
                    f"{port}"
                )
                return
        else:
            print(f"Removing existing container {container_name}...")
            _run(f"docker rm {container_name}")
            print(f"✓ Container {container_name} removed")

    print(f"Creating and starting container {container_name} on port {port}...")
    ensure_provisioner_network()
    _run(
        f"docker run -d --name {container_name} "
        f"--network {PROVISIONER_NETWORK} "
        f"-p {port}:6379 {image_tag}",
        check=True,
    )
    print(f"✓ Container {container_name} created and started on port {port}")


def stop_provisioner_redis() -> None:
    container_name = "ozwald-provisioner-redis"
    running = _run(
        f"docker ps --filter name={container_name} --format '{{{{.Names}}}}'",
        capture=True,
    )
    if (running.stdout or "").strip() == container_name:
        print(f"Stopping container {container_name}...")
        _run(f"docker stop {container_name}")
        print(f"✓ Container {container_name} stopped")
    else:
        print(f"Container {container_name} is not running")


def build_containers(name: Optional[str] = None) -> None:
    dockerfiles_dir = Path("dockerfiles")
    if not dockerfiles_dir.exists():
        print(f"Error: {dockerfiles_dir} directory not found")
        return

    if name:
        dockerfile_path = dockerfiles_dir / f"Dockerfile.{name}"
        if not dockerfile_path.exists():
            print(f"Error: Dockerfile.{name} not found in {dockerfiles_dir}")
            print("\nAvailable Dockerfiles:")
            for df in sorted(dockerfiles_dir.glob("Dockerfile.*")):
                print(f"  - {df.name.replace('Dockerfile.', '')}")
            return
        dockerfiles = [dockerfile_path]
    else:
        dockerfiles = sorted(dockerfiles_dir.glob("Dockerfile.*"))
        if not dockerfiles:
            print(f"No Dockerfiles found in {dockerfiles_dir}")
            return

    print(f"\nBuilding {len(dockerfiles)} container(s)...\n")
    for dockerfile in dockerfiles:
        container_name = dockerfile.name.replace("Dockerfile.", "")
        image_tag = f"ozwald-{container_name}:latest"
        print("=" * 70)
        print(f"Building: {container_name}")
        print(f"Image tag: {image_tag}")
        print(f"Dockerfile: {dockerfile}")
        print("=" * 70)

        result = _run(f"docker build -f {dockerfile} -t {image_tag} .")
        if result.returncode == 0:
            print(f"\n✓ Successfully built {image_tag}\n")
        else:
            print(f"\n✗ Failed to build {image_tag}\n")

        # result = _run(f"docker tag {image_tag} localhost:5000/{image_tag}")
        # if result.returncode == 0:
        #     print(f"\n✓ Successfully tagged {image_tag}\n")
        # else:
        #     print(f"\n✗ Failed to tag {image_tag}\n")
        #
        # result = _run(f"docker push localhost:5000/{image_tag}")
        # if result.returncode == 0:
        #     print(f"\n✓ Successfully pushed {image_tag}\n")
        # else:
        #     print(f"\n✗ Failed to push {image_tag}\n")
    print("\nBuild complete!")
