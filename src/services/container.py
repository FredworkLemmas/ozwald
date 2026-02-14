from __future__ import annotations

import subprocess
import threading
import time
from typing import Any, ClassVar

from hosts.resources import HostResources
from orchestration.models import (
    EffectiveServiceDefinition,
    ServiceInformation,
)
from orchestration.provisioner import SystemProvisioner
from orchestration.service import BaseProvisionableService
from util.logger import get_logger
from util.runner_logs_cache import RunnerLogsCache

CONTAINER_HEALTHCHECK_TIMEOUT = 300

logger = get_logger(__name__)


class ContainerService(BaseProvisionableService):
    service_type: ClassVar[str] = "container"

    # Container-specific configuration (class defaults, overridable per
    # instance via __init__ kwargs)
    container_image: str | None = None
    container_port__internal: int | None = None
    container_port__external: int | None = None
    container_environment: dict | None = None
    container_volumes: list[str] | None = None

    def __init__(
        self,
        service_info: ServiceInformation,
        *,
        container_environment: dict | None = None,
        container_volumes: list[str] | None = None,
        container_port__internal: int | None = None,
        container_port__external: int | None = None,
    ):
        super().__init__(service_info)

        # Apply per-instance overrides for container configuration
        if container_environment is not None:
            self.container_environment = container_environment
        if container_volumes is not None:
            self.container_volumes = container_volumes
        if container_port__internal is not None:
            self.container_port__internal = container_port__internal
        if container_port__external is not None:
            self.container_port__external = container_port__external

    # --- Generic helpers used by container logic ---
    def get_variety(self) -> str | None:
        return getattr(self._service_info, "variety", None)

    @property
    def effective_definition(self) -> EffectiveServiceDefinition:
        if not hasattr(self, "_effective_def") or self._effective_def is None:
            from config.reader import SystemConfigReader

            reader = SystemConfigReader.singleton()
            si = self.get_service_information()
            self._effective_def = reader.get_effective_service_definition(
                si.service,
                si.profile,
                si.variety,
                realm=si.realm,
            )
        return self._effective_def

    @staticmethod
    def effective_network_name(network: Any) -> str:
        """Return the effective network name for Docker."""
        # Use Any for network to avoid circular import if needed,
        # but Network is already imported in orchestration.models
        return f"oznet--{network.realm}--{network.name}"

    @classmethod
    def init_service(cls):
        """Initialize the container service by creating all defined networks."""
        from config.reader import SystemConfigReader

        reader = SystemConfigReader.singleton()
        for network in reader.defined_networks:
            eff_name = cls.effective_network_name(network)
            logger.info(f"Ensuring network {eff_name} exists")
            subprocess.run(
                ["docker", "network", "create", eff_name],
                check=False,
                capture_output=True,
                text=True,
            )

    # --- Lifecycle: start/stop container ---
    def start(self):
        """Start the service container."""
        # Get the container image
        image = self.get_container_image()
        if not image:
            logger.error(
                "No container image specified for service"
                f" {self._service_info.name}",
            )
            return

        container_name = self.get_container_name()

        # Remove any stale container with the same name to avoid
        # name conflicts on repeated start attempts
        try:
            subprocess.run(
                ["docker", "rm", "-f", container_name],
                check=False,
                capture_output=True,
                text=True,
            )
        except Exception as e:
            # log as info with exception type and msg
            logger.info(
                f"Error removing stale container: {type(e).__name__}({e})"
            )

        # compute the container start command
        cmd = self.get_container_start_command(image)

        logger.info(
            "Starting container for service "
            f"{self._service_info.name} with command: "
            f'"{" ".join(cmd)}"',
        )

        # start the container in foreground using Popen
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )

        # Real-time Log Streaming to Redis
        def log_reader():
            try:
                provisioner = SystemProvisioner.singleton()
                runner_logs_cache = RunnerLogsCache(provisioner.get_cache())
                for line in process.stdout:
                    if line:
                        logger.info(f"Container {container_name}: {line}")
                        runner_logs_cache.add_log_line(
                            container_name,
                            line.strip(),
                        )
            except Exception as e:
                logger.error(f"Error in log_reader for {container_name}: {e}")
            finally:
                if process.stdout:
                    process.stdout.close()

        log_thread = threading.Thread(target=log_reader, daemon=True)
        log_thread.start()

        # Give it a moment to actually start or fail
        time.sleep(1)
        if process.poll() is not None:
            logger.error(
                f"Container process for {container_name} exited "
                f"immediately with code {process.returncode}"
            )
            return

        # Explicitly fetch the container_id
        container_id = None
        for _ in range(10):
            id_result = subprocess.run(
                [
                    "docker",
                    "inspect",
                    "--format",
                    "{{.Id}}",
                    container_name,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if id_result.returncode == 0:
                container_id = id_result.stdout.strip()
                break
            time.sleep(0.5)

        if not container_id:
            logger.error(f"Failed to fetch container ID for {container_name}")

        # Wait for container to be running and healthy (if it has a
        # healthcheck)
        max_wait_time = CONTAINER_HEALTHCHECK_TIMEOUT  # seconds
        wait_interval = 1  # seconds
        elapsed_time = 0

        logger.info(
            f"elapsed time: {elapsed_time}, "
            f"max wait time: {max_wait_time}, "
            f"wait interval: {wait_interval}"
        )

        while elapsed_time < max_wait_time:
            # Check if container is running and its health status
            # Using container_name instead of container_id per plan
            check_cmd = [
                "docker",
                "inspect",
                (
                    "--format={{.State.Status}} {{.State.Running}} "
                    "{{if .State.Health}}{{.State.Health.Status}}"
                    "{{else}}none{{end}}"
                ),
                container_name,
            ]
            check_result = subprocess.run(
                check_cmd,
                check=False,
                capture_output=True,
                text=True,
            )

            if check_result.returncode == 0:
                output = check_result.stdout.strip()
                parts = output.split()
                status = ""
                running = ""
                health = "none"

                if len(parts) == 3:
                    status, running, health = parts
                elif len(parts) == 2:
                    running, health = parts
                elif len(parts) == 1:
                    running = parts[0]

                # Container is considered available if it's running
                # and not in the 'starting' health state.
                if running == "true" and health != "starting":
                    logger.info(
                        f"Container for service "
                        f"{self._service_info.name} is now "
                        f"{status} and {health}",
                    )

                    # Update local service info
                    if self._service_info.info is None:
                        self._service_info.info = {}
                    if container_id:
                        self._service_info.info["container_id"] = container_id
                    self._service_info.info["container_status"] = status
                    if health != "none":
                        self._service_info.info["container_health"] = health

                    # Connect to additional networks if defined
                    networks = self.effective_definition.networks
                    if len(networks) > 1:
                        target_id = container_id or container_name
                        for network_name in networks[1:]:
                            eff_net_name = self._get_effective_network_name(
                                network_name
                            )
                            connect_cmd = [
                                "docker",
                                "network",
                                "connect",
                                eff_net_name,
                                target_id,
                            ]
                            logger.info(
                                f"Connecting container {target_id} to "
                                f"network {eff_net_name}",
                            )
                            subprocess.run(connect_cmd, check=True)
                    return

                if running != "true" and process.poll() is not None:
                    logger.error(
                        f"Container for service "
                        f"{self._service_info.name} stopped "
                        "unexpectedly",
                    )
                    return

            time.sleep(wait_interval)
            elapsed_time += wait_interval
            logger.info(
                f"elapsed time: {elapsed_time}, "
                f"max wait time: {max_wait_time}, "
                f"wait interval: {wait_interval}"
            )

        logger.error(
            f"Container for service {self._service_info.name} did not"
            " start within the expected time"
        )

    def stop(self):
        """Stop the service container."""
        # Determine container ID or name
        container_identifier = None
        if self._service_info.info and self._service_info.info.get(
            "container_id",
        ):
            container_identifier = self._service_info.info["container_id"]
        else:
            container_identifier = self.get_container_name()

        try:
            stop_cmd = ["docker", "rm", "-f", container_identifier]
            result = subprocess.run(
                stop_cmd,
                check=False,
                capture_output=True,
                text=True,
            )

            if result.returncode == 0:
                logger.info(
                    f"Container for service {self._service_info.name}"
                    " stopped and removed successfully",
                )
            else:
                logger.warning(
                    f"Failed to stop/remove container for service"
                    f" {self._service_info.name}: {result.stderr}",
                )
        except Exception as e:
            logger.error(
                "Unexpected error stopping service "
                f"{self._service_info.name}: {e}",
            )

    # --- Container configuration accessors and options builders ---
    def get_container_image(self):
        if self.container_image:
            return self.container_image
        try:
            return self.effective_definition.image
        except Exception:
            return ""

    def get_effective_depends_on(self) -> list[str]:
        try:
            return self.effective_definition.depends_on
        except Exception:
            return []

    def get_effective_command(self) -> Any:
        try:
            return self.effective_definition.command
        except Exception:
            return None

    def get_effective_entrypoint(self) -> Any:
        try:
            return self.effective_definition.entrypoint
        except Exception:
            return None

    def get_effective_env_file(self) -> list[str]:
        try:
            return self.effective_definition.env_file
        except Exception:
            return []

    def get_container_name(self):
        return f"ozsvc--{self._service_info.realm}--{self._service_info.name}"

    def get_container_options__standard(self) -> list[str]:
        return ["--name", self.get_container_name()]

    def get_container_options__gpu(self) -> list[str]:
        gpu_opts = []
        env = self.get_container_environment() or {}
        gpu_flag = str(env.get("GPU", "")).lower()
        if gpu_flag not in ("1", "true", "yes"):
            return gpu_opts
        installed_gpu_drivers = HostResources.installed_gpu_drivers()
        if "amdgpu" in installed_gpu_drivers:
            gpu_opts += [
                "--device",
                "/dev/kfd",
                "--device",
                "/dev/dri",
                "--security-opt",
                "seccomp=unconfined",
            ]
        if "nvidia" in installed_gpu_drivers:
            gpu_opts += ["--gpus", "all"]
        return gpu_opts

    def get_container_options__port(self) -> list[str]:
        port_opts = []
        internal_port = self.get_internal_container_port()
        external_port = self.get_external_container_port()
        if external_port is not None and internal_port is not None:
            port_opts = ["-p", f"{external_port}:{internal_port}"]
        return port_opts

    def get_container_options__volume(self) -> list[str]:
        volume_opts = []
        container_vols = self.get_container_volumes()
        if container_vols:
            for volume in container_vols:
                volume_opts.extend(["-v", volume])
        return volume_opts

    def get_container_options__environment(self) -> list[str]:
        env_opts = []
        container_env = self.get_container_environment()
        if container_env:
            for key, value in container_env.items():
                env_opts.extend(["-e", f"{key}={value}"])
        return env_opts

    def get_container_options__network(self) -> list[str]:
        networks = self.effective_definition.networks
        if networks:
            eff_net_name = self._get_effective_network_name(networks[0])
            return ["--network", eff_net_name]
        return []

    def _get_effective_network_name(self, network_name: str) -> str:
        from config.reader import SystemConfigReader

        reader = SystemConfigReader.singleton()
        network = reader.get_network_by_name(
            network_name,
            self._service_info.realm,
        )
        if network:
            return self.effective_network_name(network)
        # Fallback to namespaced oznet name if not explicitly in config
        return f"oznet--{self._service_info.realm}--{network_name}"

    def get_container_start_command(self, image: str) -> list[str]:
        docker_cmd = ["docker", "run"]
        std_opts = self.get_container_options__standard()
        gpu_opts = self.get_container_options__gpu()
        port_opts = self.get_container_options__port()
        net_opts = self.get_container_options__network()
        env_opts = self.get_container_options__environment()
        vol_opts = self.get_container_options__volume()
        cmd = (
            docker_cmd
            + std_opts
            + gpu_opts
            + port_opts
            + net_opts
            + env_opts
            + vol_opts
            + [f"ozwald-{image}"]
        )
        logger.info("Container start command: %s", " ".join(cmd))
        return cmd

    # --- Accessors for container configuration ---
    def get_container_environment(self) -> dict | None:
        if self.container_environment is not None:
            return self.container_environment
        try:
            return self.effective_definition.environment
        except Exception:
            return None

    def get_container_volumes(self) -> list[str] | None:
        if self.container_volumes is not None:
            return self.container_volumes
        # Resolve volumes with profile/variety-aware merge
        try:
            return self.effective_definition.volumes
        except Exception:
            return None

    def get_internal_container_port(self) -> int | None:
        return self.container_port__internal

    def get_external_container_port(self) -> int | None:
        return self.container_port__external
