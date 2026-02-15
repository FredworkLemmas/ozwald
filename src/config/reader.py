import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import yaml

from orchestration.models import (
    Cache,
    EffectiveServiceDefinition,
    FootprintConfig,
    Host,
    Network,
    PersistentServiceDeclaration,
    Provisioner,
    Realm,
    Resource,
    ServiceDefinition,
    ServiceDefinitionProfile,
    ServiceDefinitionVariety,
)
from util.logger import get_logger

_system_config_reader = None
logger = get_logger(__name__)


class ConfigReader:
    """Reads and parses Ozwald configuration files, hydrating Pydantic models
    from YAML configuration.
    """

    def __init__(self, config_path: str):
        """Initialize ConfigReader with a path to a YAML configuration file.

        Args:
            config_path: Path to the YAML configuration file

        """
        self.config_path = Path(config_path)
        self._raw_config = None

        # Initialize attributes that will be populated
        self.hosts: List[Host] = []
        self.service_definitions: List[ServiceDefinition] = []
        self.provisioners: List[Provisioner] = []
        self._networks_list: List[Network] = []
        self.realms: Dict[str, Realm] = {}
        # Top-level named volumes (normalized)
        self.volumes: Dict[str, Dict[str, Any]] = {}

        # Load and parse configuration
        self._load_config()
        self._parse_config()

    def _load_config(self) -> None:
        """Load YAML configuration from file."""
        if not self.config_path.exists():
            raise FileNotFoundError(
                f"Configuration file not found: {self.config_path}",
            )

        with Path(self.config_path).open() as f:
            self._raw_config = yaml.safe_load(f)

        if not self._raw_config:
            raise ValueError(
                f"Empty or invalid YAML configuration: {self.config_path}",
            )

    def _parse_config(self) -> None:
        """Parse raw configuration and hydrate models."""
        self._parse_hosts()
        self._parse_volumes()
        self._parse_realms()
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
                if not Path(src).is_absolute():
                    raise ValueError(
                        f"Volume {name}: bind source must be absolute: {src}",
                    )
                entry["source"] = os.path.abspath(src)
            elif vtype == "nfs":
                server = spec.get("server") or ""
                source = spec.get("path") or spec.get("source") or ""
                if not server or not source:
                    raise ValueError(
                        f"Volume {name}: nfs requires 'server' and 'path'",
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

        for i, host_data in enumerate(hosts_data):
            resources = []
            for j, resource_data in enumerate(host_data.get("resources", [])):
                if "name" not in resource_data:
                    raise KeyError(
                        f"Resource at index {j} for host index {i} "
                        "is missing 'name'",
                    )
                resource = Resource(
                    name=resource_data["name"],
                    type=resource_data["type"],
                    unit=resource_data["unit"],
                    value=resource_data["value"],
                    related_resources=resource_data.get("related_resources"),
                    extended_attributes=resource_data.get(
                        "extended_attributes",
                    ),
                )
                resources.append(resource)

            if "name" not in host_data:
                raise KeyError(f"Host entry at index {i} is missing 'name'")
            if "ip" not in host_data:
                raise KeyError(f"Host entry at index {i} is missing 'ip'")

            host = Host(
                name=host_data["name"],
                ip=host_data["ip"],
                resources=resources,
            )
            self.hosts.append(host)

    def _parse_realms(self) -> None:
        """Parse realms section and create Realm models."""
        realms_data = self._raw_config.get("realms", {})
        for realm_name, realm_data in realms_data.items():
            if realm_data is None:
                realm_data = {}

            networks = self._parse_networks(
                realm_data.get("networks", []),
                realm_name,
            )
            services = self._parse_service_definitions(
                realm_data.get("service-definitions", []),
                realm_name,
            )
            persistent_services = self._parse_persistent_services(
                realm_data.get("persistent-services", []),
                realm_name,
            )

            self.realms[realm_name] = Realm(
                name=realm_name,
                networks=networks,
                service_definitions=services,
                persistent_services=persistent_services,
            )

    def _parse_networks(self, networks_data: list, realm: str) -> list[Network]:
        """Parse networks section and create Network models."""
        parsed_networks = []
        for i, network_data in enumerate(networks_data):
            if "name" not in network_data:
                raise KeyError(
                    f"Network entry at index {i} in realm '{realm}' "
                    "is missing 'name'"
                )
            network = Network(
                name=network_data["name"],
                type=network_data.get("type", "bridge"),
                realm=realm,
            )
            self._networks_list.append(network)
            parsed_networks.append(network)
        return parsed_networks

    def _parse_service_definitions(
        self,
        services_data: list,
        realm: str,
    ) -> list[ServiceDefinition]:
        """
        Parse service-definitions section and create ServiceDefinition models.

        Supports service-level profiles and varieties. Varieties behave like
        alternative definitions (e.g., different container images) that can
        override docker-compose-like fields; parent-level fields are used as
        defaults and merged appropriately.
        """
        parsed_services = []
        for i, service_data in enumerate(services_data):
            if "name" not in service_data:
                raise KeyError(
                    f"Service entry at index {i} in realm '{realm}' "
                    "is missing 'name'"
                )
            if "type" not in service_data:
                raise KeyError(
                    f"Service entry at index {i} in realm '{realm}' "
                    "is missing 'type'"
                )

            # Parent (service-level) docker-compose-like fields
            parent_env = service_data.get("environment", {}) or {}
            parent_properties = service_data.get("properties", {}) or {}
            parent_depends_on = service_data.get("depends_on", []) or []
            parent_command = service_data.get("command")
            parent_entrypoint = service_data.get("entrypoint")
            parent_env_file = service_data.get("env_file", []) or []
            parent_image = service_data.get("image", "") or ""
            parent_networks = service_data.get("networks", []) or ["default"]
            parent_footprint_data = service_data.get("footprint")
            parent_footprint = (
                FootprintConfig(**parent_footprint_data)
                if parent_footprint_data
                else None
            )

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

                env = profile_data.get("environment", {}) or {}
                properties = profile_data.get("properties", {}) or {}
                depends_on = profile_data.get("depends_on", []) or []
                env_file = profile_data.get("env_file", []) or []
                networks = profile_data.get("networks")

                # Normalize profile-specific volumes (no implicit inherit);
                # merging happens at runtime by target precedence.
                prof_vols = self._normalize_service_volumes(
                    profile_data.get("volumes", []),
                )

                # footprint
                profile_footprint_data = profile_data.get("footprint")
                profile_footprint = (
                    FootprintConfig(**profile_footprint_data)
                    if profile_footprint_data
                    else None
                )

                profile = ServiceDefinitionProfile(
                    name=name,
                    description=profile_data.get("description"),
                    image=profile_data.get("image"),
                    depends_on=depends_on,
                    command=profile_data.get("command"),
                    entrypoint=profile_data.get("entrypoint"),
                    env_file=env_file,
                    environment=env,
                    properties=properties,
                    volumes=prof_vols,
                    networks=networks,
                    footprint=profile_footprint,
                )
                profiles.append(profile)

            # Parse varieties
            varieties_data = service_data.get("varieties", {}) or {}
            varieties = {}
            # Determine a default image if not specified at parent-level
            default_image_from_variety = None
            for variety_name, variety_data in varieties_data.items():
                v_vols = self._normalize_service_volumes(
                    variety_data.get("volumes", []),
                )
                v_footprint_data = variety_data.get("footprint")
                v_footprint = (
                    FootprintConfig(**v_footprint_data)
                    if v_footprint_data
                    else None
                )
                v = ServiceDefinitionVariety(
                    image=variety_data.get("image"),
                    depends_on=variety_data.get("depends_on"),
                    command=variety_data.get("command"),
                    entrypoint=variety_data.get("entrypoint"),
                    env_file=variety_data.get("env_file"),
                    environment=variety_data.get("environment"),
                    properties=variety_data.get("properties"),
                    volumes=v_vols,
                    networks=variety_data.get("networks"),
                    footprint=v_footprint,
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
                service_data.get("volumes", []),
            )

            service_def = ServiceDefinition(
                service_name=service_data["name"],
                realm=realm,
                type=service_data["type"],
                description=service_data.get("description"),
                image=service_image,
                depends_on=parent_depends_on,
                command=parent_command,
                entrypoint=parent_entrypoint,
                env_file=parent_env_file,
                environment=parent_env,
                properties=parent_properties,
                volumes=svc_vols,
                networks=parent_networks,
                footprint=parent_footprint,
                profiles=profiles_dict,
                varieties=varieties,
            )
            self.service_definitions.append(service_def)
            parsed_services.append(service_def)

        return parsed_services

    def _parse_persistent_services(
        self,
        persistent_services_data: list,
        realm: str,
    ) -> list[PersistentServiceDeclaration]:
        """Parse persistent-services section and create
        PersistentServiceDeclaration models.
        """
        parsed_persistent_services = []
        for i, ps_data in enumerate(persistent_services_data):
            if "name" not in ps_data:
                raise KeyError(
                    f"Persistent service entry at index {i} in realm '{realm}' "
                    "is missing 'name'"
                )
            if "service" not in ps_data:
                raise KeyError(
                    f"Persistent service entry at index {i} in realm '{realm}' "
                    "is missing 'service'"
                )

            ps_decl = PersistentServiceDeclaration(
                name=ps_data["name"],
                service=ps_data["service"],
                realm=realm,
                variety=ps_data.get("variety"),
                profile=ps_data.get("profile"),
            )
            parsed_persistent_services.append(ps_decl)
        return parsed_persistent_services

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
                        "Service volume mapping requires name and target",
                    )
                if not Path(target).is_absolute():
                    raise ValueError(
                        f"Volume target must be absolute: {target}",
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
                        "tmpfs volumes are not mountable via -v here",
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
                    if not Path(host).is_absolute():
                        raise ValueError(f"Bind host must be absolute: {host}")
                    vols.append(s)
                else:
                    # Shorthand name:/target[:mode]
                    if len(parts) < 2:
                        raise ValueError(f"Invalid volume shorthand: {entry}")
                    name = parts[0]
                    target = ":".join(parts[1:2])
                    mode = (":" + parts[2]) if len(parts) > 2 else ""
                    if not Path(target).is_absolute():
                        raise ValueError(
                            f"Volume target must be absolute: {target}",
                        )
                    if name not in self.volumes:
                        raise ValueError(
                            f"Unknown volume name referenced: {name}",
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
                            "OZWALD_NFS_MOUNTS",
                            "/exports",
                        )
                        host = os.path.join(mount_root, name)
                        vols.append(f"{host}:{target}{mode or ':rw'}")
                    else:
                        raise ValueError(
                            f"Unsupported volume type for shorthand: {name}",
                        )
            else:
                raise ValueError("Unsupported volume entry type")
        return vols

    def _parse_provisioners(self) -> None:
        """Parse top-level provisioners into Provisioner models."""
        provisioners_data = self._raw_config.get("provisioners", [])
        for i, prov_data in enumerate(provisioners_data):
            prov_cache = None
            prov_cache_data = prov_data.get("cache")
            if prov_cache_data:
                if "type" not in prov_cache_data:
                    raise KeyError(
                        f"Cache for provisioner at index {i} is missing 'type'",
                    )
                prov_cache = Cache(
                    type=prov_cache_data["type"],
                    parameters=prov_cache_data.get("parameters"),
                )

            if "name" not in prov_data:
                raise KeyError(
                    f"Provisioner entry at index {i} is missing 'name'",
                )
            if "host" not in prov_data:
                raise KeyError(
                    f"Provisioner entry at index {i} is missing 'host'",
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

    def get_network_by_name(self, name: str, realm: str) -> Optional[Network]:
        """Get a network by name and realm."""
        for network in self._networks_list:
            if network.name == name and network.realm == realm:
                return network
        return None

    def get_service_by_name(
        self,
        service_name: str,
        realm: str,
    ) -> Optional[ServiceDefinition]:
        """Get a service definition by service_name and realm."""
        for service in self.service_definitions:
            if service.service_name == service_name and service.realm == realm:
                return service
        return None

    def get_effective_service_definition(
        self,
        service: str | ServiceDefinition,
        profile: str | None,
        variety: str | None,
        realm: str | None = None,
    ) -> EffectiveServiceDefinition:
        """Get the effective service definition by merging base, variety, and
        profile fields.
        """
        if isinstance(service, str):
            if realm is None:
                raise ValueError(
                    f"realm is required when service is a string: {service}",
                )
            sd = self.get_service_by_name(service, realm)
            if not sd:
                raise ValueError(
                    f"Service definition not found: {service} in realm {realm}",
                )
        else:
            sd = service

        base_env = sd.environment or {}
        base_props = sd.properties or {}
        base_depends_on = sd.depends_on or []
        base_command = sd.command
        base_entrypoint = sd.entrypoint
        base_env_file = sd.env_file
        base_image = sd.image
        base_vols = list(sd.volumes or [])
        base_networks = sd.networks or []

        v = (sd.varieties or {}).get(variety) if variety else None
        v_env = (v.environment if v else None) or {}
        v_props = (v.properties if v else None) or {}
        v_depends_on = (v.depends_on if v else None) or []
        v_command = v.command if v else None
        v_entrypoint = v.entrypoint if v else None
        v_env_file = (v.env_file if v else None) or None
        v_image = (v.image if v else None) or None
        v_vols = list(getattr(v, "volumes", []) or [])
        v_networks = list(getattr(v, "networks", []) or [])

        p = (sd.profiles or {}).get(profile) if profile else None
        p_env = (p.environment if p else None) or {}
        p_props = (p.properties if p else None) or {}
        p_depends_on = (p.depends_on if p else None) or []
        p_command = p.command if p else None
        p_entrypoint = p.entrypoint if p else None
        p_env_file = (p.env_file if p else None) or None
        p_image = (p.image if p else None) or None
        p_vols = list(getattr(p, "volumes", []) or [])
        p_networks = list(getattr(p, "networks", []) or [])

        merged_env = {**base_env, **v_env, **p_env}
        merged_props = {**base_props, **v_props, **p_props}

        def _target_of(vol_spec: str) -> str:
            try:
                _host, rest = vol_spec.split(":", 1)
                target = rest.split(":", 1)[0]
                return target
            except Exception:
                return ""

        def _merge_volumes(
            base_list: list[str],
            var_list: list[str],
            prof_list: list[str],
        ) -> list[str]:
            order: list[str] = []
            by_target: dict[str, str] = {}

            def add_many(lst: list[str]):
                for spec in lst:
                    t = _target_of(spec)
                    if not t:
                        continue
                    if t not in by_target:
                        order.append(t)
                    by_target[t] = spec

            add_many(base_list)
            add_many(var_list)
            add_many(prof_list)
            return [by_target[t] for t in order]

        merged_vols = _merge_volumes(base_vols, v_vols, p_vols)

        def _merge_footprint(base, var, prof) -> FootprintConfig | None:
            res_dict = {}
            for config in [base, var, prof]:
                if config:
                    res_dict.update(
                        config.model_dump(by_alias=True, exclude_none=True),
                    )
            return FootprintConfig(**res_dict) if res_dict else None

        merged_footprint = _merge_footprint(
            sd.footprint,
            v.footprint if v else None,
            p.footprint if p else None,
        )

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

        return EffectiveServiceDefinition(
            realm=sd.realm,
            image=choose(p_image, v_image, base_image) or "",
            environment=merged_env,
            properties=merged_props,
            depends_on=choose(p_depends_on, v_depends_on, base_depends_on)
            or [],
            command=choose(p_command, v_command, base_command),
            entrypoint=choose(p_entrypoint, v_entrypoint, base_entrypoint),
            env_file=choose(p_env_file, v_env_file, base_env_file) or [],
            volumes=merged_vols,
            networks=choose(p_networks, v_networks, base_networks)
            or ["default"],
            footprint=merged_footprint,
        )

    @property
    def persistent_services(self) -> Iterable[PersistentServiceDeclaration]:
        """Yield an iterator of all PersistentServiceDeclaration objects
        across all realms.
        """
        for realm in self.realms.values():
            if realm.persistent_services:
                yield from realm.persistent_services

    def networks(self) -> Iterable[Network]:
        """Yield an iterator of all Network objects across all realms."""
        return iter(self._networks_list)

    @property
    def defined_networks(self) -> Iterable[Network]:
        """Iterator that yields all networks defined as Network objects."""
        return iter(self._networks_list)

    # No action/mode lookups in simplified schema.


class SystemConfigReader(ConfigReader):
    @classmethod
    def singleton(cls):
        global _system_config_reader
        if not _system_config_reader:
            config_path = os.environ.get("OZWALD_CONFIG", "ozwald.yml")
            _system_config_reader = cls(config_path)
        return _system_config_reader
