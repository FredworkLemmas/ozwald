import os
from pathlib import Path
from typing import List, Optional

import yaml

from orchestration.models import (
    Cache,
    Host,
    Provisioner,
    Resource,
    ServiceDefinition,
    ServiceDefinitionProfile,
    ServiceDefinitionVariety,
)
from util.logger import get_logger

_system_config_reader = None
logger = get_logger(__name__)


class ConfigReader:
    """
    Reads and parses Ozwald configuration files, hydrating Pydantic models
    from YAML configuration.
    """

    def __init__(self, config_path: str):
        """
        Initialize ConfigReader with a path to a YAML configuration file.

        Args:
            config_path: Path to the YAML configuration file
        """
        self.config_path = Path(config_path)
        self._raw_config = None

        # Initialize attributes that will be populated
        self.hosts: List[Host] = []
        self.services: List[ServiceDefinition] = []
        self.provisioners: List[Provisioner] = []

        # Load and parse configuration
        self._load_config()
        self._parse_config()

    def _load_config(self) -> None:
        """Load YAML configuration from file."""
        if not self.config_path.exists():
            raise FileNotFoundError(
                f"Configuration file not found: {self.config_path}"
            )

        with open(self.config_path) as f:
            self._raw_config = yaml.safe_load(f)

        if not self._raw_config:
            raise ValueError(
                f"Empty or invalid YAML configuration: {self.config_path}"
            )

    def _parse_config(self) -> None:
        """Parse raw configuration and hydrate models."""
        self._parse_hosts()
        self._parse_services()
        self._parse_provisioners()

    def _parse_hosts(self) -> None:
        """Parse hosts section and create Host models."""
        hosts_data = self._raw_config.get("hosts", [])

        for host_data in hosts_data:
            resources = []
            for resource_data in host_data.get("resources", []):
                resource = Resource(
                    name=resource_data["name"],
                    type=resource_data["type"],
                    unit=resource_data["unit"],
                    value=resource_data["value"],
                    related_resources=resource_data.get("related_resources"),
                    extended_attributes=resource_data.get(
                        "extended_attributes"
                    ),
                )
                resources.append(resource)

            host = Host(
                name=host_data["name"], ip=host_data["ip"], resources=resources
            )
            self.hosts.append(host)

    def _parse_services(self) -> None:
        """Parse services section and create ServiceDefinition models.

        Supports service-level profiles and varieties. Varieties behave like
        alternative definitions (e.g., different container images) that can
        override docker-compose-like fields; parent-level fields are used as
        defaults and merged appropriately.
        """
        services_data = self._raw_config.get("services", [])

        for service_data in services_data:
            # Parent (service-level) docker-compose-like fields
            parent_env = service_data.get("environment", {}) or {}
            parent_depends_on = service_data.get("depends_on", []) or []
            parent_command = service_data.get("command")
            parent_entrypoint = service_data.get("entrypoint")
            parent_env_file = service_data.get("env_file", []) or []
            parent_image = service_data.get("image", "") or ""

            # Parse profiles (support both dict-of-dicts and list-of-dicts)
            profiles: List[ServiceDefinitionProfile] = []
            raw_profiles = service_data.get("profiles", {})
            if isinstance(raw_profiles, dict):
                items = raw_profiles.items()
            elif isinstance(raw_profiles, list):
                # Convert list of dicts to iterable of (name, data)
                items = ((p.get("name"), p) for p in raw_profiles)
            else:
                items = []

            for name, profile_data in items:
                if not name:
                    # Skip malformed profile without a name
                    continue
                # Merge with parent defaults, letting profile override
                env = {}
                env.update(parent_env)
                env.update(profile_data.get("environment", {}) or {})
                depends_on = list(parent_depends_on)
                if profile_data.get("depends_on"):
                    depends_on = profile_data.get("depends_on")
                env_file = list(parent_env_file)
                if profile_data.get("env_file"):
                    env_file = profile_data.get("env_file")

                profile = ServiceDefinitionProfile(
                    name=name,
                    description=profile_data.get("description"),
                    image=profile_data.get("image", parent_image) or None,
                    depends_on=depends_on,
                    command=profile_data.get("command", parent_command),
                    entrypoint=profile_data.get(
                        "entrypoint", parent_entrypoint
                    ),
                    env_file=env_file,
                    environment=env,
                )
                profiles.append(profile)

            # Parse varieties
            varieties_data = service_data.get("varieties", {}) or {}
            varieties = {}
            # Determine a default image if not specified at parent-level
            default_image_from_variety = None
            for variety_name, variety_data in varieties_data.items():
                v = ServiceDefinitionVariety(
                    image=variety_data.get("image", parent_image)
                    or parent_image
                    or "",
                    depends_on=variety_data.get("depends_on", [])
                    or parent_depends_on,
                    command=variety_data.get("command", parent_command),
                    entrypoint=variety_data.get(
                        "entrypoint", parent_entrypoint
                    ),
                    env_file=variety_data.get("env_file", [])
                    or parent_env_file,
                    environment=variety_data.get("environment", {}),
                )
                varieties[variety_name] = v
                if default_image_from_variety is None and v.image:
                    default_image_from_variety = v.image

            # Choose service image: explicit parent image, else first
            # variety image, else empty string
            service_image = parent_image or default_image_from_variety or ""

            # Normalize profiles to a dict keyed by profile name
            profiles_dict = {p.name: p for p in profiles}

            service_def = ServiceDefinition(
                service_name=service_data["name"],
                type=service_data["type"],
                description=service_data.get("description"),
                image=service_image,
                depends_on=parent_depends_on,
                command=parent_command,
                entrypoint=parent_entrypoint,
                env_file=parent_env_file,
                environment=parent_env,
                profiles=profiles_dict,
                varieties=varieties,
            )
            self.services.append(service_def)

    def _parse_provisioners(self) -> None:
        """Parse top-level provisioners into Provisioner models."""
        provisioners_data = self._raw_config.get("provisioners", [])
        for prov_data in provisioners_data:
            prov_cache = None
            prov_cache_data = prov_data.get("cache")
            if prov_cache_data:
                prov_cache = Cache(
                    type=prov_cache_data["type"],
                    parameters=prov_cache_data.get("parameters"),
                )

            provisioner = Provisioner(
                name=prov_data["name"],
                host=prov_data["host"],
                cache=prov_cache,
            )
            self.provisioners.append(provisioner)

    def get_host_by_name(self, name: str) -> Optional[Host]:
        """Get a host by name."""
        for host in self.hosts:
            if host.name == name:
                return host
        return None

    def get_service_by_name(
        self, service_name: str
    ) -> Optional[ServiceDefinition]:
        """Get a service definition by service_name."""
        result = None
        for service in self.services:
            if service.service_name == service_name:
                result = service
        logger.info(f"get_service_by_name{service_name} -> {result}")
        return result

    # No action/mode lookups in simplified schema.


class SystemConfigReader(ConfigReader):
    @classmethod
    def singleton(cls):
        global _system_config_reader
        if not _system_config_reader:
            _system_config_reader = cls(
                os.environ.get("OZWALD_CONFIG", "ozwald.yml")
            )
        return _system_config_reader
