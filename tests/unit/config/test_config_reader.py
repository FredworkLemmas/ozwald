import os
import tempfile

import pytest
import yaml

from config.reader import ConfigReader
from orchestration.models import (
    Host,
    Network,
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
        assert reader.service_definitions is not None
        assert reader.provisioners is not None

    def test_init_with_minimal_config(self, minimal_config_file):
        """Verify that ConfigReader can handle minimal valid configuration
        with empty lists for hosts, service_definitions, and provisioners.
        """
        reader = ConfigReader(str(minimal_config_file))

        assert len(reader.hosts) == 0
        assert len(reader.service_definitions) == 0
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
# Network Parsing Tests
# ============================================================================


class TestNetworkParsing:
    """Tests for parsing network configurations."""

    def test_networks_are_parsed(self, tmp_path):
        """Verify that all networks from the configuration are parsed."""
        cfg = {
            "realms": {
                "default": {
                    "networks": [{"name": "layer1"}, {"name": "layer2"}],
                }
            },
            "hosts": [],
            "provisioners": [],
        }
        cfg_path = tmp_path / "test_networks.yml"
        import yaml as _yaml

        cfg_path.write_text(_yaml.safe_dump(cfg))
        reader = ConfigReader(str(cfg_path))

        networks = list(reader.networks())
        assert len(networks) == 2
        assert all(isinstance(n, Network) for n in networks)
        assert networks[0].name == "layer1"
        assert networks[0].realm == "default"
        assert networks[1].name == "layer2"
        assert networks[1].realm == "default"

    def test_service_networks_are_parsed(self, tmp_path):
        """Verify that networks in service definitions are parsed."""
        cfg = {
            "realms": {
                "default": {
                    "service-definitions": [
                        {
                            "name": "svc1",
                            "type": "container",
                            "networks": ["layer1", "layer2"],
                            "profiles": {
                                "p1": {"networks": ["layer3"]},
                            },
                            "varieties": {
                                "v1": {"networks": ["layer4"]},
                            },
                        },
                        {
                            "name": "svc2",
                            "type": "container",
                            # No networks specified
                        },
                    ],
                }
            }
        }
        cfg_path = tmp_path / "test_svc_networks.yml"
        import yaml as _yaml

        cfg_path.write_text(_yaml.safe_dump(cfg))
        reader = ConfigReader(str(cfg_path))

        svc1 = reader.get_service_by_name("svc1", "default")
        assert svc1.networks == ["layer1", "layer2"]
        assert svc1.profiles["p1"].networks == ["layer3"]
        assert svc1.varieties["v1"].networks == ["layer4"]

        svc2 = reader.get_service_by_name("svc2", "default")
        # Should default to ["default"]
        assert svc2.networks == ["default"]


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

        assert len(reader.service_definitions) == 2
        assert all(
            isinstance(svc, ServiceDefinition)
            for svc in reader.service_definitions
        )

    def test_service_attributes(self, sample_config_file):
        """Verify that service attributes (service_name, type, description,
        environment, varieties) are correctly parsed.
        """
        reader = ConfigReader(str(sample_config_file))

        qwen_service = next(
            s
            for s in reader.service_definitions
            if s.service_name == "qwen1.5-vllm"
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
        correctly parsed and associated with service_definitions.
        """
        reader = ConfigReader(str(sample_config_file))

        qwen_service = next(
            s
            for s in reader.service_definitions
            if s.service_name == "qwen1.5-vllm"
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
        """Verify merge of environment values from base to profile via
        get_effective_service_definition.
        """
        cfg = sample_config_dict
        # Ensure base has MODEL_NAME
        svc = next(
            s
            for s in cfg["realms"]["default"]["service-definitions"]
            if s["name"] == "qwen1.5-vllm"
        )
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
        eff = reader.get_effective_service_definition(
            "qwen1.5-vllm", "embed", None, realm="default"
        )

        # Assert: inherited non-conflicting key and overridden conflicting key
        assert eff.environment["MODEL_NAME"] == "profile-model/name"
        assert "GPU_MEMORY_UTILIZATION" in eff.environment

    def test_variety_overrides_base_but_profile_overrides_variety(
        self,
        tmp_path,
        sample_config_dict,
    ):
        """Verify precedence order for environment values: base < variety <
        profile via get_effective_service_definition.
        """
        cfg = sample_config_dict
        svc = next(
            s
            for s in cfg["realms"]["default"]["service-definitions"]
            if s["name"] == "qwen1.5-vllm"
        )
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

        # Test effective with variety only
        eff_v = reader.get_effective_service_definition(
            "qwen1.5-vllm", None, "cpu-only", realm="default"
        )
        assert eff_v.environment["FOO"] == "variety"

        # Test effective with profile (overrides variety and base)
        eff_p = reader.get_effective_service_definition(
            "qwen1.5-vllm", "embed", "cpu-only", realm="default"
        )
        assert eff_p.environment["FOO"] == "profile"

    def test_service_without_profiles(self, sample_config_file):
        """
        Verify that service_definitions without profiles have an empty
        profiles list.
        """
        reader = ConfigReader(str(sample_config_file))

        chunker_service = next(
            s for s in reader.service_definitions if s.service_name == "chunker"
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

        service = reader.get_service_by_name("qwen1.5-vllm", "default")
        assert service is not None
        assert service.service_name == "qwen1.5-vllm"
        assert service.type == "container"

    def test_get_service_by_name_not_found(self, sample_config_file):
        """Verify that get_service_by_name returns None when
        no matching service is found.
        """
        reader = ConfigReader(str(sample_config_file))

        service = reader.get_service_by_name("nonexistent", "default")
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
        assert len(reader.service_definitions) > 0
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
            "realms": {
                "default": {
                    "service-definitions": [
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
                                "A": {
                                    "volumes": [{"name": "v2", "target": "/t2"}]
                                },
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
                }
            },
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
        svc = reader.get_service_by_name("svc", "default")
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


class TestFootprintParsing:
    """Tests for parsing footprint configurations."""

    def test_footprint_parsing_simple(self, tmp_path):
        """Verify that footprint is parsed from base service definition."""
        cfg = {
            "realms": {
                "default": {
                    "service-definitions": [
                        {
                            "name": "svc",
                            "type": "container",
                            "footprint": {
                                "run-time": 30,
                                "run-script": "base.sh",
                            },
                        },
                    ],
                }
            }
        }
        cfg_path = tmp_path / "test_footprint.yml"
        import yaml as _yaml

        cfg_path.write_text(_yaml.safe_dump(cfg))
        reader = ConfigReader(str(cfg_path))
        svc = reader.get_service_by_name("svc", "default")
        assert svc.footprint is not None
        assert svc.footprint.run_time == 30
        assert svc.footprint.run_script == "base.sh"

    def test_footprint_parsing_profile_merge(self, tmp_path):
        """
        Verify that footprint is merged via get_effective_service_definition.
        """
        cfg = {
            "realms": {
                "default": {
                    "service-definitions": [
                        {
                            "name": "svc",
                            "type": "container",
                            "footprint": {
                                "run-time": 30,
                                "run-script": "base.sh",
                            },
                            "profiles": {
                                "p1": {
                                    "footprint": {
                                        "run-time": 60,
                                    },
                                },
                                "p2": {
                                    "description": "no footprint override",
                                },
                            },
                        },
                    ],
                }
            }
        }
        cfg_path = tmp_path / "test_footprint_profile.yml"
        import yaml as _yaml

        cfg_path.write_text(_yaml.safe_dump(cfg))
        reader = ConfigReader(str(cfg_path))

        # Check effective for p1
        eff1 = reader.get_effective_service_definition(
            "svc", "p1", None, realm="default"
        )
        assert eff1.footprint.run_time == 60
        assert eff1.footprint.run_script == "base.sh"

        # Check effective for p2
        eff2 = reader.get_effective_service_definition(
            "svc", "p2", None, realm="default"
        )
        assert eff2.footprint.run_time == 30
        assert eff2.footprint.run_script == "base.sh"

    def test_footprint_parsing_variety(self, tmp_path):
        """Verify that footprint is extracted for varieties."""
        cfg = {
            "realms": {
                "default": {
                    "service-definitions": [
                        {
                            "name": "svc",
                            "type": "container",
                            "varieties": {
                                "v1": {
                                    "footprint": {
                                        "run-script": "var.sh",
                                    },
                                },
                            },
                        },
                    ],
                }
            }
        }
        cfg_path = tmp_path / "test_footprint_variety.yml"
        import yaml as _yaml

        cfg_path.write_text(_yaml.safe_dump(cfg))
        reader = ConfigReader(str(cfg_path))
        svc = reader.get_service_by_name("svc", "default")

        v1 = svc.varieties["v1"]
        assert v1.footprint.run_script == "var.sh"
        assert v1.footprint.run_time is None


class TestEffectiveServiceDefinition:
    """Tests for the get_effective_service_definition method."""

    def test_merge_precedence(self, tmp_path):
        """Verify merge precedence: Profile > Variety > Base."""
        cfg = {
            "realms": {
                "default": {
                    "service-definitions": [
                        {
                            "name": "svc",
                            "type": "container",
                            "image": "base-img",
                            "environment": {"K1": "base-v1", "K2": "base-v2"},
                            "varieties": {
                                "v1": {
                                    "image": "var-img",
                                    "environment": {
                                        "K2": "var-v2",
                                        "K3": "var-v3",
                                    },
                                }
                            },
                            "profiles": {
                                "p1": {
                                    "image": "prof-img",
                                    "environment": {
                                        "K3": "prof-v3",
                                        "K4": "prof-v4",
                                    },
                                }
                            },
                        }
                    ]
                }
            }
        }
        cfg_path = tmp_path / "test_effective.yml"
        import yaml as _yaml

        cfg_path.write_text(_yaml.safe_dump(cfg))
        reader = ConfigReader(str(cfg_path))

        eff = reader.get_effective_service_definition(
            "svc", "p1", "v1", realm="default"
        )

        # image: Profile > Variety > Base
        assert eff.image == "prof-img"
        # environment: merged, Profile > Variety > Base
        assert eff.environment["K1"] == "base-v1"
        assert eff.environment["K2"] == "var-v2"
        assert eff.environment["K3"] == "prof-v3"
        assert eff.environment["K4"] == "prof-v4"

    def test_property_merging(self, tmp_path):
        """Verify property merging precedence: Profile > Variety > Base."""
        cfg = {
            "realms": {
                "default": {
                    "service-definitions": [
                        {
                            "name": "svc",
                            "type": "container",
                            "properties": {"P1": "base-p1", "P2": "base-p2"},
                            "varieties": {
                                "v1": {
                                    "properties": {
                                        "P2": "var-p2",
                                        "P3": "var-p3",
                                    },
                                }
                            },
                            "profiles": {
                                "p1": {
                                    "properties": {
                                        "P3": "prof-p3",
                                        "P4": "prof-p4",
                                    },
                                }
                            },
                        }
                    ]
                }
            }
        }
        cfg_path = tmp_path / "test_properties.yml"
        import yaml as _yaml

        cfg_path.write_text(_yaml.safe_dump(cfg))
        reader = ConfigReader(str(cfg_path))

        eff = reader.get_effective_service_definition(
            "svc", "p1", "v1", realm="default"
        )

        # properties: merged, Profile > Variety > Base
        assert eff.properties["P1"] == "base-p1"
        assert eff.properties["P2"] == "var-p2"
        assert eff.properties["P3"] == "prof-p3"
        assert eff.properties["P4"] == "prof-p4"

    def test_volume_merging(self, tmp_path):
        """Verify volume merging by target precedence."""
        cfg = {
            "realms": {
                "default": {
                    "service-definitions": [
                        {
                            "name": "svc",
                            "type": "container",
                            "volumes": ["/host1:/t1:ro", "/host2:/t2:rw"],
                            "varieties": {
                                "v1": {
                                    "volumes": [
                                        "/host3:/t2:ro",
                                        "/host4:/t3:rw",
                                    ]
                                }
                            },
                            "profiles": {
                                "p1": {
                                    "volumes": [
                                        "/host5:/t1:rw",
                                        "/host6:/t4:ro",
                                    ]
                                }
                            },
                        }
                    ]
                }
            }
        }
        cfg_path = tmp_path / "test_vols.yml"
        import yaml as _yaml

        cfg_path.write_text(_yaml.safe_dump(cfg))
        reader = ConfigReader(str(cfg_path))

        eff = reader.get_effective_service_definition(
            "svc", "p1", "v1", realm="default"
        )

        # /t1: Profile overrides Base
        # /t2: Variety overrides Base
        # /t3: Variety
        # /t4: Profile
        # Order should be base order then var then prof
        expected = [
            "/host5:/t1:rw",  # t1 from prof (replaces base position)
            "/host3:/t2:ro",  # t2 from var (replaces base position)
            "/host4:/t3:rw",  # t3 from var
            "/host6:/t4:ro",  # t4 from prof
        ]
        assert eff.volumes == expected

    def test_network_merging(self, tmp_path):
        """Verify network merging precedence: Profile > Variety > Base."""
        cfg = {
            "realms": {
                "default": {
                    "service-definitions": [
                        {
                            "name": "svc",
                            "type": "container",
                            "networks": ["base-net"],
                            "varieties": {"v1": {"networks": ["var-net"]}},
                            "profiles": {"p1": {"networks": ["prof-net"]}},
                            "profiles_no_override": {
                                "p2": {"description": "no networks override"}
                            },
                        }
                    ]
                }
            }
        }
        cfg_path = tmp_path / "test_net_merge.yml"
        import yaml as _yaml

        cfg_path.write_text(_yaml.safe_dump(cfg))
        reader = ConfigReader(str(cfg_path))

        # Profile > Variety > Base
        eff1 = reader.get_effective_service_definition(
            "svc", "p1", "v1", realm="default"
        )
        assert eff1.networks == ["prof-net"]

        # Variety > Base
        eff2 = reader.get_effective_service_definition(
            "svc", None, "v1", realm="default"
        )
        assert eff2.networks == ["var-net"]

        # Base only
        eff3 = reader.get_effective_service_definition(
            "svc", None, None, realm="default"
        )
        assert eff3.networks == ["base-net"]


class TestPersistentServiceParsing:
    """Tests for parsing persistent services configurations."""

    def test_persistent_services_are_parsed(self, tmp_path):
        """Verify that persistent-services section in realms is
        correctly parsed.
        """
        config_data = {
            "realms": {
                "prod": {
                    "persistent-services": [
                        {
                            "name": "proxy",
                            "service": "nginx",
                            "variety": "stable",
                        },
                        {
                            "name": "db",
                            "service": "postgres",
                            "profile": "high-perf",
                        },
                    ]
                }
            },
            "service-definitions": [
                {"name": "nginx", "type": "container"},
                {"name": "postgres", "type": "container"},
            ],
        }
        config_file = tmp_path / "persistent_config.yml"
        with open(config_file, "w") as f:
            yaml.dump(config_data, f)

        reader = ConfigReader(str(config_file))
        assert "prod" in reader.realms
        realm = reader.realms["prod"]
        assert len(realm.persistent_services) == 2

        proxy = next(
            ps for ps in realm.persistent_services if ps.name == "proxy"
        )
        assert proxy.service == "nginx"
        assert proxy.variety == "stable"
        assert proxy.realm == "prod"

        db = next(ps for ps in realm.persistent_services if ps.name == "db")
        assert db.service == "postgres"
        assert db.profile == "high-perf"
        assert db.realm == "prod"

    def test_persistent_services_property(self, tmp_path):
        """Verify that the persistent_services property yields all services."""
        config_data = {
            "realms": {
                "r1": {
                    "persistent-services": [{"name": "s1", "service": "svc"}]
                },
                "r2": {
                    "persistent-services": [{"name": "s2", "service": "svc"}]
                },
            },
            "service-definitions": [{"name": "svc", "type": "container"}],
        }
        config_file = tmp_path / "multi_realm_persistent.yml"
        with open(config_file, "w") as f:
            yaml.dump(config_data, f)

        reader = ConfigReader(str(config_file))
        persistent = list(reader.persistent_services)
        assert len(persistent) == 2
        names = {ps.name for ps in persistent}
        assert names == {"s1", "s2"}


class TestNetworkIterator:
    """Tests for the networks() iterator in ConfigReader."""

    def test_networks_iterator(self, tmp_path):
        """Verify that networks() iterator yields all configured networks."""
        config_data = {
            "realms": {
                "r1": {
                    "networks": [
                        {"name": "net1", "type": "bridge"},
                        {"name": "net2", "type": "ipvlan"},
                    ]
                },
                "r2": {
                    "networks": [
                        {"name": "net3", "type": "none"},
                    ]
                },
            }
        }
        config_file = tmp_path / "networks_config.yml"
        with open(config_file, "w") as f:
            yaml.dump(config_data, f)

        reader = ConfigReader(str(config_file))
        networks = list(reader.networks())
        assert len(networks) == 3
        names = {n.name for n in networks}
        assert names == {"net1", "net2", "net3"}


# ============================================================================
# Vault and Locker Parsing Tests
# ============================================================================


class TestVaultConfig:
    @pytest.fixture
    def config_file(self):
        content = {
            "realms": {
                "test-realm": {
                    "vault": {
                        "lockers": {
                            "locker1": {},
                            "locker2": {},
                        }
                    },
                    "service-definitions": [
                        {
                            "name": "svc1",
                            "type": "container",
                            "lockers": ["locker1"],
                        }
                    ],
                }
            },
            "provisioners": [
                {
                    "name": "p1",
                    "host": "h1",
                    "cache": {"type": "redis"},
                }
            ],
            "hosts": [{"name": "h1", "ip": "127.0.0.1"}],
        }
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yml", delete=False
        ) as f:
            yaml.dump(content, f)
            path = f.name
        yield path
        if os.path.exists(path):
            os.remove(path)

    def test_parse_vault(self, config_file):
        reader = ConfigReader(config_file)
        realm = reader.realms["test-realm"]
        assert realm.vault is not None
        assert len(realm.vault.lockers) == 2
        locker_names = {locker.name for locker in realm.vault.lockers}
        assert locker_names == {"locker1", "locker2"}

    def test_parse_service_lockers(self, config_file):
        reader = ConfigReader(config_file)
        sd = reader.get_service_by_name("svc1", "test-realm")
        assert sd.lockers == ["locker1"]
