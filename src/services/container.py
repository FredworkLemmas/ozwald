from __future__ import annotations

import subprocess
import threading
import time
from datetime import datetime
from typing import Any, ClassVar

from hosts.resources import HostResources
from orchestration.models import (
    EffectiveServiceDefinition,
    ServiceInformation,
    ServiceStatus,
)
from orchestration.provisioner import SystemProvisioner
from orchestration.service import BaseProvisionableService
from util.active_services_cache import ActiveServicesCache, WriteCollision
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
            )
        return self._effective_def

    # --- Lifecycle: start/stop container ---
    def start(self):
        """Start the service container."""
        active_services_cache = ActiveServicesCache(self._cache)

        # Get current active services from cache
        active_services = active_services_cache.get_services()

        # Find the service in the active services list
        current_service = None
        for service in active_services:
            if (
                service.name == self._service_info.name
                and service.service == self._service_info.service
                and service.profile == self._service_info.profile
            ):
                current_service = service
                break

        # Raise error if service is not found in active services
        if current_service is None:
            raise RuntimeError(
                f"Service {self._service_info.name} not found in"
                " active services",
            )

        # Raise error if service status is not set to starting
        if current_service.status != ServiceStatus.STARTING:
            raise RuntimeError(
                f"Service {self._service_info.name} status is "
                f"{current_service.status}, expected"
                f" {ServiceStatus.STARTING}",
            )

        # Record start initiation time and persist to cache before
        # starting container
        # Note: SystemProvisioner also sets start_initiated, but we do it
        # here as well to ensure it is set even if started manually.
        # However, we should be careful about race conditions.
        # updated_services = []
        # for service in active_services:
        #     if (
        #         service.name == self._service_info.name
        #         and service.service == self._service_info.service
        #         and service.profile == self._service_info.profile
        #     ):
        #         if service.info is None:
        #             service.info = {}
        #         service.info["start_initiated"] = datetime.now().isoformat()
        #     updated_services.append(service)

        # Save updated services to cache with retry to tolerate locking
        # self._set_services_with_retry(active_services_cache, updated_services)

        # Start the container in a separate thread
        def start_container():
            try:
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
                        "Error removing stale container: "
                        f"{type(e).__name__}({e})"
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
                        runner_logs_cache = RunnerLogsCache(
                            provisioner.get_cache()
                        )
                        for line in process.stdout:
                            if line:
                                logger.info(
                                    f"Container {container_name}: {line}"
                                )
                                runner_logs_cache.add_log_line(
                                    container_name,
                                    line.strip(),
                                )
                    except Exception as e:
                        logger.error(
                            f"Error in log_reader for {container_name}: {e}"
                        )
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
                    logger.error(
                        f"Failed to fetch container ID for {container_name}"
                    )

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

                            # Update service status in cache
                            updated_services = []
                            for service in active_services:
                                if (
                                    service.name == self._service_info.name
                                    and service.service
                                    == self._service_info.service
                                    and service.profile
                                    == self._service_info.profile
                                ):
                                    service.status = ServiceStatus.AVAILABLE
                                    if service.info is None:
                                        service.info = {}
                                    if container_id:
                                        service.info["container_id"] = (
                                            container_id
                                        )
                                    service.info["container_status"] = status
                                    if health != "none":
                                        service.info["container_health"] = (
                                            health
                                        )
                                    service.info["start_completed"] = (
                                        datetime.now().isoformat()
                                    )
                                updated_services.append(service)

                            self._set_services_with_retry(
                                active_services_cache,
                                updated_services,
                            )
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

                # Start streaming logs to Redis for historical access
                # self._stream_logs_to_redis(container_id)

                logger.error(
                    f"Container for service {self._service_info.name} did not"
                    " start within the expected time",
                )
            except Exception as e:
                # self._stream_logs_to_redis(container_id)
                logger.error(
                    "Unexpected error starting service "
                    f"{self._service_info.name}: {e}",
                )

        container_thread = threading.Thread(target=start_container, daemon=True)
        container_thread.start()

    def stop(self):
        """Stop the service container."""
        active_services_cache = ActiveServicesCache(self._cache)
        active_services = active_services_cache.get_services()

        # Locate current service in cache
        current_service = None
        for service in active_services:
            if (
                service.name == self._service_info.name
                and service.service == self._service_info.service
                and service.profile == self._service_info.profile
            ):
                current_service = service
                break

        if current_service is None:
            raise RuntimeError(
                f"Service {self._service_info.name} not found in"
                " active services",
            )

        if current_service.status != ServiceStatus.STOPPING:
            raise RuntimeError(
                f"Service {self._service_info.name} status is"
                f" {current_service.status}, expected"
                f" {ServiceStatus.STOPPING}",
            )

        def stop_container():
            try:
                # Determine container ID or name
                container_identifier = None
                if current_service.info and current_service.info.get(
                    "container_id",
                ):
                    container_identifier = current_service.info["container_id"]
                else:
                    container_identifier = self.get_container_name()

                # self._stream_logs_to_redis(container_identifier)
                # Give the streaming thread a moment to establish connection
                # before we remove the container
                # time.sleep(2)

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

                # Update cache: remove or mark stopped
                updated_services = []
                for service in active_services:
                    if (
                        service.name == self._service_info.name
                        and service.service == self._service_info.service
                        and service.profile == self._service_info.profile
                    ):
                        # Remove service from cache on stop completion
                        continue
                    updated_services.append(service)

                self._set_services_with_retry(
                    active_services_cache,
                    updated_services,
                )
            except Exception as e:
                logger.error(
                    "Unexpected error stopping service "
                    f"{self._service_info.name}: {e}",
                )

        container_thread = threading.Thread(target=stop_container, daemon=True)
        container_thread.start()

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
        return f"service-{self._service_info.name}"

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

    def get_container_start_command(self, image: str) -> list[str]:
        docker_cmd = ["docker", "run"]
        std_opts = self.get_container_options__standard()
        gpu_opts = self.get_container_options__gpu()
        port_opts = self.get_container_options__port()
        env_opts = self.get_container_options__environment()
        vol_opts = self.get_container_options__volume()
        cmd = (
            docker_cmd
            + std_opts
            + gpu_opts
            + port_opts
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

    # --- Cache helper used by lifecycle ---
    def _set_services_with_retry(
        self,
        active_services_cache: ActiveServicesCache,
        services,
    ):
        deadline = time.time() + 5.0
        attempt = 0
        while True:
            attempt += 1
            try:
                active_services_cache.set_services(services)
                return True
            except (WriteCollision, RuntimeError) as e:
                msg = str(e)
                if (
                    isinstance(e, RuntimeError)
                    and "Lock error" not in msg
                    and "lock" not in msg.lower()
                ):
                    logger.error(
                        "Non-lock runtime error while setting services: %s",
                        msg,
                    )
                    raise
                if time.time() >= deadline:
                    logger.error(
                        "Failed to update active services after %d attempts:"
                        " %s",
                        attempt,
                        msg,
                    )
                    return False
                time.sleep(0.5)
            except Exception as e:
                logger.error(
                    "Unexpected error while setting services: %s",
                    str(e),
                )
                raise
