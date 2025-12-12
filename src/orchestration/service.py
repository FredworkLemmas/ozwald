import importlib
import inspect
import os
import pkgutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from typing import Any, ClassVar, Optional, Type

from config.reader import SystemConfigReader
from hosts.resources import HostResources
from orchestration.models import (
    Cache,
    Service,
    ServiceDefinition,
    ServiceInformation,
    ServiceStatus,
)
from util.active_services_cache import ActiveServicesCache, WriteCollision
from util.logger import get_logger

logger = get_logger(__name__)


class BaseProvisionableService(Service):
    _cache: Cache = None
    _service_info: Optional[ServiceInformation] = None
    service_type: ClassVar[str]
    container_image: Optional[str] = None
    container_port__internal: Optional[int] = None
    container_port__external: Optional[int] = None
    container_environment: Optional[dict] = None
    container_volumes: Optional[list[str]] = None

    # Internal service registry (lazy-initialized). Mark as ClassVar so Pydantic
    # does not wrap these as ModelPrivateAttr, which caused runtime errors like:
    # "ModelPrivateAttr object has no attribute 'get'" when accessing as a dict.
    _service_registry: ClassVar[
        Optional[dict[str, Type["BaseProvisionableService"]]]
    ] = None
    _service_registry_lock: ClassVar[threading.Lock] = threading.Lock()

    def __init__(
        self,
        service_info: ServiceInformation,
        *,
        container_environment: Optional[dict] = None,
        container_volumes: Optional[list[str]] = None,
        container_port__internal: Optional[int] = None,
        container_port__external: Optional[int] = None,
    ):
        # Preserve any incoming parameters (e.g., selected variety) if present
        # on the ServiceInformation object; otherwise default to empty dict.
        incoming_params = getattr(service_info, "parameters", None) or {}

        super().__init__(
            name=service_info.name,
            service_name=service_info.service,
            host=os.environ["OZWALD_HOST"],
            # Runtime-level parameters only; definition no longer supplies
            # generic parameters. Keep empty unless caller overrides later.
            parameters=incoming_params,
            profile=service_info.profile,
        )
        from orchestration.provisioner import SystemProvisioner

        self._cache = SystemProvisioner.singleton().get_cache()
        self._service_info = service_info

        # Allow callers to override container settings at instantiation time.
        # If a value isn't provided, leave the instance attribute unset so that
        # any subclass-defined class attribute remains effective.
        if container_environment is not None:
            self.container_environment = container_environment
        if container_volumes is not None:
            self.container_volumes = container_volumes
        if container_port__internal is not None:
            self.container_port__internal = container_port__internal
        if container_port__external is not None:
            self.container_port__external = container_port__external

    def get_variety(self) -> str | None:
        return getattr(self._service_info, "variety", None)

    def start(self):
        """Start the service."""
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
                f"{current_service.status}, expected {ServiceStatus.STARTING}"
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
                                # Update status to running
                                service.status = ServiceStatus.AVAILABLE
                                if service.info is None:
                                    service.info = {}
                                service.info["container_id"] = container_id
                            updated_services.append(service)

                        # Save updated services to cache with retry to tolerate
                        # locking
                        if self._set_services_with_retry(
                            active_services_cache, updated_services
                        ):
                            return
                        else:
                            # If we failed to update the cache, we still return;
                            # error already logged by helper
                            return

                    time.sleep(wait_interval)
                    elapsed_time += wait_interval

                # If we get here, container didn't start properly
                logger.error(
                    f"Container for service {self._service_info.name} "
                    f"failed to start within {max_wait_time} seconds"
                )

            except subprocess.CalledProcessError as e:
                logger.error(
                    "Failed to start container for service "
                    f"{self._service_info.name}: {e}"
                )
            except Exception as e:
                logger.error(
                    "Unexpected error starting service "
                    f"{self._service_info.name}: {e}"
                )

        # Start the container in a separate thread
        container_thread = threading.Thread(target=start_container, daemon=True)
        container_thread.start()

    def stop(self):
        """Stop the service."""
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
                "Service "
                f"{self._service_info.name} not found in active services"
            )

        # Raise error if service status is not set to stopping
        if current_service.status != ServiceStatus.STOPPING:
            raise RuntimeError(
                f"Service {self._service_info.name} status is "
                f"{current_service.status}, expected {ServiceStatus.STOPPING}"
            )

        # Record stop initiation time and persist to cache before stopping
        # container
        updated_services = []
        for service in active_services:
            if (
                service.name == self._service_info.name
                and service.service == self._service_info.service
                and service.profile == self._service_info.profile
            ):
                if service.info is None:
                    service.info = {}
                service.info["stop_initiated"] = datetime.now().isoformat()
            updated_services.append(service)

        # Save updated services to cache with retry to tolerate locking
        self._set_services_with_retry(active_services_cache, updated_services)

        # Stop the container in a separate thread
        def stop_container():
            try:
                # Get container ID from service info
                container_id = None
                if (
                    current_service.info
                    and "container_id" in current_service.info
                ):
                    container_id = current_service.info["container_id"]

                if not container_id:
                    logger.warning(
                        "No container found for service "
                        f"{self._service_info.name}"
                    )
                else:
                    # Stop the docker container
                    stop_cmd = ["docker", "stop", container_id]
                    stop_result = subprocess.run(
                        stop_cmd, capture_output=True, text=True
                    )

                    if stop_result.returncode != 0:
                        logger.warning(
                            "Failed to stop container "
                            f"{container_id}: {stop_result.stderr}"
                        )
                    else:
                        logger.info(
                            "Container "
                            f"{container_id} for service "
                            f"{self._service_info.name} stopped"
                        )

                    # Wait for container to be stopped and remove it
                    max_wait_time = 30  # seconds
                    wait_interval = 1  # seconds
                    elapsed_time = 0

                    while elapsed_time < max_wait_time:
                        # Check if container is stopped
                        check_cmd = [
                            "docker",
                            "inspect",
                            "--format={{.State.Running}}",
                            container_id,
                        ]
                        check_result = subprocess.run(
                            check_cmd, capture_output=True, text=True
                        )

                        # If container is not running or doesn't exist anymore,
                        # we're done
                        if (
                            check_result.returncode != 0
                            or check_result.stdout.strip() == "false"
                        ):
                            logger.info(
                                "Container for service "
                                f"{self._service_info.name} is now stopped"
                            )

                            # Remove the container
                            remove_cmd = ["docker", "rm", container_id]
                            remove_result = subprocess.run(
                                remove_cmd, capture_output=True, text=True
                            )
                            if remove_result.returncode == 0:
                                logger.info(f"Container {container_id} removed")
                            else:
                                logger.warning(
                                    "Failed to remove container "
                                    f"{container_id}: "
                                    f"{remove_result.stderr}"
                                )
                            break

                        time.sleep(wait_interval)
                        elapsed_time += wait_interval

                    if elapsed_time >= max_wait_time:
                        logger.warning(
                            "Container for service "
                            f"{self._service_info.name} did not stop "
                            "within "
                            f"{max_wait_time} seconds"
                        )

                # Remove service from active services cache
                updated_services = []
                for service in active_services:
                    if not (
                        service.name == self._service_info.name
                        and service.service == self._service_info.service
                        and service.profile == self._service_info.profile
                    ):
                        updated_services.append(service)

                # Save updated services to cache (without the stopped service)
                # with retry to tolerate locking
                if self._set_services_with_retry(
                    active_services_cache, updated_services
                ):
                    logger.info(
                        "Service "
                        f"{self._service_info.name} removed from active "
                        "services cache"
                    )
                else:
                    # Error already logged by helper
                    pass

            except subprocess.CalledProcessError as e:
                logger.error(
                    "Failed to stop container for service "
                    f"{self._service_info.name}: {e}"
                )
            except Exception as e:
                logger.error(
                    "Unexpected error stopping service "
                    f"{self._service_info.name}: {e}"
                )

        # Stop the container in a separate thread
        container_thread = threading.Thread(target=stop_container, daemon=True)
        container_thread.start()

    def get_service_information(self) -> ServiceInformation:
        """Return the service information."""
        return self._service_info

    def _get_service_definition(
        self, service_info: ServiceInformation
    ) -> ServiceDefinition:
        """Return the service definition."""
        config_reader = SystemConfigReader.singleton()
        service_def = config_reader.get_service_by_name(service_info.service)
        if service_def is None:
            raise RuntimeError(
                f"Service definition for service {service_info.service}"
                " not found"
            )
        return service_def

    def get_service_definition(self) -> ServiceDefinition:
        return self._get_service_definition(self.get_service_information())

    def _resolve_effective_fields(
        self,
        service_def: ServiceDefinition,
        profile_name: Optional[str],
        variety_name: Optional[str],
    ) -> dict[str, Any]:
        """Resolve effective docker-compose–like fields with precedence.

        Precedence order for overrides:
          base service < variety < profile

        Environment is merged (service | variety | profile) with later layers
        taking priority per key. For list/str fields, the first non-empty value
        by precedence is selected.
        """
        # Base fields
        base_env = service_def.environment or {}
        base_depends_on = service_def.depends_on or []
        base_command = service_def.command
        base_entrypoint = service_def.entrypoint
        base_env_file = service_def.env_file
        base_image = service_def.image

        # Variety layer (optional)
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

        # Profile layer (optional)
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

        # Merge environment service < variety < profile
        merged_env = {**base_env, **v_env, **p_env}

        # Choose attributes by precedence: profile > variety > base
        def choose(*vals):
            for val in vals:
                # Accept non-empty strings, non-empty lists, or any truthy value
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
        }
        return effective

    def _get_effective_environment(
        self, service_info: ServiceInformation
    ) -> dict[str, Any]:
        """
        Return merged environment considering service, variety, and profile.
        """
        service_def = self._get_service_definition(service_info)
        variety = self.get_variety()
        resolved = self._resolve_effective_fields(
            service_def, service_info.profile, variety
        )
        return resolved.get("environment", {}) or {}

    def get_container_image(self):
        # Instance override wins if provided
        if self.container_image:
            return self.container_image
        # Otherwise compute effective image from definition/variety/profile
        try:
            service_info = self.get_service_information()
            service_def = self._get_service_definition(service_info)
            resolved = self._resolve_effective_fields(
                service_def, service_info.profile, service_info.variety
            )
            return resolved.get("image") or ""
        except Exception:
            return ""

    # Optional getters for other effective fields in case subclasses need them
    def get_effective_depends_on(self) -> list[str]:
        try:
            si = self.get_service_information()
            sd = self._get_service_definition(si)
            resolved = self._resolve_effective_fields(
                sd, si.profile, si.variety
            )
            return resolved.get("depends_on") or []
        except Exception:
            return []

    def get_effective_command(self) -> Any:
        try:
            si = self.get_service_information()
            sd = self._get_service_definition(si)
            resolved = self._resolve_effective_fields(
                sd, si.profile, si.variety
            )
            return resolved.get("command")
        except Exception:
            return None

    def get_effective_entrypoint(self) -> Any:
        try:
            si = self.get_service_information()
            sd = self._get_service_definition(si)
            resolved = self._resolve_effective_fields(
                sd, si.profile, si.variety
            )
            return resolved.get("entrypoint")
        except Exception:
            return None

    def get_effective_env_file(self) -> list[str]:
        try:
            si = self.get_service_information()
            sd = self._get_service_definition(si)
            resolved = self._resolve_effective_fields(
                sd, si.profile, si.variety
            )
            return resolved.get("env_file") or []
        except Exception:
            return []

    def get_container_name(self):
        return f"service-{self._service_info.name}"

    def get_container_options__standard(self) -> list[str]:
        """Returns the docker run command for the container."""
        return ["-d", "--rm", "--name", self.get_container_name()]

    def get_container_options__gpu(self) -> list[str]:
        gpu_opts = []

        # Use environment flag GPU=true/1/yes to request GPU
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
        """Returns the docker run command for the container."""
        # def base cmd and std opts
        docker_cmd = ["docker", "run"]
        std_opts = self.get_container_options__standard()

        # compute gpu opts
        gpu_opts = self.get_container_options__gpu()

        # add port opts
        port_opts = self.get_container_options__port()

        # add env opts
        env_opts = self.get_container_options__environment()

        # add vol opts
        vol_opts = self.get_container_options__volume()

        # assemble cmd and return
        cmd = (
            docker_cmd
            + std_opts
            + gpu_opts
            + port_opts
            + env_opts
            + vol_opts
            + [f"ozwald-{image}"]
        )
        return cmd

    # --- Accessors for container configuration ---
    def get_container_environment(self) -> Optional[dict]:
        """Return container environment mapping for docker run (-e)."""
        if self.container_environment is not None:
            return self.container_environment
        # Default to environment from the service definition/profile
        try:
            return self._get_effective_environment(
                self.get_service_information()
            )
        except Exception:
            return None

    def get_container_volumes(self) -> Optional[list[str]]:
        """Return container volume mappings for docker run (-v)."""
        return self.container_volumes

    def get_internal_container_port(self) -> Optional[int]:
        """Return the container's internal port to expose."""
        return self.container_port__internal

    def get_external_container_port(self) -> Optional[int]:
        """Return the host's external port to map to the container."""
        return self.container_port__external

    def _set_services_with_retry(
        self, active_services_cache: ActiveServicesCache, services
    ):
        """
        Attempt to call active_services_cache.set_services with retries to
        gracefully tolerate
        locking issues for up to 5 seconds, sleeping 500ms between attempts.

        Returns True on success, False if unable to set within the retry window.
        """
        deadline = time.time() + 5.0
        attempt = 0
        while True:
            attempt += 1
            try:
                active_services_cache.set_services(services)
                return True
            except (WriteCollision, RuntimeError) as e:
                # Only retry on likely lock related issues
                # For RuntimeError, only retry if it looks like a lock error per
                # ActiveServicesCache
                msg = str(e)
                if (
                    isinstance(e, RuntimeError)
                    and "Lock error" not in msg
                    and "lock" not in msg.lower()
                ):
                    # Not a lock-related runtime error; re-raise
                    logger.error(
                        "Unexpected error while setting active services "
                        f"(no retry): {e}"
                    )
                    return False
                if time.time() >= deadline:
                    break
                time.sleep(0.5)
            except Exception as e:
                # Any other unexpected exception -> don't retry
                logger.error(
                    f"Unexpected error while setting active services: {e}"
                )
                return False
        logger.error(
            "Failed to set active services after retrying for 5 seconds "
            "due to locking issues"
        )
        return False

    @classmethod
    def _lookup_service(
        cls, service_type: str
    ) -> Optional[Type["BaseProvisionableService"]]:
        """
        Return a service class from the `services` module that:
        - Inherits from BaseProvisionableService
        - Has a class attribute `service_type` matching the argument

        The `services` module is scanned only once on first invocation,
        and results are cached for subsequent calls.
        """
        # Fast path if already initialized
        if cls._service_registry is not None:
            return cls._service_registry.get(service_type)

        # Lazily build the registry with thread-safety
        with cls._service_registry_lock:
            if cls._service_registry is None:
                cls._service_registry = cls._build_service_registry()
        return cls._service_registry.get(service_type)

    @classmethod
    def _build_service_registry(
        cls,
    ) -> dict[str, Type["BaseProvisionableService"]]:
        """Builds registry of service type to service class."""
        registry: dict[str, Type[BaseProvisionableService]] = {}
        try:
            import services as services_pkg  # local package
        except Exception as e:
            logger.error(f"Failed to import services package: {e}")
            return registry

        # Import all submodules under services package once
        try:
            package_walk = pkgutil.walk_packages(
                services_pkg.__path__, services_pkg.__name__ + "."
            )
            for _finder, name, _ispkg in package_walk:
                try:
                    importlib.import_module(name)
                except Exception as e:
                    logger.warning(
                        f"Could not import services submodule '{name}': {e}"
                    )
                    continue
        except Exception as e:
            logger.warning(f"Error while scanning services package: {e}")

        # After importing, inspect loaded modules under services.*
        for mod_name, module in list(sys.modules.items()):
            if not isinstance(mod_name, str):
                continue
            if not mod_name.startswith("services.") and mod_name != "services":
                continue
            try:
                for _, obj in inspect.getmembers(module, inspect.isclass):
                    # Ensure it's defined in the services package
                    # (not an import alias)
                    if not getattr(obj, "__module__", "").startswith(
                        "services"
                    ):
                        continue
                    if not issubclass(obj, cls) or obj is cls:
                        continue
                    st = getattr(obj, "service_type", None)
                    if not isinstance(st, str) or not st:
                        continue
                    if st in registry:
                        # Duplicate service_type; warn and keep the first one
                        if registry[st] is not obj:
                            logger.warning(
                                (
                                    f"Duplicate service_type '{st}' for "
                                    f"{obj.__module__}.{obj.__name__}; "
                                )
                                + (
                                    "already registered to "
                                    f"{registry[st].__module__}."
                                    f"{registry[st].__name__}. Ignoring."
                                )
                            )
                        continue
                    registry[st] = obj
            except Exception as e:
                logger.debug(
                    "Skipping module "
                    f"{mod_name} during registry build due to error: {e}"
                )

        if not registry:
            logger.warning(
                "No provisionable services found under the services package."
            )
        else:
            logger.info(
                "Service registry initialized with "
                f"{len(registry)} entries: {sorted(registry.keys())}"
            )
        return registry
