import pytest
import yaml

from config.reader import ConfigReader
from orchestration.models import (
    Host,
    Resource,
    ResourceType,
    ServiceDefinition,
)

# ============================================================================
# Initialization and Loading Tests
# ============================================================================


class TestConfigReaderInitialization:
    """Tests for ConfigReader initialization and file loading."""

    def test_init_with_valid_config_file(self, sample_config_file):
        """Verify that ConfigReader successfully initializes with a valid
        configuration file and populates all expected attributes.
        """
        reader = ConfigReader(str(sample_config_file))

        assert reader.config_path == sample_config_file
        assert reader.hosts is not None
        assert reader.services is not None
        assert reader.provisioners is not None

    def test_init_with_minimal_config(self, minimal_config_file):
        """Verify that ConfigReader can handle minimal valid configuration
        with empty lists for hosts, services, and provisioners.
        """
        reader = ConfigReader(str(minimal_config_file))

        assert len(reader.hosts) == 0
        assert len(reader.services) == 0
        assert len(reader.provisioners) == 0

    def test_init_with_nonexistent_file(self, tmp_path):
        """Verify that ConfigReader raises FileNotFoundError when
        initialized with a path to a non-existent file.
        """
        nonexistent_file = tmp_path / "does_not_exist.yml"

        with pytest.raises(FileNotFoundError) as exc_info:
            ConfigReader(str(nonexistent_file))

        assert "Configuration file not found" in str(exc_info.value)

    def test_init_with_empty_file(self, empty_config_file):
        """Verify that ConfigReader raises ValueError when initialized
        with an empty YAML file.
        """
        with pytest.raises(
            ValueError,
            match=r"Empty or invalid YAML configuration",
        ):
            ConfigReader(str(empty_config_file))

    def test_init_with_invalid_yaml(self, invalid_yaml_file):
        """Verify that ConfigReader raises an exception when initialized
        with a file containing invalid YAML syntax.
        """
        with pytest.raises(yaml.YAMLError):
            ConfigReader(str(invalid_yaml_file))

    # orchestrator section no longer exists in simplified schema


# ============================================================================
# Host Parsing Tests
# ============================================================================


class TestHostParsing:
    """Tests for parsing host configurations."""

    def test_hosts_are_parsed(self, sample_config_file):
        """Verify that all hosts from the configuration are parsed
        and converted to Host model instances.
        """
        reader = ConfigReader(str(sample_config_file))
        print(f"hosts: {reader.hosts}")
        assert len(reader.hosts) == 2
        print(f"types: {[type(h) for h in reader.hosts]}")
        assert all(isinstance(host, Host) for host in reader.hosts)

    def test_host_attributes(self, sample_config_file):
        """Verify that host attributes (name, ip) are correctly
        parsed from the configuration.
        """
        reader = ConfigReader(str(sample_config_file))

        jamma = next(h for h in reader.hosts if h.name == "jamma")
        assert jamma.name == "jamma"
        assert jamma.ip == "192.168.0.211"

        bitty = next(h for h in reader.hosts if h.name == "bitty")
        assert bitty.name == "bitty"
        assert bitty.ip == "192.168.0.254"

    def test_host_resources_are_parsed(self, sample_config_file):
        """Verify that resources for each host are correctly parsed including
        name, type, unit, value, related_resources, and
        extended_attributes.
        """
        reader = ConfigReader(str(sample_config_file))

        jamma = next(h for h in reader.hosts if h.name == "jamma")
        assert len(jamma.resources) == 5  # 2 GPUs, 2 VRAM, 1 memory
        assert all(isinstance(r, Resource) for r in jamma.resources)

        # Check GPU resource
        gpu_resource = jamma.resources[0]
        assert gpu_resource.name == "gpu-0"
        assert gpu_resource.type == ResourceType.GPU
        assert gpu_resource.unit == "device"
        assert gpu_resource.value == 1.0
        assert gpu_resource.related_resources == ["vram-0"]
        assert gpu_resource.extended_attributes["id"] == ":0"
        assert gpu_resource.extended_attributes["gpu_type"] == "nvidia"

        # Check VRAM resource
        vram_resource = jamma.resources[1]
        assert vram_resource.name == "vram-0"
        assert vram_resource.type == ResourceType.VRAM
        assert vram_resource.unit == "GB"
        assert vram_resource.value == 8.0
        assert vram_resource.related_resources == ["gpu-0"]
        assert vram_resource.extended_attributes is None

        # Check memory resource
        memory_resource = jamma.resources[4]
        assert memory_resource.name == "memory"
        assert memory_resource.type == ResourceType.MEMORY
        assert memory_resource.unit == "GB"
        assert memory_resource.value == 96.0
        assert memory_resource.related_resources is None

    def test_host_resources_without_extended_attributes(
        self,
        sample_config_file,
    ):
        """Verify that resources without extended_attributes (like VRAM
        or memory) are correctly parsed with extended_attributes set to None.
        """
        reader = ConfigReader(str(sample_config_file))

        jamma = next(h for h in reader.hosts if h.name == "jamma")
        vram_resource = jamma.resources[1]
        assert vram_resource.extended_attributes is None

        memory_resource = jamma.resources[4]
        assert memory_resource.extended_attributes is None

    def test_resource_relationships(self, sample_config_file):
        """Verify that related_resources correctly link GPU and VRAM
        resources.
        """
        reader = ConfigReader(str(sample_config_file))

        jamma = next(h for h in reader.hosts if h.name == "jamma")

        # GPU-0 should be related to vram-0
        gpu_0 = next(r for r in jamma.resources if r.name == "gpu-0")
        assert "vram-0" in gpu_0.related_resources

        # VRAM-0 should be related to gpu-0
        vram_0 = next(r for r in jamma.resources if r.name == "vram-0")
        assert "gpu-0" in vram_0.related_resources

    def test_multiple_gpus_with_vram(self, sample_config_file):
        """Verify that hosts with multiple GPUs have correct resources
        and relationships for each GPU-VRAM pair.
        """
        reader = ConfigReader(str(sample_config_file))

        jamma = next(h for h in reader.hosts if h.name == "jamma")

        # Check GPU-1 and VRAM-1
        gpu_1 = next(r for r in jamma.resources if r.name == "gpu-1")
        assert gpu_1.type == ResourceType.GPU
        assert gpu_1.extended_attributes["id"] == ":1"
        assert "vram-1" in gpu_1.related_resources

        vram_1 = next(r for r in jamma.resources if r.name == "vram-1")
        assert vram_1.type == ResourceType.VRAM
        assert vram_1.value == 8.0
        assert "gpu-1" in vram_1.related_resources


# ============================================================================
# Service Parsing Tests
# ============================================================================


class TestServiceParsing:
    """Tests for parsing service definition configurations."""

    def test_services_are_parsed(self, sample_config_file):
        """Verify that all service definitions are parsed and converted
        to ServiceDefinition model instances.
        """
        reader = ConfigReader(str(sample_config_file))

        assert len(reader.services) == 2
        assert all(
            isinstance(svc, ServiceDefinition) for svc in reader.services
        )

    def test_service_attributes(self, sample_config_file):
        """Verify that service attributes (service_name, type, description,
        environment, varieties) are correctly parsed.
        """
        reader = ConfigReader(str(sample_config_file))

        qwen_service = next(
            s for s in reader.services if s.service_name == "qwen1.5-vllm"
        )
        assert qwen_service.service_name == "qwen1.5-vllm"
        # `type` is now a string, not an enum
        assert qwen_service.type == "container"
        assert qwen_service.description == "DeepSeek Qwen 1.5B"
        assert "MODEL_NAME" in qwen_service.environment
        assert (
            qwen_service.environment["MODEL_NAME"]
            == "deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B"
        )
        # Varieties parsed
        assert isinstance(qwen_service.varieties, dict)
        assert "nvidia" in qwen_service.varieties
        assert "cpu-only" in qwen_service.varieties
        assert (
            qwen_service.varieties["nvidia"].image == "openai-api-vllm.nvidia"
        )
        assert (
            qwen_service.varieties["cpu-only"].image
            == "openai-api-vllm.cpu-only"
        )
        # Service image defaults to parent image or first variety image
        assert qwen_service.image in (
            "openai-api-vllm.nvidia",
            "openai-api-vllm.cpu-only",
        )

    def test_service_profiles_are_parsed(self, sample_config_file):
        """Verify that service profiles with their environment are
        correctly parsed and associated with services.
        """
        reader = ConfigReader(str(sample_config_file))

        qwen_service = next(
            s for s in reader.services if s.service_name == "qwen1.5-vllm"
        )
        assert len(qwen_service.profiles) == 2

        embed_profile = qwen_service.profiles["embed"]
        assert embed_profile.name == "embed"
        assert embed_profile.environment["GPU"] is True
        assert embed_profile.environment["GPU_MEMORY_UTILIZATION"] == 0.7
        assert embed_profile.environment["MAX_MODEL_LEN"] == 1100

    def test_profile_inherits_and_overrides_base_environment(
        self,
        tmp_path,
        sample_config_dict,
    ):
        """Verify that a profile's environment inherits keys from the base
        service environment and overrides conflicting keys.
        Base < Profile precedence for environment.
        """
        # Arrange: clone sample config and add an override for MODEL_NAME in
        # profile
        cfg = sample_config_dict
        # Ensure base has MODEL_NAME
        svc = next(s for s in cfg["services"] if s["name"] == "qwen1.5-vllm")
        svc["environment"]["MODEL_NAME"] = "base-model/name"
        # Make profiles a list case and override MODEL_NAME inside 'embed'
        for p in svc["profiles"]:
            if p["name"] == "embed":
                p.setdefault("environment", {})
                p["environment"]["MODEL_NAME"] = "profile-model/name"

        # Write temp config
        pth = tmp_path / "config.yml"
        import yaml

        pth.write_text(yaml.safe_dump(cfg))

        # Act
        reader = ConfigReader(str(pth))
        qwen_service = next(
            s for s in reader.services if s.service_name == "qwen1.5-vllm"
        )
        embed_profile = qwen_service.profiles["embed"]

        # Assert: inherited non-conflicting key and overridden conflicting key
        assert embed_profile.environment["MODEL_NAME"] == "profile-model/name"
        # Also ensure another base key remains present (if any). If none,
        # assert presence of override keys
        assert "GPU_MEMORY_UTILIZATION" in embed_profile.environment

    def test_variety_overrides_base_but_profile_overrides_variety(
        self,
        tmp_path,
        sample_config_dict,
    ):
        """Verify precedence order for environment values: base < variety <
        profile.
        """
        cfg = sample_config_dict
        svc = next(s for s in cfg["services"] if s["name"] == "qwen1.5-vllm")
        # Base value
        svc["environment"]["FOO"] = "base"
        # Ensure varieties exist and set FOO at variety level
        svc.setdefault("varieties", {})
        svc["varieties"].setdefault("cpu-only", {})
        svc["varieties"]["cpu-only"].setdefault("environment", {})
        svc["varieties"]["cpu-only"]["environment"]["FOO"] = "variety"
        # Ensure profile exists and sets FOO
        for p in svc["profiles"]:
            if p["name"] == "embed":
                p.setdefault("environment", {})
                p["environment"]["FOO"] = "profile"

        # Write temp config
        pth = tmp_path / "config.yml"
        import yaml

        pth.write_text(yaml.safe_dump(cfg))

        # Act
        reader = ConfigReader(str(pth))
        qwen = next(
            s for s in reader.services if s.service_name == "qwen1.5-vllm"
        )
        # Variety wins over base at the variety level
        assert (
            qwen.varieties["cpu-only"].environment.get("FOO", "base")
            == "variety"
        )
        # Profile wins over both
        embed = qwen.profiles["embed"]
        assert embed.environment["FOO"] == "profile"

    def test_service_without_profiles(self, sample_config_file):
        """Verify that services without profiles have an empty profiles list."""
        reader = ConfigReader(str(sample_config_file))

        chunker_service = next(
            s for s in reader.services if s.service_name == "chunker"
        )
        assert len(chunker_service.profiles) == 0


# (Actions removed from simplified schema)


class TestProvisionersParsing:
    """Tests for parsing top-level provisioners configuration."""

    def test_provisioners_are_parsed(self, sample_config_file):
        reader = ConfigReader(str(sample_config_file))
        assert len(reader.provisioners) == 2
        names = sorted([p.name for p in reader.provisioners])
        assert names == ["bitty", "jamma"]

    def test_provisioner_without_cache(
        self,
        config_with_provisioner_without_cache_file,
    ):
        reader = ConfigReader(str(config_with_provisioner_without_cache_file))
        assert len(reader.provisioners) == 1
        test_prov = reader.provisioners[0]
        assert test_prov.name == "test-provisioner"
        assert test_prov.cache is None


# ============================================================================
# Utility Method Tests
# ============================================================================


class TestUtilityMethods:
    """Tests for ConfigReader utility/lookup methods."""

    def test_get_host_by_name_found(self, sample_config_file):
        """Verify that get_host_by_name returns the correct Host
        when a matching name is found.
        """
        reader = ConfigReader(str(sample_config_file))

        host = reader.get_host_by_name("jamma")
        assert host is not None
        assert host.name == "jamma"
        assert host.ip == "192.168.0.211"

    def test_get_host_by_name_not_found(self, sample_config_file):
        """Verify that get_host_by_name returns None when
        no matching host is found.
        """
        reader = ConfigReader(str(sample_config_file))

        host = reader.get_host_by_name("nonexistent")
        assert host is None

    def test_get_service_by_name_found(self, sample_config_file):
        """Verify that get_service_by_name returns the correct ServiceDefinition
        when a matching service_name is found.
        """
        reader = ConfigReader(str(sample_config_file))

        service = reader.get_service_by_name("qwen1.5-vllm")
        assert service is not None
        assert service.service_name == "qwen1.5-vllm"
        assert service.type == "container"

    def test_get_service_by_name_not_found(self, sample_config_file):
        """Verify that get_service_by_name returns None when
        no matching service is found.
        """
        reader = ConfigReader(str(sample_config_file))

        service = reader.get_service_by_name("nonexistent")
        assert service is None

    # Actions and modes are removed in the simplified schema.


# ============================================================================
# Integration Tests
# ============================================================================


class TestIntegration:
    """Integration tests for ConfigReader with complete workflows."""

    def test_full_configuration_parsing(self, sample_config_file):
        """Integration test: Verify that a complete configuration file
        is parsed correctly with all sections populated.
        """
        reader = ConfigReader(str(sample_config_file))

        # Verify sections are populated per simplified schema
        assert len(reader.hosts) > 0
        assert len(reader.services) > 0
        assert len(reader.provisioners) > 0

    def test_pathlib_path_initialization(self, sample_config_file):
        """Verify that ConfigReader accepts both string and Path objects
        for initialization.
        """
        reader_from_str = ConfigReader(str(sample_config_file))
        reader_from_path = ConfigReader(sample_config_file)

        assert reader_from_str.config_path == reader_from_path.config_path
        assert len(reader_from_str.hosts) == len(reader_from_path.hosts)


# ============================================================================
# Volumes in profiles and varieties
# ============================================================================


class TestVolumesInProfilesVarieties:
    def test_volumes_parsed_and_normalized(self, tmp_path):
        cfg_dir = tmp_path / "cfg"
        cfg_dir.mkdir()
        host1 = cfg_dir / "host1"
        host1.mkdir()
        cfg = {
            "hosts": [],
            "services": [
                {
                    "name": "svc",
                    "type": "container",
                    "volumes": [
                        {
                            "name": "v1",
                            "target": "/t1",
                            "read_only": True,
                        },
                    ],
                    "varieties": {
                        "A": {"volumes": [{"name": "v2", "target": "/t2"}]},
                    },
                    "profiles": {
                        "P": {
                            "volumes": [
                                {
                                    "name": "v1",
                                    "target": "/t1",
                                    "read_only": False,
                                },
                            ],
                        },
                    },
                },
            ],
            "provisioners": [],
            "volumes": {
                "v1": {
                    "type": "bind",
                    "source": "${SETTINGS_FILE_DIR}/host1",
                },
                "v2": {"type": "named"},
            },
        }

        cfg_path = cfg_dir / "settings.yml"
        import yaml as _yaml

        cfg_path.write_text(_yaml.safe_dump(cfg))

        reader = ConfigReader(str(cfg_path))
        svc = reader.get_service_by_name("svc")
        assert svc is not None
        # base volume normalized to absolute bind host
        assert any(v.endswith(":/t1:ro") for v in svc.volumes)
        # variety volume normalized for named
        varA = svc.varieties.get("A")
        assert varA is not None
        assert varA.volumes == ["v2:/t2:rw"]
        # profile volume normalized override
        profP = svc.profiles.get("P")
        assert profP is not None
        assert any(v.endswith(":/t1:rw") for v in profP.volumes)
