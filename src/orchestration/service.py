import importlib
import inspect
import os
import pkgutil
import sys
import threading
from typing import ClassVar, Optional, Type

from config.reader import SystemConfigReader
from orchestration.models import (
    Cache,
    Service,
    ServiceDefinition,
    ServiceInformation,
)
from util.logger import get_logger

logger = get_logger(__name__)


class BaseProvisionableService(Service):
    _cache: Cache = None
    _service_info: Optional[ServiceInformation] = None
    service_type: ClassVar[str]

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

    # Lifecycle to be implemented by subclasses (e.g., ContainerService)

    def start(self):
        """Start the service (abstract in base)."""
        raise NotImplementedError("start must be implemented by subclasses")

    def stop(self):
        """Stop the service (abstract in base)."""
        raise NotImplementedError("stop must be implemented by subclasses")

    def get_service_information(self) -> ServiceInformation:
        """Return the service information."""
        return self._service_info

    def get_service_definition(self) -> ServiceDefinition:
        """Return the service definition for this service instance."""
        si = self.get_service_information()
        config_reader = SystemConfigReader.singleton()
        service_def = config_reader.get_service_by_name(si.service)
        if service_def is None:
            raise RuntimeError(
                f"Service definition for service {si.service} not found"
            )
        return service_def

    # (no container-specific helpers are implemented in the base class)

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
