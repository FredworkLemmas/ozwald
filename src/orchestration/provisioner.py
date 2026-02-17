import itertools
import os
import pathlib
import shutil
import signal
import subprocess
import time
from datetime import datetime
from typing import Iterable, List, Optional, Type

import yaml
from dotenv import load_dotenv

from config.reader import SystemConfigReader
from hosts.resources import HostResources
from orchestration.service import BaseProvisionableService
from util.active_services_cache import ActiveServicesCache, WriteCollision
from util.footprint_request_cache import FootprintRequestCache
from util.logger import get_logger
from util.secrets_store import SecretsStore

from .models import (
    Cache,
    ConfiguredServiceIdentifier,
    FootprintAction,
    NetworkInstance,
    Resource,
    ServiceDefinition,
    ServiceInformation,
    ServiceInstanceUsage,
    ServiceStatus,
    SystemUsageDelta,
    VolumeDefinition,
)

BACKEND_DAEMON_SLEEP_TIME = 2.0
SERVICE_START_TIMEOUT = 3600.0
SERVICE_STOP_TIMEOUT = 3600.0

logger = get_logger()
load_dotenv()

_system_provisioner = None


class SystemProvisioner:
    """Singleton provisioner that manages service lifecycle and resources"""

    def __init__(
        self,
        config_reader: SystemConfigReader,
        cache: Optional[Cache] = None,
    ):
        self.config_reader = config_reader
        self._cache = cache or self._init_cache()
        self._active_services_cache = (
            ActiveServicesCache(cache) if cache else None
        )
        self._footprint_request_cache = (
            FootprintRequestCache(cache) if cache else None
        )
        self._secrets_store = SecretsStore(cache) if cache else None
        self._provisioned_networks: List[NetworkInstance] = []

        # Find our own provisioner definition to get encrypted_storage_dir
        self.encrypted_storage_dir = None
        configured_provisioner_name = os.environ.get("OZWALD_PROVISIONER")
        if configured_provisioner_name:
            for provisioner in config_reader.provisioners:
                if provisioner.name == configured_provisioner_name:
                    self.encrypted_storage_dir = (
                        provisioner.encrypted_storage_dir
                    )
                    break

    def get_cache(self) -> Cache:
        return self._cache

    @staticmethod
    def _init_cache() -> Cache:
        provisioner_cache = None
        config_reader = SystemConfigReader.singleton()
        configured_provisioner_name = os.environ.get(
            "OZWALD_PROVISIONER",
        )

        if not configured_provisioner_name:
            if len(config_reader.provisioners) == 1:
                configured_provisioner_name = config_reader.provisioners[0].name
            else:
                configured_provisioner_name = "unconfigured"

        for provisioner in config_reader.provisioners:
            if provisioner.name == configured_provisioner_name:
                provisioner_cache = provisioner.cache
                break

        if not provisioner_cache:
            # If we still don't have a cache, and there are
            # provisioners, maybe we should just use the first one?
            if config_reader.provisioners:
                provisioner_cache = config_reader.provisioners[0].cache
            else:
                # No provisioners at all; this is a configuration error
                raise ValueError(
                    "No provisioners found in configuration",
                )
        return provisioner_cache

    @classmethod
    def singleton(cls, cache: Optional[Cache] = None):
        global _system_provisioner
        if not _system_provisioner:
            config_reader = SystemConfigReader.singleton()
            provisioner_cache = cache or cls._init_cache()

            _system_provisioner = cls(
                config_reader=config_reader,
                cache=provisioner_cache,
            )
            # Prepare NFS mounts defined at top-level volumes before use
            try:
                _system_provisioner._prepare_nfs_mounts()
            except Exception as e:
                logger.error("Failed to prepare NFS mounts: %s", e)
        return _system_provisioner

    def set_secret(self, realm: str, locker: str, encrypted_blob: str) -> None:
        """Store an encrypted secret in the secrets store."""
        if self._secrets_store:
            self._secrets_store.set_secret(realm, locker, encrypted_blob)

    def get_secret(self, realm: str, locker: str) -> str | None:
        """Retrieve an encrypted secret from the secrets store."""
        if self._secrets_store:
            return self._secrets_store.get_secret(realm, locker)
        return None

    def get_configured_services(self) -> List[ServiceDefinition]:
        """Get all service_definitions configured for this provisioner"""
        return self.config_reader.service_definitions

    def get_active_services(
        self,
        persistent: Optional[bool] = None,
    ) -> List[ServiceInformation]:
        """Get all currently active services"""
        if self._active_services_cache:
            services = self._active_services_cache.get_services()
            if persistent is None:
                return services
            return [s for s in services if s.persistent == persistent]
        return []

    def update_active_services(
        self,
        service_updates: List[ServiceInformation],
        persistent: Optional[bool] = None,
    ) -> bool:
        """Update active services based on provided service information.
        This initiates activation/deactivation of services.

        Returns:
            True if services were successfully updated, False otherwise.

        """
        if not self._active_services_cache:
            return False

        # Validation
        if persistent is True:
            for si in service_updates:
                if not si.persistent:
                    raise ValueError(
                        f"Service {si.name} is not persistent, but "
                        "update_dynamic_services was called with "
                        "persistent=True"
                    )
        elif persistent is False:
            for si in service_updates:
                if si.persistent:
                    raise ValueError(
                        f"Service {si.name} is persistent, but "
                        "update_dynamic_services was called with "
                        "persistent=False"
                    )

        # Get current active services from cache
        active_service_info_objects = self._active_services_cache.get_services()

        # Create a set of requested service names
        requested_services = {si.name for si in service_updates}

        # Stop services if they're not in the requested list and within
        # the persistence scope
        if persistent is None:
            services_to_remove = [
                svc
                for svc in active_service_info_objects
                if svc.name not in requested_services
            ]
        elif persistent is True:
            services_to_remove = [
                svc
                for svc in active_service_info_objects
                if svc.persistent and svc.name not in requested_services
            ]
        else:  # persistent is False
            services_to_remove = [
                svc
                for svc in active_service_info_objects
                if not svc.persistent and svc.name not in requested_services
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

                # Update desired state fields
                existing.profile = service_info.profile
                existing.variety = service_info.variety
                existing.secrets_tokens = service_info.secrets_tokens
                # Properties are usually derived from definition, but if they
                # were provided in service_info, we should update them.
                if service_info.properties:
                    existing.properties.update(service_info.properties)
            else:
                new_service = self._init_service(service_info)
                if new_service:
                    active_service_info_objects.append(new_service)

        # Save updated services to cache with retry logic
        start_time = time.time()
        while time.time() - start_time < 2.0:
            try:
                self._active_services_cache.set_services(
                    active_service_info_objects,
                )
                return True
            except WriteCollision:
                time.sleep(0.2)

        logger.error(
            "Failed to update services: timeout after 2 seconds "
            "due to write collisions",
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
            ),
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
            ),
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
                                    gpu_id,
                                    0,
                                ),
                                "available": (
                                    host_resources.gpuid_to_available_vram.get(
                                        gpu_id,
                                        0,
                                    )
                                ),
                            }
                            for gpu_id in range(host_resources.total_gpus)
                        },
                    },
                ),
            )

            # GPU resource
            for gpu_id in range(host_resources.total_gpus):
                vram_total = host_resources.gpuid_to_total_vram.get(gpu_id, 0)
                vram_avail = host_resources.gpuid_to_available_vram.get(
                    gpu_id,
                    0,
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
                    ),
                )

        return resources

    def _validate_active_services_cache_initialized(self) -> bool:
        if not self._active_services_cache:
            logger.error(
                "Active services cache not initialized; "
                "backend daemon cannot run",
            )
            return False
        return True

    @staticmethod
    def _validate_footprint_data_path_defined() -> bool:
        footprint_path_str = os.environ.get("OZWALD_FOOTPRINT_DATA")
        if not footprint_path_str:
            logger.error(
                "OZWALD_FOOTPRINT_DATA environment variable is not defined; "
                "backend daemon cannot run",
            )
            return False
        return True

    @staticmethod
    def _validate_footprint_data_file_is_writable() -> bool:
        footprint_path_str = os.environ.get("OZWALD_FOOTPRINT_DATA")
        footprint_path = pathlib.Path(footprint_path_str)
        if footprint_path.exists():
            if not os.access(footprint_path, os.W_OK):
                logger.error(
                    f"Footprint data file '{footprint_path}' is not writable; "
                    "backend daemon cannot run"
                )
                return False
        else:
            parent = footprint_path.parent
            if not parent.exists():
                logger.error(
                    f"Parent directory '{parent}' for "
                    f"footprint data: {footprint_path} does not exist;"
                    "backend daemon cannot run"
                )
                return False
            if not os.access(parent, os.W_OK):
                logger.error(
                    f"Footprint data directory '{parent}' is not writable; "
                    "backend daemon cannot run"
                )
                return False

        return True

    def _init_services(self) -> None:
        """Call init_service class method for each service class."""
        for svc_cls in BaseProvisionableService.get_service_classes():
            logger.info("Initializing service class: %s", svc_cls.__name__)
            svc_cls.init_service()

    def _init_networks(self) -> None:
        """Call _init_networks class method for each service class."""
        for svc_cls in BaseProvisionableService.get_service_classes():
            if hasattr(svc_cls, "_init_networks"):
                logger.info(
                    "Initializing networks for service class: %s",
                    svc_cls.__name__,
                )
                svc_cls._init_networks()

    def _get_service_class_from_service_info(
        self, svc_info: ServiceInformation
    ) -> Optional[Type["BaseProvisionableService"]]:
        # Resolve the service definition to get the concrete
        # service type
        service_def = self.config_reader.get_service_by_name(
            svc_info.service,
            svc_info.realm,
        )
        if not service_def:
            logger.error(
                (f"Service definition '{svc_info.service}' not ")
                + (f"found for active service '{svc_info.name}'"),
            )
            return None

        service_type_str = getattr(
            service_def.type,
            "value",
            str(service_def.type),
        )

        # Lookup the service class
        return BaseProvisionableService._lookup_service(
            service_type_str,
        )

    def _start_service(
        self,
        svc_info: ServiceInformation,
        service_cls: Type["BaseProvisionableService"],
        now: datetime,
    ) -> bool:
        updated = False

        # Re-verify status and initiation from the most current cache state
        # to avoid race conditions with background threads or other
        # provisioner instances.
        current_active = self._active_services_cache.get_services()
        latest_info = next(
            (s for s in current_active if s.name == svc_info.name), None
        )
        if latest_info:
            if latest_info.status == ServiceStatus.AVAILABLE:
                logger.info(
                    f"Service {svc_info.name} is already AVAILABLE, "
                    "skipping start"
                )
                return False
            # Update our local info with latest from cache to get the
            # freshest info
            if latest_info.info:
                svc_info.info.update(latest_info.info)

        logger.info(f"service {svc_info.name}[{svc_info.service}] is starting")
        # Check duplicate initiation within timeout
        start_initiated_iso = svc_info.info.get(
            "start_initiated",
        )
        if start_initiated_iso:
            started_when = datetime.fromisoformat(
                start_initiated_iso,
            )
            if (now - started_when).total_seconds() < SERVICE_START_TIMEOUT:
                logger.info(
                    (
                        "Duplicate start request "
                        "ignored for service '%s': "
                        "start already initiated at %s"
                    ),
                    svc_info.name,
                    start_initiated_iso,
                )
                return False

        # Instantiate and start the service
        try:
            service_instance = service_cls(
                service_info=svc_info,
            )
        except Exception as e:
            logger.error(
                ("Failed to initialize service instance for '%s': %s(%s)"),
                svc_info.name,
                e.__class__.__name__,
                e,
            )
            return False

        # Record start initiation before starting
        svc_info.info["start_initiated"] = now.isoformat()
        updated = True

        # Prepare and mount volumes
        self._prepare_service_volumes(svc_info)

        try:
            logger.info(
                f"starting service: {svc_info.name}",
            )
            service_instance.start()
            # If start() returns without error, we consider it AVAILABLE
            svc_info.status = ServiceStatus.AVAILABLE
            svc_info.info["start_completed"] = datetime.now().isoformat()
            updated = True
        except Exception as e:
            logger.error(
                ("Error starting service '%s': %s(%s)"),
                svc_info.name,
                e.__class__.__name__,
                e,
            )
            # Do not set completed on failure

        return updated

    def _stop_service(
        self,
        svc_info: ServiceInformation,
        service_cls: Type["BaseProvisionableService"],
        now: datetime,
    ) -> bool:
        updated = False

        # Re-verify status and initiation from the most current cache state
        current_active = self._active_services_cache.get_services()
        latest_info = next(
            (s for s in current_active if s.name == svc_info.name), None
        )
        if latest_info:
            if latest_info.status is None:  # Service already removed
                logger.info(
                    f"Service {svc_info.name} is already removed, skipping stop"
                )
                return False
            if latest_info.info:
                svc_info.info.update(latest_info.info)

        stop_initiated_iso = svc_info.info.get(
            "stop_initiated",
        )
        if stop_initiated_iso:
            stopped_when = datetime.fromisoformat(
                stop_initiated_iso,
            )
            if (now - stopped_when).total_seconds() < SERVICE_STOP_TIMEOUT:
                logger.info(
                    (
                        "Duplicate stop request "
                        "ignored for service '%s': "
                        "stop already initiated at %s"
                    ),
                    svc_info.name,
                    stop_initiated_iso,
                )
                return False

        # Instantiate and stop the service
        try:
            service_instance = service_cls(
                service_info=svc_info,
            )
        except Exception as e:
            logger.error(
                ("Failed to initialize service instance for stopping '%s': %s"),
                svc_info.name,
                e,
            )
            return False

        # Record stop initiation prior to stopping
        svc_info.info["stop_initiated"] = now.isoformat()
        updated = True

        try:
            # Some service_definitions may not implement stop yet
            stop_fn = getattr(
                service_instance,
                "stop",
                None,
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

            # Cleanup and unmount volumes
            self._cleanup_service_volumes(svc_info)
        except Exception as e:
            logger.error(
                "Error stopping service '%s': %s",
                svc_info.name,
                e,
            )
        finally:
            svc_info.info["stop_completed"] = datetime.now().isoformat()
            updated = True

        return updated

    def _handle_requests(self):
        # Load the current snapshot of active services
        active_services: List[ServiceInformation] = (
            self._active_services_cache.get_services()
        )

        # If there are no active services, check for footprinting
        # requests
        if not active_services and self._footprint_request_cache:
            logger.info("No active services, checking footprinting requests")
            requests = self._footprint_request_cache.get_requests()
            if requests:
                logger.info("footprint requests found")
                # Process one request at a time
                self._handle_footprint_request(requests[0])
                # After processing, loop again (do not sleep long)
                time.sleep(0.2)
                return

        if not active_services:
            time.sleep(BACKEND_DAEMON_SLEEP_TIME)
            return

        any_updated = False
        now = datetime.now()

        for idx, svc_info in enumerate(active_services):
            logger.info("examining service: %s", svc_info)
            try:
                # Only act on services with STARTING or STOPPING status
                if svc_info.status not in (
                    ServiceStatus.STARTING,
                    ServiceStatus.STOPPING,
                ):
                    continue

                # lookup service class
                service_cls = self._get_service_class_from_service_info(
                    svc_info,
                )
                if not service_cls:
                    logger.error(
                        "No provisionable service implementation found"
                        f" for type '{svc_info.service}' "
                        f"(service '{svc_info.name}')",
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
                if (
                    svc_info.status == ServiceStatus.STARTING
                    and self._start_service(svc_info, service_cls, now)
                ) or (
                    svc_info.status == ServiceStatus.STOPPING
                    and self._stop_service(svc_info, service_cls, now)
                ):
                    any_updated = True

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
        if any_updated:
            # Remove services that have completed stopping to avoid
            # lingering STOPPING entries and races with service-level
            # cache updates. A STOPPING service with a stop_completed
            # marker should be removed from the active list.
            active_services = [
                s
                for s in active_services
                if not (
                    s.status == ServiceStatus.STOPPING
                    and getattr(s, "info", None)
                    and s.info.get("stop_completed")
                )
            ]

            deadline = time.time() + 5.0
            while True:
                try:
                    self._active_services_cache.set_services(
                        active_services,
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

    def _init_persistent_services(self) -> None:
        """Start all persistent services defined in the configuration."""
        service_updates = []
        for ps_decl in self.config_reader.persistent_services:
            si = ServiceInformation(
                name=ps_decl.name,
                service=ps_decl.service,
                realm=ps_decl.realm,
                variety=ps_decl.variety,
                profile=ps_decl.profile,
                persistent=True,
            )
            service_updates.append(si)

        if service_updates:
            logger.info(
                "Initializing %d persistent services", len(service_updates)
            )
            self.update_active_services(service_updates, persistent=True)

    def _shutdown_persistent_services(self) -> None:
        """Gracefully shutdown all persistent services."""
        logger.info("Shutting down persistent services...")
        # Request all persistent services to stop
        self.update_active_services([], persistent=True)

        # Wait for them to stop
        start_time = time.time()
        timeout = 60.0
        while time.time() - start_time < timeout:
            persistent_services = self.get_active_services(persistent=True)
            if not persistent_services:
                logger.info("All persistent services have stopped.")
                return

            # Process stop requests
            try:
                self._handle_requests()
            except Exception as e:
                logger.error("Error during persistent services shutdown: %s", e)

            time.sleep(1.0)

        logger.warning(
            "Timeout reached while waiting for persistent services to stop."
        )

    def _deinit_services(self) -> None:
        """Call deinit_service class method for each service class."""
        for svc_cls in BaseProvisionableService.get_service_classes():
            logger.info("Deinitializing service class: %s", svc_cls.__name__)
            svc_cls.deinit_service()

    def run_backend_daemon(self):
        """Run the backend daemon
        - Loops until a termination signal is received
        - Sleeps BACKEND_DAEMON_SLEEP_TIME seconds between iterations
        - Processes active services: starting and stopping
        - Handles duplicate start/stop requests within timeout windows
        """
        # sanity check: active services cache must be initialized
        if not self._validate_active_services_cache_initialized():
            return

        # sanity check: footprinting data path envvar must be defined
        if not self._validate_footprint_data_path_defined():
            return

        # sanity check: footprinting data file must be writable
        if not self._validate_footprint_data_file_is_writable():
            return

        # Initialize storage
        self._init_storage()

        # Initialize services
        self._init_services()

        # Initialize networks
        self._init_networks()

        # Start persistent services
        self._init_persistent_services()

        # Graceful shutdown handling
        running = True

        def _handle_signal(signum, frame):
            nonlocal running
            logger.info(
                "Provisioner backend received signal %s; "
                "shutting down gracefully...",
                signum,
            )
            running = False
            self._deinit_services()

        try:
            signal.signal(signal.SIGINT, _handle_signal)
            signal.signal(signal.SIGTERM, _handle_signal)
        except Exception:
            # In some environments (e.g., threads), setting signal handlers is
            # not permitted
            logger.debug(
                "Signal handlers could not be registered; proceeding "
                "without them",
            )

        while running:
            try:
                self._handle_requests()
            except Exception as e:
                logger.error("Backend daemon loop encountered an error: %s", e)

            time.sleep(BACKEND_DAEMON_SLEEP_TIME)

        # Graceful shutdown of persistent services
        self._shutdown_persistent_services()

        # Final deinitialization of all service types
        self._deinit_services()

        # Cleanup temporary volumes and storage
        self._clear_temporary_volumes()
        self._deinit_storage()

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
        nfs_vols = {
            name: spec
            for name, spec in vols.items()
            if spec.get("type") == "nfs"
        }
        if not nfs_vols:
            return

        mount_root = os.environ.get("OZWALD_NFS_MOUNTS", "/exports")
        pathlib.Path(mount_root).mkdir(exist_ok=True, parents=True)
        for name, spec in nfs_vols.items():
            server = spec.get("server")
            path = spec.get("path")
            opts = spec.get("options")
            mountpoint = os.path.join(mount_root, name)
            pathlib.Path(mountpoint).mkdir(exist_ok=True, parents=True)
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
            result = subprocess.run(
                cmd, check=False, capture_output=True, text=True
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"Failed to mount NFS {src} -> {mountpoint}: "
                    f"{result.stderr or result.stdout}",
                )
            logger.info("Mounted NFS %s -> %s", src, mountpoint)

    # ------------------------------------------------------------------
    # Encrypted Storage Management
    # ------------------------------------------------------------------

    def _init_storage(self) -> None:
        """Initialize encrypted storage root and clear temporary volumes."""
        if not self.encrypted_storage_dir:
            return

        storage_file = os.environ.get("OZWALD_ENCRYPTED_VOLUME_FILE")
        system_key = os.environ.get("OZWALD_SYSTEM_KEY")

        if storage_file and system_key:
            # Mount the global encrypted volume
            self._mount_encrypted_volume(
                pathlib.Path(storage_file),
                pathlib.Path(self.encrypted_storage_dir),
                system_key,
            )

        # Clear leftover temporary volumes
        self._clear_temporary_volumes()

        # Ensure realm-specific directories exist
        for realm_name in self.config_reader.realms:
            realm_root = pathlib.Path(self.encrypted_storage_dir) / realm_name
            (realm_root / "tmp").mkdir(exist_ok=True, parents=True)
            (realm_root / "mounts").mkdir(exist_ok=True, parents=True)

    def _deinit_storage(self) -> None:
        """Unmount the global encrypted storage root."""
        if not self.encrypted_storage_dir:
            return

        storage_file = os.environ.get("OZWALD_ENCRYPTED_VOLUME_FILE")
        if storage_file:
            self._unmount_encrypted_volume(
                pathlib.Path(self.encrypted_storage_dir)
            )

    def _clear_temporary_volumes(self) -> None:
        """Safely unmount and remove stale temporary volumes."""
        if not self.encrypted_storage_dir:
            return

        storage_root = pathlib.Path(self.encrypted_storage_dir)
        if not storage_root.exists():
            return

        for realm_dir in storage_root.iterdir():
            if not realm_dir.is_dir():
                continue

            tmp_root = realm_dir / "tmp"
            if tmp_root.exists():
                try:
                    # Unmount any sub-mounts if they exist (unlikely in tmp)
                    for entry in tmp_root.rglob("*"):
                        if entry.is_dir() and self._is_mountpoint(str(entry)):
                            self._unmount_encrypted_volume(entry)
                    shutil.rmtree(tmp_root)
                except Exception as e:
                    logger.warning(
                        f"Failed to clear temporary root {tmp_root}: {e}"
                    )
                tmp_root.mkdir(exist_ok=True)

            mounts_root = realm_dir / "mounts"
            if mounts_root.exists():
                # Unmount everything in mounts_root
                for mnt in mounts_root.rglob("*"):
                    if mnt.is_dir() and self._is_mountpoint(str(mnt)):
                        self._unmount_encrypted_volume(mnt)

    def _mount_encrypted_volume(
        self, image_path: pathlib.Path, mount_point: pathlib.Path, key: str
    ) -> bool:
        """Mount an encrypted filesystem image."""
        if self._is_mountpoint(str(mount_point)):
            return True

        mount_point.mkdir(exist_ok=True, parents=True)

        logger.info(f"Mounting encrypted volume {image_path} to {mount_point}")

        # Placeholder for actual LUKS/mount logic.
        # In a real Ozwald environment, this would involve cryptsetup and mount.
        # For the purpose of this implementation, we simulate success if the
        # image exists, or create a directory if it's for temporary use.
        try:
            # Simulation: just ensure the mount point is usable
            return True
        except Exception as e:
            logger.error(f"Failed to mount encrypted volume: {e}")
            return False

    def _unmount_encrypted_volume(self, mount_point: pathlib.Path) -> bool:
        """Unmount an encrypted volume."""
        if not self._is_mountpoint(str(mount_point)):
            return True

        logger.info(f"Unmounting volume at {mount_point}")
        try:
            # Simulation: just use umount command
            subprocess.run(["umount", "-l", str(mount_point)], check=False)
            return True
        except Exception as e:
            logger.error(f"Failed to unmount volume: {e}")
            return False

    def _get_latest_volume_version(
        self, realm: str, source: str
    ) -> Optional[pathlib.Path]:
        """Find the latest encrypted version for a given base source name."""
        if not self.encrypted_storage_dir:
            return None

        realm_root = pathlib.Path(self.encrypted_storage_dir) / realm
        if not realm_root.exists():
            return None

        # Pattern: {source}.{timestamp}.img
        candidates = list(realm_root.glob(f"{source}.*.img"))
        if not candidates:
            return None

        # Sort by name (timestamp is sortable)
        candidates.sort()
        return candidates[-1]

    def _prepare_service_volumes(self, svc_info: ServiceInformation) -> None:
        """Prepare and mount volumes for the service."""

        realm_name = svc_info.realm
        realm = self.config_reader.realms.get(realm_name)
        if not realm:
            return

        realm_volumes = {v.name: v for v in (realm.volumes or [])}

        reader = SystemConfigReader.singleton()
        eff_def = reader.get_effective_service_definition(
            svc_info.service,
            realm=svc_info.realm,
            profile=svc_info.profile,
            variety=svc_info.variety,
        )

        resolved_vols = []
        for vol_str in eff_def.volumes:
            parts = vol_str.split(":")
            name_or_host = parts[0]
            target = parts[1]
            mode = (":" + parts[2]) if len(parts) > 2 else ""

            if name_or_host in realm_volumes:
                vol_def = realm_volumes[name_or_host]
                host_path = self._mount_realm_volume(svc_info, vol_def)
                if host_path:
                    resolved_vols.append(f"{host_path}:{target}{mode}")
                else:
                    logger.warning(
                        f"Failed to mount realm volume '{name_or_host}' "
                        f"for service '{svc_info.name}'"
                    )
                    resolved_vols.append(vol_str)
            else:
                resolved_vols.append(vol_str)

        svc_info.info["resolved_volumes"] = resolved_vols

    def _mount_realm_volume(
        self, svc_info: ServiceInformation, vol_def: "VolumeDefinition"
    ) -> Optional[str]:
        from .models import VolumeType

        realm_name = svc_info.realm
        instance_name = svc_info.name
        storage_root = pathlib.Path(self.encrypted_storage_dir) / realm_name

        if vol_def.type == VolumeType.TMP_WRITEABLE:
            # Create a unique directory for this instance
            host_path = storage_root / "tmp" / instance_name / vol_def.name
            host_path.mkdir(exist_ok=True, parents=True)
            return str(host_path)

        elif vol_def.type == VolumeType.VERSIONED_READ_ONLY:
            # Find latest version
            latest_img = self._get_latest_volume_version(
                realm_name, vol_def.source
            )
            if not latest_img:
                logger.warning(
                    f"No versioned volume found for source {vol_def.source}"
                )
                return None

            mount_point = storage_root / "mounts" / instance_name / vol_def.name

            # Use key from secrets_tokens
            key = svc_info.secrets_tokens.get(vol_def.name)
            if not key:
                logger.warning(
                    f"No key provided for versioned volume {vol_def.name}"
                )
                return None

            if self._mount_encrypted_volume(latest_img, mount_point, key):
                return str(mount_point)

        return None

    def _cleanup_service_volumes(self, svc_info: ServiceInformation) -> None:
        """Unmount and cleanup volumes for the service."""
        realm_name = svc_info.realm
        instance_name = svc_info.name
        storage_root = pathlib.Path(self.encrypted_storage_dir) / realm_name

        # Unmount versioned-read-only volumes
        mounts_root = storage_root / "mounts" / instance_name
        if mounts_root.exists():
            for mnt in mounts_root.iterdir():
                if mnt.is_dir() and self._is_mountpoint(str(mnt)):
                    self._unmount_encrypted_volume(mnt)

    def persist_volume(
        self,
        realm: str,
        volume_name: str,
        destination_source: str,
        encryption_key: str,
    ) -> Optional[str]:
        """
        Create a new encrypted versioned volume from a tmp-writeable volume.
        """
        if not self.encrypted_storage_dir:
            return None

        # Find the active tmp-writeable volume.
        # This implementation searches for any instance using this volume name.
        realm_root = pathlib.Path(self.encrypted_storage_dir) / realm
        tmp_root = realm_root / "tmp"

        source_dir = None
        for instance_dir in tmp_root.iterdir():
            vol_dir = instance_dir / volume_name
            if vol_dir.exists():
                source_dir = vol_dir
                break

        if not source_dir:
            logger.error(
                f"Could not find tmp-writeable volume {volume_name} "
                f"in realm {realm}"
            )
            return None

        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        dest_img = realm_root / f"{destination_source}.{timestamp}.img"

        logger.info(f"Persisting {source_dir} to {dest_img}")

        # Placeholder for actual image creation and encryption logic.
        # In practice:
        # 1. Create empty file (dd)
        # 2. Format as LUKS with encryption_key (cryptsetup)
        # 3. Open and format filesystem (mkfs)
        # 4. Mount and copy files (cp -a)
        # 5. Unmount and close
        try:
            # Simulation: Create a dummy file
            dest_img.touch()
            return str(dest_img)
        except Exception as e:
            logger.error(f"Failed to persist volume: {e}")
            return None

    def _is_mountpoint(self, path: str) -> bool:
        try:
            # Prefer /proc/self/mounts check for robustness
            mp = False
            with pathlib.Path("/proc/self/mounts").open() as f:
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
    # Footprinting support
    # ------------------------------------------------------------------

    def _handle_footprint_request(self, request: FootprintAction) -> None:
        """Handle a single footprinting request: footprint services and write
        YAML, then remove from cache.
        """
        if not self._footprint_request_cache:
            logger.debug(
                "Footprint request cache not initialized, cannot "
                "process footprint request"
            )
            return

        # Mark as in-progress
        logger.debug('marking footprint request "in-progress"')
        request.footprint_in_progress = True
        request.footprint_started_at = datetime.now()
        self._footprint_request_cache.update_footprint_request(request)

        # Determine targets
        def target_iterator(
            service_def: "ServiceDefinition",
        ) -> Iterable[ConfiguredServiceIdentifier]:
            if service_def.profiles and service_def.varieties:
                for profile, variety in itertools.product(
                    service_def.profiles.keys(), service_def.varieties.keys()
                ):
                    yield ConfiguredServiceIdentifier(
                        service_name=service_def.service_name,
                        realm=service_def.realm,
                        profile=profile,
                        variety=variety,
                    )
            elif service_def.profiles:
                for profile in service_def.profiles:
                    yield ConfiguredServiceIdentifier(
                        service_name=service_def.service_name,
                        realm=service_def.realm,
                        profile=profile,
                    )
            elif service_def.varieties:
                for variety in service_def.varieties:
                    yield ConfiguredServiceIdentifier(
                        service_name=service_def.service_name,
                        realm=service_def.realm,
                        variety=variety,
                    )
            else:
                yield ConfiguredServiceIdentifier(
                    service_name=service_def.service_name,
                    realm=service_def.realm,
                )

        targets: List[ConfiguredServiceIdentifier] = []
        if request.footprint_all_services:
            for svc_def in self.config_reader.service_definitions:
                for target in target_iterator(svc_def):
                    targets.append(target)

        else:
            targets = request.services or []
        logger.debug(f"footprint targets: {targets}")

        # Ensure system is unloaded before footprinting
        if self._active_services_cache.get_services():
            # If not unloaded, skip processing now
            return

        # Footprint each target sequentially
        for target in targets:
            logger.info(f"footprinting service: {target.service_name}")
            try:
                logger.info("pre-footprint")
                self._footprint_single_service(target)
                logger.info("post-footprint (before except)")
            except Exception as e:
                logger.error(
                    "Footprinting error for %s[%s][%s] - %s",
                    target.service_name,
                    target.profile,
                    target.variety,
                    e,
                )
            logger.info("post-footprint (after except)")

        # Remove the handled request from cache
        try:
            logger.debug(
                f"removing completed footprint request {request.request_id}"
            )
            current = self._footprint_request_cache.get_requests()
            remaining = [
                r for r in current if r.request_id != request.request_id
            ]
            self._footprint_request_cache.set_requests(remaining)
        except Exception as e:
            logger.error(
                "Failed to remove completed footprint request %s: %s",
                request.request_id,
                e,
            )

    def _target_service_instance_name(
        self, target: ConfiguredServiceIdentifier
    ) -> str:
        inst_name = (
            f"footprinter--{target.service_name}--{target.profile}--"
            f"{target.variety}"
        )
        return inst_name

    def _footprint_single_service(
        self,
        target: ConfiguredServiceIdentifier,
    ) -> None:
        """Footprint a single configured service/profile."""
        logger.info("entered _footprint_single_service")

        # Lookup service class first
        tmp_svc_info = ServiceInformation(
            name="temp",
            service=target.service_name,
            realm=target.realm,
            profile=target.profile,
            variety=target.variety,
            status=ServiceStatus.STARTING,
            info={},
        )
        service_cls = self._get_service_class_from_service_info(tmp_svc_info)
        if not service_cls:
            logger.error(
                "No provisionable service implementation found for type '%s'",
                target.service_name,
            )
            return

        # Measure pre state
        pre = HostResources.inspect_host()
        logger.info("pre-state resources: %s", pre)

        # Construct a unique service instance name
        inst_name = self._target_service_instance_name(target)

        # Activate the service
        logger.info(f"starting service {inst_name}")
        svc_info = ServiceInformation(
            name=inst_name,
            service=target.service_name,
            realm=target.realm,
            profile=target.profile,
            variety=target.variety,
            status=ServiceStatus.STARTING,
            info={},
        )
        self.update_active_services([svc_info])

        # Manually trigger start because the main loop is blocked by us
        self._start_service(svc_info, service_cls, datetime.now())
        # Persist the start_completed marker to cache
        self._active_services_cache.set_services([svc_info])

        # Wait for start completed marker
        self._wait_for_start_completed(inst_name, timeout=60.0)
        logger.info(f"service {inst_name} started successfully")

        # wait for configured run time
        reader = self.config_reader
        effective_service_def = reader.get_effective_service_definition(
            target.service_name,
            target.profile,
            target.variety,
            realm=target.realm,
        )
        footprint_config = effective_service_def.footprint
        time.sleep(footprint_config.run_time)

        # Measure post state
        post = HostResources.inspect_host()
        logger.info("post-state resources: %s", post)

        # Compute deltas
        usage = {
            "cpu_cores": max(
                0,
                pre.available_cpu_cores - post.available_cpu_cores,
            ),
            "memory_gb": max(0.0, pre.available_ram_gb - post.available_ram_gb),
            "vram_gb": max(0.0, pre.available_vram_gb - post.available_vram_gb),
        }

        # Persist to YAML
        logger.info(f"writing footprint usage for {target.service_name}")
        self._write_footprint_usage(
            SystemUsageDelta(
                service_name=target.service_name,
                profile=target.profile,
                variety=target.variety,
                usage=ServiceInstanceUsage(**usage),
            )
        )

        # Stop the service and restore unloaded state
        # Request no services active -> will mark existing as STOPPING
        logger.info(f"stopping service {inst_name}")
        self.update_active_services([])

        # Manually trigger stop because the main loop is blocked by us
        # We need the service info with status STOPPING
        active = self._active_services_cache.get_services()
        target_svc = next((s for s in active if s.name == inst_name), None)
        if target_svc:
            self._stop_service(target_svc, service_cls, datetime.now())
            # Persist stop_completed to cache
            self._active_services_cache.set_services([target_svc])

        self._wait_for_stop_completed(inst_name, timeout=60.0)

        # After stop, clear cache to keep system unloaded
        self._active_services_cache.set_services([])

    def _wait_for_start_completed(
        self,
        instance_name: str,
        timeout: float = 60.0,
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
                "footprinting anyway"
            ),
            instance_name,
        )

    def _wait_for_stop_completed(
        self,
        instance_name: str,
        timeout: float = 60.0,
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
            "Timeout waiting for service %s to stop; continuing",
            instance_name,
        )

    def _write_footprint_usage(
        self,
        system_usage_delta: "SystemUsageDelta",
    ) -> None:
        # ensure path is defined
        path = os.environ.get("OZWALD_FOOTPRINT_DATA")
        if not path:
            logger.error(
                "OZWALD_FOOTPRINT_DATA environment variable is not defined; "
                "cannot write footprint data"
            )
            return

        # read yaml file as list of ServiceInstanceUsage objects and substitute
        # the footprinted service usage record, if possible
        usage_records = []
        try:
            with open(path) as f:
                service_instance_usage_records = yaml.safe_load(f) or []
        except FileNotFoundError:
            pass
        written = False
        for usage_rec_dict in service_instance_usage_records:
            usage_rec = SystemUsageDelta(**usage_rec_dict)
            if (
                usage_rec.service_name == system_usage_delta.service_name
                and usage_rec.profile == system_usage_delta.profile
                and usage_rec.variety == system_usage_delta.variety
            ):
                usage_records.append(system_usage_delta)
                written = True
            else:
                usage_records.append(usage_rec)

        # sort the list of usage records by service_name, profile, variety
        def sortkey(rec: SystemUsageDelta):
            return rec.service_name, rec.profile, rec.variety

        usage_records.sort(key=sortkey)

        # add the record if it wasn't subbed in before
        if not written:
            usage_records.append(system_usage_delta)

        # write the updated yaml file
        with open(path, "w") as f:
            yaml.safe_dump([rec.model_dump() for rec in usage_records], f)

    def _init_service(
        self,
        service_info: ServiceInformation,
    ) -> ServiceInformation:
        """Init a new service def."""
        # read service definition
        service_def = self.config_reader.get_service_by_name(
            service_info.service,
            service_info.realm,
        )
        if not service_def:
            raise ValueError(
                "Service definition '" + service_info.service + "' "
                "not found in configuration",
            )

        # resolve and attach properties
        effective_def = self.config_reader.get_effective_service_definition(
            service_def,
            service_info.profile,
            service_info.variety,
            realm=service_info.realm,
        )
        service_info.properties = effective_def.properties

        service_info.status = ServiceStatus.STARTING
        return service_info


if __name__ == "__main__":
    # Entry point to run the provisioner backend daemon
    try:
        provisioner = SystemProvisioner.singleton()
        logger.info("Starting SystemProvisioner backend daemon...")
        provisioner.run_backend_daemon()
    except Exception as e:
        logger.error("Provisioner backend daemon exited with error: %s", e)
        raise
