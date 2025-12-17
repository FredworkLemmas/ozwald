from __future__ import annotations

import subprocess
import threading
import time
from datetime import datetime
from typing import Any, ClassVar

from hosts.resources import HostResources
from orchestration.models import ServiceInformation, ServiceStatus
from orchestration.service import BaseProvisionableService
from util.active_services_cache import ActiveServicesCache, WriteCollision
from util.logger import get_logger

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
                " active services"
            )

        # Raise error if service status is not set to starting
        if current_service.status != ServiceStatus.STARTING:
            raise RuntimeError(
                f"Service {self._service_info.name} status is "
                f"{current_service.status}, expected"
                f" {ServiceStatus.STARTING}"
            )

        # Record start initiation time and persist to cache before
        # starting container
        updated_services = []
        for service in active_services:
            if (
                service.name == self._service_info.name
                and service.service == self._service_info.service
                and service.profile == self._service_info.profile
            ):
                if service.info is None:
                    service.info = {}
                service.info["start_initiated"] = datetime.now().isoformat()
            updated_services.append(service)

        # Save updated services to cache with retry to tolerate locking
        self._set_services_with_retry(active_services_cache, updated_services)

        # Start the container in a separate thread
        def start_container():
            try:
                # Get the container image
                image = self.get_container_image()
                if not image:
                    logger.error(
                        "No container image specified for service"
                        "f' {self._service_info.name}"
                    )
                    return

                # compute the container start command
                cmd = self.get_container_start_command(image)

                logger.info(
                    "Starting container for service "
                    f"{self._service_info.name} with command: "
                    f'"{" ".join(cmd)}"'
                )

                # start the container
                result = subprocess.run(
                    cmd, capture_output=True, text=True, check=True
                )
                container_id = result.stdout.strip()

                # Wait for container to be running
                max_wait_time = 30  # seconds
                wait_interval = 1  # seconds
                elapsed_time = 0

                while elapsed_time < max_wait_time:
                    # Check if container is running
                    check_cmd = [
                        "docker",
                        "inspect",
                        "--format={{.State.Running}}",
                        container_id,
                    ]
                    check_result = subprocess.run(
                        check_cmd, capture_output=True, text=True
                    )

                    if (
                        check_result.returncode == 0
                        and check_result.stdout.strip() == "true"
                    ):
                        logger.info(
                            f"Container for service {self._service_info.name}"
                            " is now running"
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
                                service.info["container_id"] = container_id
                                service.info["start_completed"] = (
                                    datetime.now().isoformat()
                                )
                            updated_services.append(service)

                        self._set_services_with_retry(
                            active_services_cache, updated_services
                        )
                        return

                    time.sleep(wait_interval)
                    elapsed_time += wait_interval

                logger.error(
                    f"Container for service {self._service_info.name} did not"
                    " start within the expected time"
                )
            except subprocess.CalledProcessError as e:
                logger.error(
                    f"Failed to start container for service"
                    f" {self._service_info.name}: {e}"
                )
            except Exception as e:
                logger.error(
                    "Unexpected error starting service "
                    f"{self._service_info.name}: {e}"
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
                " active services"
            )

        if current_service.status != ServiceStatus.STOPPING:
            raise RuntimeError(
                f"Service {self._service_info.name} status is"
                f" {current_service.status}, expected"
                f" {ServiceStatus.STOPPING}"
            )

        def stop_container():
            try:
                # Determine container ID or name
                container_identifier = None
                if current_service.info and current_service.info.get(
                    "container_id"
                ):
                    container_identifier = current_service.info["container_id"]
                else:
                    container_identifier = self.get_container_name()

                stop_cmd = ["docker", "rm", "-f", container_identifier]
                result = subprocess.run(
                    stop_cmd, capture_output=True, text=True
                )

                if result.returncode == 0:
                    logger.info(
                        f"Container for service {self._service_info.name}"
                        " stopped and removed successfully"
                    )
                else:
                    logger.warning(
                        f"Failed to stop/remove container for service"
                        f" {self._service_info.name}: {result.stderr}"
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
                    active_services_cache, updated_services
                )
            except Exception as e:
                logger.error(
                    "Unexpected error stopping service "
                    f"{self._service_info.name}: {e}"
                )

        container_thread = threading.Thread(target=stop_container, daemon=True)
        container_thread.start()

    # --- Effective configuration helpers ---
    def _resolve_effective_fields(
        self,
        service_def,
        profile_name: str | None,
        variety_name: str | None,
    ) -> dict[str, Any]:
        # Delegate to base get_service_definition but keep logic local
        base_env = service_def.environment or {}
        base_depends_on = service_def.depends_on or []
        base_command = service_def.command
        base_entrypoint = service_def.entrypoint
        base_env_file = service_def.env_file
        base_image = service_def.image
        base_vols = list(service_def.volumes or [])

        v = None
        if variety_name:
            try:
                v = (service_def.varieties or {}).get(variety_name)
            except Exception:
                v = None

        v_env = (v.environment if v else None) or {}
        v_depends_on = (v.depends_on if v else None) or []
        v_command = v.command if v else None
        v_entrypoint = v.entrypoint if v else None
        v_env_file = (v.env_file if v else None) or None
        v_image = (v.image if v else None) or None
        v_vols = list(getattr(v, "volumes", []) or [])

        p = None
        if profile_name:
            try:
                p = (service_def.profiles or {}).get(profile_name)
            except Exception:
                p = None

        p_env = (p.environment if p else None) or {}
        p_depends_on = (p.depends_on if p else None) or []
        p_command = p.command if p else None
        p_entrypoint = p.entrypoint if p else None
        p_env_file = (p.env_file if p else None) or None
        p_image = (p.image if p else None) or None
        p_vols = list(getattr(p, "volumes", []) or [])

        merged_env = {**base_env, **v_env, **p_env}

        def _target_of(vol_spec: str) -> str:
            try:
                # vol_spec format examples:
                #   /host:/ctr:ro
                #   name:/ctr:rw
                #   /host:/ctr (no mode)
                _host, rest = vol_spec.split(":", 1)
                target = rest.split(":", 1)[0]
                return target
            except Exception:
                return ""

        def _merge_volumes(
            base_list: list[str], var_list: list[str], prof_list: list[str]
        ) -> list[str]:
            order: list[str] = []  # target order
            by_target: dict[str, str] = {}

            def add_many(lst: list[str]):
                for spec in lst:
                    t = _target_of(spec)
                    if not t:
                        continue
                    if t in by_target:
                        # replace, keep position
                        by_target[t] = spec
                    else:
                        by_target[t] = spec
                        order.append(t)

            add_many(base_list)
            add_many(v_vols)
            add_many(prof_list)
            return [by_target[t] for t in order]

        merged_vols = _merge_volumes(base_vols, v_vols, p_vols)

        def choose(*vals):
            for val in vals:
                if isinstance(val, str):
                    if val.strip():
                        return val
                elif isinstance(val, (list, tuple)):
                    if len(val) > 0:
                        return list(val)
                elif val is not None:
                    return val
            return None

        effective = {
            "environment": merged_env,
            "depends_on": choose(p_depends_on, v_depends_on, base_depends_on)
            or [],
            "command": choose(p_command, v_command, base_command),
            "entrypoint": choose(p_entrypoint, v_entrypoint, base_entrypoint),
            "env_file": choose(p_env_file, v_env_file, base_env_file) or [],
            "image": choose(p_image, v_image, base_image) or "",
            "volumes": merged_vols,
        }
        return effective

    def _get_effective_environment(
        self, service_info: ServiceInformation
    ) -> dict:
        service_def = self.get_service_definition()
        variety = self.get_variety()
        resolved = self._resolve_effective_fields(
            service_def, service_info.profile, variety
        )
        return resolved.get("environment", {}) or {}

    # --- Container configuration accessors and options builders ---
    def get_container_image(self):
        if self.container_image:
            return self.container_image
        try:
            service_info = self.get_service_information()
            service_def = self.get_service_definition()
            resolved = self._resolve_effective_fields(
                service_def, service_info.profile, service_info.variety
            )
            return resolved.get("image") or ""
        except Exception:
            return ""

    def get_effective_depends_on(self) -> list[str]:
        try:
            si = self.get_service_information()
            sd = self.get_service_definition()
            resolved = self._resolve_effective_fields(
                sd, si.profile, si.variety
            )
            return resolved.get("depends_on") or []
        except Exception:
            return []

    def get_effective_command(self) -> Any:
        try:
            si = self.get_service_information()
            sd = self.get_service_definition()
            resolved = self._resolve_effective_fields(
                sd, si.profile, si.variety
            )
            return resolved.get("command")
        except Exception:
            return None

    def get_effective_entrypoint(self) -> Any:
        try:
            si = self.get_service_information()
            sd = self.get_service_definition()
            resolved = self._resolve_effective_fields(
                sd, si.profile, si.variety
            )
            return resolved.get("entrypoint")
        except Exception:
            return None

    def get_effective_env_file(self) -> list[str]:
        try:
            si = self.get_service_information()
            sd = self.get_service_definition()
            resolved = self._resolve_effective_fields(
                sd, si.profile, si.variety
            )
            return resolved.get("env_file") or []
        except Exception:
            return []

    def get_container_name(self):
        return f"service-{self._service_info.name}"

    def get_container_options__standard(self) -> list[str]:
        return ["-d", "--rm", "--name", self.get_container_name()]

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
            return self._get_effective_environment(
                self.get_service_information()
            )
        except Exception:
            return None

    def get_container_volumes(self) -> list[str] | None:
        if self.container_volumes is not None:
            return self.container_volumes
        # Resolve volumes with profile/variety-aware merge
        try:
            si = self.get_service_information()
            sd = self.get_service_definition()
            resolved = self._resolve_effective_fields(
                sd, si.profile, si.variety
            )
            return resolved.get("volumes") or []
        except Exception:
            return None

    def get_internal_container_port(self) -> int | None:
        return self.container_port__internal

    def get_external_container_port(self) -> int | None:
        return self.container_port__external

    # --- Cache helper used by lifecycle ---
    def _set_services_with_retry(
        self, active_services_cache: ActiveServicesCache, services
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
                    "Unexpected error while setting services: %s", str(e)
                )
                raise
