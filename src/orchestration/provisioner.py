import os
import signal
import subprocess
import tempfile
import time
from datetime import datetime
from typing import List, Optional

import yaml

from config.reader import SystemConfigReader
from hosts.resources import HostResources
from orchestration.models import ConfiguredServiceIdentifier, ProfilerAction
from orchestration.service import BaseProvisionableService
from util.active_services_cache import ActiveServicesCache, WriteCollision
from util.logger import get_logger
from util.profiler_request_cache import ProfilerRequestCache

from .models import (
    Cache,
    Resource,
    Service,
    ServiceDefinition,
    ServiceInformation,
    ServiceStatus,
)

BACKEND_DAEMON_SLEEP_TIME = 2.0
SERVICE_START_TIMEOUT = 20.0
SERVICE_STOP_TIMEOUT = 20.0

logger = get_logger()

_system_provisioner = None


class SystemProvisioner:
    """Singleton provisioner that manages service lifecycle and resources"""

    def __init__(
        self, config_reader: SystemConfigReader, cache: Optional[Cache] = None
    ):
        self.config_reader = config_reader
        self._cache = cache
        self._active_services_cache = (
            ActiveServicesCache(cache) if cache else None
        )
        self._profiler_request_cache = (
            ProfilerRequestCache(cache) if cache else None
        )

    def get_cache(self) -> Cache:
        return self._cache

    @classmethod
    def singleton(cls, cache: Optional[Cache] = None):
        global _system_provisioner
        if not _system_provisioner:
            config_reader = SystemConfigReader.singleton()

            if "OZWALD_PROVISIONER" not in os.environ:
                raise ValueError(
                    "OZWALD_PROVISIONER environment variable not set"
                )

            if not cache:
                provisioner_cache = None
                configured_provisioner_name = os.environ.get(
                    "OZWALD_PROVISIONER", "unconfigured"
                )
                for provisioner in config_reader.provisioners:
                    if provisioner.name == configured_provisioner_name:
                        provisioner_cache = provisioner.cache

                if not provisioner_cache:
                    raise ValueError(
                        "OZWALD_PROVISIONER: "
                        f"{configured_provisioner_name} not found in "
                        "configuration"
                    )
            else:
                provisioner_cache = cache

            _system_provisioner = cls(
                config_reader=config_reader, cache=provisioner_cache
            )
            # Prepare NFS mounts defined at top-level volumes before use
            try:
                _system_provisioner._prepare_nfs_mounts()
            except Exception as e:
                logger.error("Failed to prepare NFS mounts: %s", e)
        return _system_provisioner

    def run_backend_daemon(self):
        """Run the backend daemon
        - Loops until a termination signal is received
        - Sleeps BACKEND_DAEMON_SLEEP_TIME seconds between iterations
        - Processes active services: starting and stopping
        - Handles duplicate start/stop requests within timeout windows
        """
        if not self._active_services_cache:
            logger.error(
                "Active services cache not initialized; "
                "backend daemon cannot run"
            )
            return

        # Graceful shutdown handling
        running = True

        def _handle_signal(signum, frame):
            nonlocal running
            logger.info(
                "Provisioner backend received signal "
                f"{signum}; shutting down gracefully..."
            )
            running = False

        try:
            signal.signal(signal.SIGINT, _handle_signal)
            signal.signal(signal.SIGTERM, _handle_signal)
        except Exception:
            # In some environments (e.g., threads), setting signal handlers is
            # not permitted
            logger.debug(
                "Signal handlers could not be registered; proceeding "
                "without them"
            )

        while running:
            try:
                # Load the current snapshot of active services
                active_services: List[ServiceInformation] = (
                    self._active_services_cache.get_services()
                )

                # If there are no active services, check for profiling requests
                if not active_services and self._profiler_request_cache:
                    requests = self._profiler_request_cache.get_requests()
                    if requests:
                        # Process one request at a time
                        self._handle_profiler_request(requests[0])
                        # After processing, loop again (do not sleep long)
                        time.sleep(0.2)
                        continue

                if not active_services:
                    time.sleep(BACKEND_DAEMON_SLEEP_TIME)
                    continue

                updated = False
                now = datetime.now()

                for idx, svc_info in enumerate(active_services):
                    logger.info(f"examining service: {svc_info}")
                    try:
                        # Only act on services with STARTING or STOPPING status
                        if svc_info.status not in (
                            ServiceStatus.STARTING,
                            ServiceStatus.STOPPING,
                        ):
                            continue

                        # Resolve the service definition to get the concrete
                        # service type
                        service_def = self.config_reader.get_service_by_name(
                            svc_info.service
                        )
                        if not service_def:
                            logger.error(
                                (
                                    "Service definition "
                                    f"'{svc_info.service}' not "
                                )
                                + (
                                    "found for active service "
                                    f"'{svc_info.name}'"
                                )
                            )
                            continue

                        service_type_str = getattr(
                            service_def.type, "value", str(service_def.type)
                        )

                        # Lookup the service class
                        service_cls = BaseProvisionableService._lookup_service(
                            service_type_str
                        )
                        if not service_cls:
                            logger.error(
                                "No provisionable service implementation found"
                                f" for type '{service_type_str}' "
                                f"(service '{svc_info.name}')"
                            )
                            continue

                        # Ensure info dict exists
                        if svc_info.info is None:
                            svc_info.info = {}

                        logger.info(
                            "Processing service '%s' in backend loop",
                            svc_info.name,
                        )

                        # STARTING flow
                        if svc_info.status == ServiceStatus.STARTING:
                            logger.info("service is starting")
                            # Check duplicate initiation within timeout
                            start_initiated_iso = svc_info.info.get(
                                "start_initiated"
                            )
                            logger.info("past info check")
                            if start_initiated_iso:
                                try:
                                    started_when = datetime.fromisoformat(
                                        start_initiated_iso
                                    )
                                    if (
                                        now - started_when
                                    ).total_seconds() < SERVICE_START_TIMEOUT:
                                        logger.info(
                                            (
                                                "Duplicate start request "
                                                "ignored for service '%s': "
                                                "start already initiated at %s"
                                            ),
                                            svc_info.name,
                                            start_initiated_iso,
                                        )
                                        continue
                                except Exception:
                                    # If timestamp malformed, proceed with start
                                    pass

                            # Instantiate and start the service
                            try:
                                service_instance = service_cls(
                                    service_info=svc_info
                                )
                            except Exception as e:
                                logger.error(
                                    (
                                        "Failed to initialize service "
                                        "instance for '%s': %s(%s)"
                                    ),
                                    svc_info.name,
                                    e.__class__.__name__,
                                    e,
                                )
                                continue

                            # Record start initiation before starting
                            svc_info.info["start_initiated"] = now.isoformat()
                            updated = True

                            try:
                                logger.info(
                                    f"starting service: {svc_info.name}"
                                )
                                service_instance.start()
                            except Exception as e:
                                logger.error(
                                    ("Error starting service '%s': %s(%s)"),
                                    svc_info.name,
                                    e.__class__.__name__,
                                    e,
                                )
                                # Do not set completed on failure
                            else:
                                # Mark start completed immediately after
                                # initiating start
                                svc_info.info["start_completed"] = (
                                    datetime.now().isoformat()
                                )
                                updated = True

                        # STOPPING flow
                        elif svc_info.status == ServiceStatus.STOPPING:
                            stop_initiated_iso = svc_info.info.get(
                                "stop_initiated"
                            )
                            if stop_initiated_iso:
                                try:
                                    stopped_when = datetime.fromisoformat(
                                        stop_initiated_iso
                                    )
                                    if (
                                        now - stopped_when
                                    ).total_seconds() < SERVICE_STOP_TIMEOUT:
                                        logger.info(
                                            (
                                                "Duplicate stop request "
                                                "ignored for service '%s': "
                                                "stop already initiated at %s"
                                            ),
                                            svc_info.name,
                                            stop_initiated_iso,
                                        )
                                        continue
                                except Exception:
                                    pass

                            # Instantiate and stop the service
                            try:
                                service_instance = service_cls(
                                    service_info=svc_info
                                )
                            except Exception as e:
                                logger.error(
                                    (
                                        "Failed to initialize service "
                                        "instance for stopping '%s': %s"
                                    ),
                                    svc_info.name,
                                    e,
                                )
                                continue

                            # Record stop initiation prior to stopping
                            svc_info.info["stop_initiated"] = now.isoformat()
                            updated = True

                            try:
                                # Some services may not implement stop yet
                                stop_fn = getattr(
                                    service_instance, "stop", None
                                )
                                if callable(stop_fn):
                                    stop_fn()
                                else:
                                    logger.warning(
                                        (
                                            "Service class '%s' has no 'stop' "
                                            "method; marking as stopped"
                                        ),
                                        service_cls.__name__,
                                    )
                            except Exception as e:
                                logger.error(
                                    "Error stopping service '%s': %s",
                                    svc_info.name,
                                    e,
                                )
                            finally:
                                svc_info.info["stop_completed"] = (
                                    datetime.now().isoformat()
                                )
                                updated = True

                        # Assign the possibly updated object back
                        active_services[idx] = svc_info

                    except Exception as e:
                        logger.error(
                            (
                                "Unexpected error processing service '%s' "
                                "in backend loop: %s"
                            ),
                            getattr(svc_info, "name", "?"),
                            e,
                        )

                # Persist updates if any
                if updated:
                    deadline = time.time() + 5.0
                    while True:
                        try:
                            self._active_services_cache.set_services(
                                active_services
                            )
                            break
                        except (WriteCollision, RuntimeError) as e:
                            if time.time() >= deadline:
                                logger.error(
                                    "Failed to persist active services: %s",
                                    e,
                                )
                                break
                            time.sleep(0.5)
                        except Exception as e:
                            logger.error(
                                (
                                    "Unexpected error while writing active "
                                    "services cache: %s"
                                ),
                                e,
                            )
                            break

            except Exception as e:
                logger.error(f"Backend daemon loop encountered an error: {e}")

            time.sleep(BACKEND_DAEMON_SLEEP_TIME)

        logger.info("Provisioner backend daemon stopped.")

    # ------------------------------------------------------------------
    # Storage preparation (NFS)
    # ------------------------------------------------------------------

    def _prepare_nfs_mounts(self) -> None:
        """Mount any NFS volumes defined in the configuration.

        Each NFS volume is mounted to
        ${OZWALD_NFS_MOUNTS}/${volume_name} (default root: /exports).
        Idempotent: skips if already mounted.
        """
        vols = getattr(self.config_reader, "volumes", {}) or {}
        if not vols:
            return
        mount_root = os.environ.get("OZWALD_NFS_MOUNTS", "/exports")
        os.makedirs(mount_root, exist_ok=True)
        for name, spec in vols.items():
            if spec.get("type") != "nfs":
                continue
            server = spec.get("server")
            path = spec.get("path")
            opts = spec.get("options")
            mountpoint = os.path.join(mount_root, name)
            os.makedirs(mountpoint, exist_ok=True)
            if self._is_mountpoint(mountpoint):
                continue
            # Build mount command
            src = f"{server}:{path}"
            cmd = ["mount", "-t", "nfs"]
            if opts:
                if isinstance(opts, dict):
                    # dict to comma-separated k=v
                    flat = ",".join(f"{k}={v}" for k, v in opts.items())
                    cmd += ["-o", flat]
                elif isinstance(opts, str):
                    cmd += ["-o", opts]
            cmd += [src, mountpoint]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise RuntimeError(
                    f"Failed to mount NFS {src} -> {mountpoint}: "
                    f"{result.stderr or result.stdout}"
                )
            logger.info("Mounted NFS %s -> %s", src, mountpoint)

    def _is_mountpoint(self, path: str) -> bool:
        try:
            # Prefer /proc/self/mounts check for robustness
            mp = False
            with open("/proc/self/mounts") as f:
                for line in f:
                    try:
                        parts = line.split()
                        if len(parts) >= 2 and parts[1] == path:
                            mp = True
                            break
                    except Exception:
                        continue
            if mp:
                return True
            # Fallback to os.path.ismount
            return os.path.ismount(path)
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Profiling support
    # ------------------------------------------------------------------

    def _handle_profiler_request(self, request: ProfilerAction) -> None:
        """
        Handle a single profiling request: profile services and write YAML,
        then remove from cache.
        """
        if not self._profiler_request_cache or not self._active_services_cache:
            return

        # Mark as in-progress
        request.profile_in_progress = True
        request.profile_started_at = datetime.now()
        self._profiler_request_cache.update_profile_request(request)

        # Determine targets
        targets: List[ConfiguredServiceIdentifier] = []
        if request.profile_all_services:
            for svc_def in self.config_reader.services:
                if svc_def.profiles:
                    for prof in svc_def.profiles:
                        targets.append(
                            ConfiguredServiceIdentifier(
                                service_name=svc_def.service_name,
                                profile=prof.name,
                            )
                        )
                else:
                    targets.append(
                        ConfiguredServiceIdentifier(
                            service_name=svc_def.service_name, profile="default"
                        )
                    )
        else:
            targets = request.services or []

        # Ensure system is unloaded before profiling
        if self._active_services_cache.get_services():
            # If not unloaded, skip processing now
            return

        # Profile each target sequentially
        for target in targets:
            try:
                self._profile_single_service(target)
            except Exception as e:
                logger.error(
                    "Profiling error for %s:%s - %s",
                    target.service_name,
                    target.profile,
                    e,
                )

        # Remove the handled request from cache
        try:
            current = self._profiler_request_cache.get_requests()
            remaining = [
                r for r in current if r.request_id != request.request_id
            ]
            self._profiler_request_cache.set_requests(remaining)
        except Exception as e:
            logger.error(
                "Failed to remove completed profiler request %s: %s",
                request.request_id,
                e,
            )

    def _profile_single_service(
        self, target: ConfiguredServiceIdentifier
    ) -> None:
        """Profile a single configured service/profile."""
        # Measure pre state
        pre = HostResources.inspect_host()

        # Construct a unique service instance name
        inst_name = f"prof-{target.service_name}-{target.profile}"

        # Activate the service
        svc_info = ServiceInformation(
            name=inst_name,
            service=target.service_name,
            profile=target.profile,
            status=ServiceStatus.STARTING,
            info={},
        )
        self.update_services([svc_info])

        # Wait for start completed marker
        self._wait_for_start_completed(inst_name, timeout=60.0)

        # Measure post state
        post = HostResources.inspect_host()

        # Compute deltas
        usage = {
            "cpu_cores": max(
                0, pre.available_cpu_cores - post.available_cpu_cores
            ),
            "memory_gb": max(0.0, pre.available_ram_gb - post.available_ram_gb),
            "vram_gb": max(0.0, pre.available_vram_gb - post.available_vram_gb),
        }

        # Persist to YAML
        self._write_profile_usage(target.service_name, target.profile, usage)

        # Stop the service and restore unloaded state
        # Request no services active -> will mark existing as STOPPING
        self.update_services([])
        self._wait_for_stop_completed(inst_name, timeout=60.0)

        # After stop, clear cache to keep system unloaded
        self._active_services_cache.set_services([])

    def _wait_for_start_completed(
        self, instance_name: str, timeout: float = 60.0
    ) -> None:
        start = time.time()
        while time.time() - start < timeout:
            services = self._active_services_cache.get_services()
            for s in services:
                if (
                    s.name == instance_name
                    and s.info
                    and s.info.get("start_completed")
                ):
                    return
            time.sleep(0.5)
        logger.warning(
            (
                "Timeout waiting for service %s to start; proceeding with "
                "profiling anyway"
            ),
            instance_name,
        )

    def _wait_for_stop_completed(
        self, instance_name: str, timeout: float = 60.0
    ) -> None:
        start = time.time()
        while time.time() - start < timeout:
            services = self._active_services_cache.get_services()
            for s in services:
                if (
                    s.name == instance_name
                    and s.info
                    and s.info.get("stop_completed")
                ):
                    return
            time.sleep(0.5)
        logger.warning(
            f"Timeout waiting for service {instance_name} to stop; continuing"
        )

    def _write_profile_usage(
        self, service_name: str, profile: str, usage: dict
    ) -> None:
        """Write or update the YAML file with usage info."""
        # Resolve output path, preferring environment override.
        # Avoid hardcoded /tmp to satisfy Bandit B108 and improve portability.
        default_path = os.path.join(
            tempfile.gettempdir(), "ozwald-profiles.yml"
        )
        path = os.environ.get("OZWALD_PROFILER_DATA", default_path)
        parent_dir = os.path.dirname(path) or "."

        data = {}
        try:
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    loaded = yaml.safe_load(f)
                    if isinstance(loaded, dict):
                        data = loaded
        except Exception as e:
            logger.warning(f"Could not read profiler data file '{path}': {e}")

        svc_block = data.get(service_name, {})
        svc_block[profile] = usage
        data[service_name] = svc_block

        try:
            # Ensure parent directory exists with restrictive permissions
            os.makedirs(parent_dir, mode=0o700, exist_ok=True)

            # Atomic write: write to a temp file in the same directory,
            # then replace
            fd, tmp_path = tempfile.mkstemp(
                prefix=".ozwald-profiles.", dir=parent_dir, text=True
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    yaml.safe_dump(data, f, sort_keys=True)
                # Restrict permissions on the new file
                os.chmod(tmp_path, 0o600)
                os.replace(tmp_path, path)
            finally:
                # In case of exceptions before replace, try to clean up
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
        except Exception as e:
            logger.error(f"Failed to write profiler data to '{path}': {e}")

    def get_configured_services(self) -> List[ServiceDefinition]:
        """Get all services configured for this provisioner"""
        return self.config_reader.services

    def get_active_services(self) -> List[Service]:
        """Get all currently active services"""
        if self._active_services_cache:
            return self._active_services_cache.get_services()
        return []

    def update_services(
        self, service_updates: List[ServiceInformation]
    ) -> bool:
        """
        Update active services based on provided service information.
        This initiates activation/deactivation of services.

        Returns:
            True if services were successfully updated, False otherwise.
        """
        if not self._active_services_cache:
            return False

        # Get current active services from cache
        active_service_info_objects = self._active_services_cache.get_services()

        # Create a set of requested service names
        requested_services = {si.name for si in service_updates}

        # Stop services if they're not in the requested list
        services_to_remove = [
            svc
            for svc in active_service_info_objects
            if svc.name not in requested_services
        ]
        for svc in services_to_remove:
            svc.status = ServiceStatus.STOPPING

        # Add or update services
        for service_info in service_updates:
            existing = next(
                (
                    s
                    for s in active_service_info_objects
                    if s.name == service_info.name
                ),
                None,
            )

            if existing:
                # Update existing service if needed
                if existing.status == ServiceStatus.STOPPING:
                    existing.status = ServiceStatus.STARTING
            else:
                new_service = self._init_service(service_info)
                if new_service:
                    active_service_info_objects.append(new_service)

        # Save updated services to cache with retry logic
        start_time = time.time()
        while time.time() - start_time < 2.0:
            try:
                self._active_services_cache.set_services(
                    active_service_info_objects
                )
                return True
            except WriteCollision:
                time.sleep(0.2)

        logger.error(
            "Failed to update services: timeout after 2 seconds "
            "due to write collisions"
        )
        return False

    def get_available_resources(self) -> List[Resource]:
        """Get currently available resources on this host"""
        host_resources = HostResources.inspect_host()

        resources = []

        # CPU resource
        resources.append(
            Resource(
                name="cpu",
                type="cpu",
                unit="cores",
                value=host_resources.available_cpu_cores,
                related_resources=None,
                extended_attributes={"total": host_resources.total_cpu_cores},
            )
        )

        # Memory resource
        resources.append(
            Resource(
                name="memory",
                type="memory",
                unit="GB",
                value=host_resources.available_ram_gb,
                related_resources=None,
                extended_attributes={"total": host_resources.total_ram_gb},
            )
        )

        # VRAM resource
        if host_resources.total_gpus > 0:
            resources.append(
                Resource(
                    name="vram",
                    type="vram",
                    unit="GB",
                    value=host_resources.available_vram_gb,
                    related_resources=["gpu"],
                    extended_attributes={
                        "total": host_resources.total_vram_gb,
                        "gpu_details": {
                            str(gpu_id): {
                                "total": host_resources.gpuid_to_total_vram.get(
                                    gpu_id, 0
                                ),
                                "available": (
                                    host_resources.gpuid_to_available_vram.get(
                                        gpu_id, 0
                                    )
                                ),
                            }
                            for gpu_id in range(host_resources.total_gpus)
                        },
                    },
                )
            )

            # GPU resource
            for gpu_id in range(host_resources.total_gpus):
                vram_total = host_resources.gpuid_to_total_vram.get(gpu_id, 0)
                vram_avail = host_resources.gpuid_to_available_vram.get(
                    gpu_id, 0
                )
                is_available = (
                    1.0 if gpu_id in host_resources.available_gpus else 0.0
                )
                resources.append(
                    Resource(
                        name=f"gpu_{gpu_id}",
                        type="gpu",
                        unit="device",
                        value=is_available,
                        related_resources=["vram"],
                        extended_attributes={
                            "gpu_id": gpu_id,
                            "vram_total": vram_total,
                            "vram_available": vram_avail,
                        },
                    )
                )

        return resources

    def _init_service(
        self, service_info: ServiceInformation
    ) -> ServiceInformation:
        """init a new service def."""
        # read service definition
        service_def = self.config_reader.get_service_by_name(
            service_info.service
        )
        if not service_def:
            raise ValueError(
                "Service definition '" + service_info.service + "' "
                "not found in configuration"
            )

        service_info.status = ServiceStatus.STARTING
        return service_info


if __name__ == "__main__":
    # Entry point to run the provisioner backend daemon
    try:
        provisioner = SystemProvisioner.singleton()
        logger.info("Starting SystemProvisioner backend daemon...")
        provisioner.run_backend_daemon()
    except Exception as e:
        logger.error(f"Provisioner backend daemon exited with error: {e}")
        raise
