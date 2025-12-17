import os
from pathlib import Path
from typing import Any, Dict, List, Optional

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
        # Top-level named volumes (normalized)
        self.volumes: Dict[str, Dict[str, Any]] = {}

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
        self._parse_volumes()
        self._parse_services()
        self._parse_provisioners()

    # ---------------- Internal helpers -----------------

    def _substitute_path_vars(self, value: str) -> str:
        """Restricted variable substitution for settings.

        Supports only ${SETTINGS_FILE_DIR} and ${OZWALD_PROJECT_ROOT_DIR}.
        """
        if not isinstance(value, str):
            return value
        # Compute supported variables
        settings_dir = str(self.config_path.parent.resolve())
        project_root = os.environ.get("OZWALD_PROJECT_ROOT_DIR", "")

        def repl(token: str, replacement: str, s: str) -> str:
            return s.replace(token, replacement)

        out = value
        if "${SETTINGS_FILE_DIR}" in out:
            out = repl("${SETTINGS_FILE_DIR}", settings_dir, out)
        if "${OZWALD_PROJECT_ROOT_DIR}" in out:
            if project_root:
                out = repl("${OZWALD_PROJECT_ROOT_DIR}", project_root, out)
            else:
                # Leave as-is; later validation can error if required
                pass
        # Collapse any accidental //
        out = out.replace("//", "/")
        return out

    def _parse_volumes(self) -> None:
        """Parse top-level volumes into normalized dict entries.

        Normalization:
        - bind: ensure absolute `source` after substitution
        - nfs: ensure `server` and `path` (or `source`) exist
        - named/tmpfs: store as-is (driver/options optional)
        """
        vols = self._raw_config.get("volumes", {}) or {}
        normalized: Dict[str, Dict[str, Any]] = {}
        for name, spec in vols.items():
            if not isinstance(spec, dict):
                logger.error("Volume '%s' spec must be a mapping", name)
                continue
            vtype = (spec.get("type") or "").strip()
            if vtype not in ("bind", "named", "tmpfs", "nfs"):
                raise ValueError(f"Volume {name}: unsupported type '{vtype}'")
            entry: Dict[str, Any] = {"type": vtype}
            if vtype == "bind":
                src = spec.get("source") or ""
                src = self._substitute_path_vars(src)
                if not src:
                    raise ValueError(f"Volume {name}: bind requires 'source'")
                # require absolute after substitution
                if not os.path.isabs(src):
                    raise ValueError(
                        f"Volume {name}: bind source must be absolute: {src}"
                    )
                entry["source"] = os.path.abspath(src)
            elif vtype == "nfs":
                server = spec.get("server") or ""
                source = spec.get("path") or spec.get("source") or ""
                if not server or not source:
                    raise ValueError(
                        f"Volume {name}: nfs requires 'server' and 'path'"
                    )
                entry["server"] = server
                entry["path"] = source
                if spec.get("options"):
                    entry["options"] = spec.get("options")
            elif vtype == "named":
                if spec.get("driver"):
                    entry["driver"] = spec.get("driver")
                if spec.get("options"):
                    entry["options"] = spec.get("options")
            elif vtype == "tmpfs":
                if spec.get("options"):
                    entry["options"] = spec.get("options")
            # Common optional fields
            if spec.get("scope"):
                entry["scope"] = spec.get("scope")
            if spec.get("lifecycle"):
                entry["lifecycle"] = spec.get("lifecycle")
            normalized[name] = entry
        self.volumes = normalized

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
                # Normalize profile-specific volumes (no implicit inherit);
                # merging happens at runtime by target precedence.
                prof_vols = self._normalize_service_volumes(
                    profile_data.get("volumes", [])
                )

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
                    volumes=prof_vols,
                )
                profiles.append(profile)

            # Parse varieties
            varieties_data = service_data.get("varieties", {}) or {}
            varieties = {}
            # Determine a default image if not specified at parent-level
            default_image_from_variety = None
            for variety_name, variety_data in varieties_data.items():
                v_vols = self._normalize_service_volumes(
                    variety_data.get("volumes", [])
                )
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
                    volumes=v_vols,
                )
                varieties[variety_name] = v
                if default_image_from_variety is None and v.image:
                    default_image_from_variety = v.image

            # Choose service image: explicit parent image, else first
            # variety image, else empty string
            service_image = parent_image or default_image_from_variety or ""

            # Normalize profiles to a dict keyed by profile name
            profiles_dict = {p.name: p for p in profiles}

            # Normalize and attach volumes for service (may use top-level)
            svc_vols = self._normalize_service_volumes(
                service_data.get("volumes", [])
            )

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
                volumes=svc_vols,
                profiles=profiles_dict,
                varieties=varieties,
            )
            self.services.append(service_def)

    def _normalize_service_volumes(self, raw_vols) -> List[str]:
        """Return a list of docker-ready volume strings.

        Supports:
        - mapping with name/target/read_only
        - shorthand "name:/target[:rw|ro]"
        - legacy bind string "/host:/ctr[:mode]" (absolute host required)
        """
        vols: List[str] = []
        if not raw_vols:
            return vols
        for entry in raw_vols:
            if isinstance(entry, dict):
                name = entry.get("name")
                target = entry.get("target") or ""
                ro = bool(entry.get("read_only", False))
                if not name or not target:
                    raise ValueError(
                        "Service volume mapping requires name and target"
                    )
                if not os.path.isabs(target):
                    raise ValueError(
                        f"Volume target must be absolute: {target}"
                    )
                spec = self.volumes.get(name)
                if not spec:
                    raise ValueError(f"Unknown volume name referenced: {name}")
                vtype = spec.get("type")
                mode = ":ro" if ro else ":rw"
                if vtype == "bind":
                    host = spec.get("source")
                    vols.append(f"{host}:{target}{mode}")
                elif vtype == "named":
                    vols.append(f"{name}:{target}{mode}")
                elif vtype == "nfs":
                    # Will be pre-mounted under OZWALD_NFS_MOUNTS/name
                    mount_root = os.environ.get("OZWALD_NFS_MOUNTS", "/exports")
                    host = os.path.join(mount_root, name)
                    vols.append(f"{host}:{target}{mode}")
                elif vtype == "tmpfs":
                    # For now, skip; could render --tmpfs later
                    raise ValueError(
                        "tmpfs volumes are not mountable via -v here"
                    )
            elif isinstance(entry, str):
                # Substitute tokens in bind host segment if present
                s = self._substitute_path_vars(entry)
                # If starts with '/', treat as bind string
                parts = s.split(":")
                if s.startswith("/"):
                    if len(parts) < 2:
                        raise ValueError(f"Invalid bind volume string: {entry}")
                    host = parts[0]
                    if not os.path.isabs(host):
                        raise ValueError(f"Bind host must be absolute: {host}")
                    vols.append(s)
                else:
                    # Shorthand name:/target[:mode]
                    if len(parts) < 2:
                        raise ValueError(f"Invalid volume shorthand: {entry}")
                    name = parts[0]
                    target = ":".join(parts[1:2])
                    mode = (":" + parts[2]) if len(parts) > 2 else ""
                    if not os.path.isabs(target):
                        raise ValueError(
                            f"Volume target must be absolute: {target}"
                        )
                    if name not in self.volumes:
                        raise ValueError(
                            f"Unknown volume name referenced: {name}"
                        )
                    spec = self.volumes[name]
                    if spec.get("type") == "bind":
                        host = spec.get("source")
                        # default mode if not supplied
                        mmode = mode or ":rw"
                        vols.append(f"{host}:{target}{mmode}")
                    elif spec.get("type") == "named":
                        vols.append(f"{name}:{target}{mode or ':rw'}")
                    elif spec.get("type") == "nfs":
                        mount_root = os.environ.get(
                            "OZWALD_NFS_MOUNTS", "/exports"
                        )
                        host = os.path.join(mount_root, name)
                        vols.append(f"{host}:{target}{mode or ':rw'}")
                    else:
                        raise ValueError(
                            f"Unsupported volume type for shorthand: {name}"
                        )
            else:
                raise ValueError("Unsupported volume entry type")
        return vols

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
