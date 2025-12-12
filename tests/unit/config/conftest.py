import pytest
import yaml


@pytest.fixture
def sample_config_dict():
    """
    Provides a complete sample configuration dictionary that matches the
    expected YAML structure for testing ConfigReader.
    """
    return {
        "hosts": [
            {
                "name": "jamma",
                "ip": "192.168.0.211",
                "resources": [
                    {
                        "name": "gpu-0",
                        "type": "gpu",
                        "unit": "device",
                        "value": 1.0,
                        "related_resources": ["vram-0"],
                        "extended_attributes": {
                            "id": ":0",
                            "gpu_type": "nvidia",
                        },
                    },
                    {
                        "name": "vram-0",
                        "type": "vram",
                        "unit": "GB",
                        "value": 8.0,
                        "related_resources": ["gpu-0"],
                        "extended_attributes": None,
                    },
                    {
                        "name": "gpu-1",
                        "type": "gpu",
                        "unit": "device",
                        "value": 1.0,
                        "related_resources": ["vram-1"],
                        "extended_attributes": {
                            "id": ":1",
                            "gpu_type": "nvidia",
                        },
                    },
                    {
                        "name": "vram-1",
                        "type": "vram",
                        "unit": "GB",
                        "value": 8.0,
                        "related_resources": ["gpu-1"],
                        "extended_attributes": None,
                    },
                    {
                        "name": "memory",
                        "type": "memory",
                        "unit": "GB",
                        "value": 96.0,
                        "related_resources": None,
                        "extended_attributes": None,
                    },
                ],
            },
            {
                "name": "bitty",
                "ip": "192.168.0.254",
                "resources": [
                    {
                        "name": "gpu-0",
                        "type": "gpu",
                        "unit": "device",
                        "value": 1.0,
                        "related_resources": ["vram-0"],
                        "extended_attributes": {"id": ":0", "gpu_type": "amd"},
                    },
                    {
                        "name": "vram-0",
                        "type": "vram",
                        "unit": "GB",
                        "value": 8.0,
                        "related_resources": ["gpu-0"],
                        "extended_attributes": None,
                    },
                    {
                        "name": "memory",
                        "type": "memory",
                        "unit": "GB",
                        "value": 20.0,
                        "related_resources": None,
                        "extended_attributes": None,
                    },
                ],
            },
        ],
        "services": [
            {
                "name": "qwen1.5-vllm",
                "type": "container",
                "description": "DeepSeek Qwen 1.5B",
                "varieties": {
                    "nvidia": {"image": "openai-api-vllm.nvidia"},
                    "cpu-only": {"image": "openai-api-vllm.cpu-only"},
                },
                "environment": {
                    "MODEL_NAME": "deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B"
                },
                "profiles": [
                    {
                        "name": "embed",
                        "environment": {
                            "GPU": True,
                            "CPU_OFFLOAD_GB": "",
                            "GPU_MEMORY_UTILIZATION": 0.7,
                            "MAX_MODEL_LEN": 1100,
                        },
                    },
                    {"name": "no-gpu", "environment": {"GPU": False}},
                ],
            },
            {
                "name": "chunker",
                "type": "container",
                "description": "Chunker service",
                "environment": {
                    "SOURCES": ["fiction-sources"],
                    "CHUNK_SIZE": 1000,
                    "CHUNK_OVERLAP": 500,
                },
            },
        ],
        "provisioners": [
            {
                "name": "bitty",
                "host": "bitty",
                "cache": {
                    "type": "redis",
                    "parameters": {"host": "jamma", "port": 6679, "db": 0},
                },
            },
            {
                "name": "jamma",
                "host": "jamma",
                "cache": {
                    "type": "redis",
                    "parameters": {"host": "jamma", "port": 6679, "db": 0},
                },
            },
        ],
    }


@pytest.fixture
def sample_config_file(sample_config_dict, tmp_path):
    """
    Creates a temporary YAML configuration file with sample data
    for testing ConfigReader initialization.
    """
    config_file = tmp_path / "config.yml"
    with open(config_file, "w") as f:
        yaml.dump(sample_config_dict, f)
    return config_file


@pytest.fixture
def minimal_config_dict():
    """
    Provides a minimal valid configuration with only required fields.
    """
    return {"hosts": [], "services": [], "provisioners": []}


@pytest.fixture
def config_without_cache_dict():
    """
    Provides a configuration without cache to test optional cache field.
    """
    return {"hosts": [], "services": [], "provisioners": []}


@pytest.fixture
def config_without_cache_file(config_without_cache_dict, tmp_path):
    """
    Creates a temporary YAML file without cache configuration.
    """
    config_file = tmp_path / "no_cache_config.yml"
    with open(config_file, "w") as f:
        yaml.dump(config_without_cache_dict, f)
    return config_file


@pytest.fixture
def config_with_provisioner_without_cache_dict():
    """
    Provides a configuration with provisioners that don't have cache.
    """
    return {
        "hosts": [],
        "services": [],
        "provisioners": [{"name": "test-provisioner", "host": "test-host"}],
    }


@pytest.fixture
def config_with_provisioner_without_cache_file(
    config_with_provisioner_without_cache_dict, tmp_path
):
    """
    Creates a temporary YAML file with provisioners without cache.
    """
    config_file = tmp_path / "provisioner_no_cache.yml"
    with open(config_file, "w") as f:
        yaml.dump(config_with_provisioner_without_cache_dict, f)
    return config_file


@pytest.fixture
def minimal_config_file(minimal_config_dict, tmp_path):
    """
    Creates a temporary YAML file with minimal valid configuration.
    """
    config_file = tmp_path / "minimal_config.yml"
    with open(config_file, "w") as f:
        yaml.dump(minimal_config_dict, f)
    return config_file


@pytest.fixture
def empty_config_file(tmp_path):
    """
    Creates an empty YAML configuration file for error testing.
    """
    config_file = tmp_path / "empty_config.yml"
    config_file.touch()
    return config_file


@pytest.fixture
def invalid_yaml_file(tmp_path):
    """
    Creates a YAML file with invalid syntax for error testing.
    """
    config_file = tmp_path / "invalid_config.yml"
    with open(config_file, "w") as f:
        f.write("hosts:\n  - name: test\n  invalid yaml: {{{}}")
    return config_file


@pytest.fixture
def missing_orchestrator_config_dict():
    """
    Deprecated: orchestrator section removed from simplified schema.
    """
    return {"hosts": [], "services": [], "provisioners": []}


@pytest.fixture
def missing_orchestrator_file(missing_orchestrator_config_dict, tmp_path):
    """
    Deprecated: orchestrator section removed; keep for compatibility of
    fixtures with simplified schema.
    """
    config_file = tmp_path / "no_orchestrator.yml"
    with open(config_file, "w") as f:
        yaml.dump(missing_orchestrator_config_dict, f)
    return config_file
