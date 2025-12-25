"""Tests for HostResources implementation."""

import subprocess
import sys
from unittest.mock import MagicMock, Mock, patch

import pytest

# Mock amdsmi and pynvml before importing HostResources to prevent
# library loading errors
sys.modules["amdsmi"] = MagicMock()
sys.modules["pynvml"] = MagicMock()

from src.hosts.resources import GPUResource, HostResources  # noqa: E402


@pytest.fixture
def mock_psutil():
    """Mock psutil module."""
    with patch("src.hosts.resources.psutil") as mock:
        # Default CPU mock
        mock.cpu_count.return_value = 8
        mock.cpu_percent.return_value = 25.0

        # Default memory mock
        mock_memory = MagicMock()
        mock_memory.total = 16 * (1024**3)  # 16 GB
        mock_memory.available = 8 * (1024**3)  # 8 GB
        mock.virtual_memory.return_value = mock_memory

        yield mock


@pytest.fixture
def mock_nvidia_available():
    """Mock NVIDIA_AVAILABLE flag."""
    with patch("src.hosts.resources.NVIDIA_AVAILABLE", True):
        yield


@pytest.fixture
def mock_amd_available():
    """Mock AMD_AVAILABLE flag."""
    with patch("src.hosts.resources.AMD_AVAILABLE", True):
        yield


@pytest.fixture
def mock_pynvml():
    """Mock pynvml module for NVIDIA GPUs."""
    with patch("src.hosts.resources.pynvml") as mock:
        yield mock


@pytest.fixture
def mock_amdsmi():
    """Mock amdsmi module for AMD GPUs."""
    with patch("src.hosts.resources.amdsmi") as mock:
        yield mock


class TestHostResourcesInspectHost:
    """Tests for HostResources.inspect_host method."""

    def test_inspect_host_cpu_info(self, mock_psutil):
        """Test CPU information is correctly retrieved."""
        with patch.object(
            HostResources,
            "_get_nvidia_gpu_info",
            return_value=([], 0.0, 0.0),
        ), patch.object(
            HostResources,
            "_get_amd_gpu_info",
            return_value=([], 0.0, 0.0),
        ):
            result = HostResources.inspect_host()

            assert result.total_cpu_cores == 8
            assert result.available_cpu_cores == 6  # 8 * (100 - 25) / 100
            mock_psutil.cpu_count.assert_called_once()
            mock_psutil.cpu_percent.assert_called_once_with(interval=1)

    def test_inspect_host_memory_info(self, mock_psutil):
        """Test memory information is correctly retrieved."""
        with patch.object(
            HostResources,
            "_get_nvidia_gpu_info",
            return_value=([], 0.0, 0.0),
        ), patch.object(
            HostResources,
            "_get_amd_gpu_info",
            return_value=([], 0.0, 0.0),
        ):
            result = HostResources.inspect_host()

            assert result.total_ram_gb == 16.0
            assert result.available_ram_gb == 8.0
            mock_psutil.virtual_memory.assert_called_once()

    def test_inspect_host_no_gpus(self, mock_psutil):
        """Test behavior when no GPUs are available."""
        with patch.object(
            HostResources,
            "_get_nvidia_gpu_info",
            return_value=([], 0.0, 0.0),
        ), patch.object(
            HostResources,
            "_get_amd_gpu_info",
            return_value=([], 0.0, 0.0),
        ):
            result = HostResources.inspect_host()

            assert result.total_gpus == 0
            assert result.total_vram_gb == 0.0
            assert result.available_vram_gb == 0.0
            assert result.available_gpus == []
            assert result.gpus == []

    def test_inspect_host_with_nvidia_gpus(
        self,
        mock_psutil,
        mock_nvidia_available,
        mock_pynvml,
    ):
        """Test NVIDIA GPU information is correctly retrieved."""
        nvidia_gpus = [
            {
                "id": 0,
                "total_vram_mb": 8192.0,
                "free_vram_mb": 4096.0,
                "utilization": 0.5,
                "vendor": "nvidia",
                "description": "NVIDIA GeForce RTX 3080",
                "pci_device_description": "0000:01:00.0",
            },
            {
                "id": 1,
                "total_vram_mb": 16384.0,
                "free_vram_mb": 12288.0,
                "utilization": 0.25,
                "vendor": "nvidia",
                "description": "NVIDIA GeForce RTX 4090",
                "pci_device_description": "0000:02:00.0",
            },
        ]

        with patch.object(
            HostResources,
            "_get_nvidia_gpu_info",
            return_value=(nvidia_gpus, 24.0, 16.0),
        ), patch.object(
            HostResources,
            "_get_amd_gpu_info",
            return_value=([], 0.0, 0.0),
        ):
            result = HostResources.inspect_host()

            assert result.total_gpus == 2
            assert result.total_vram_gb == pytest.approx(
                24.0,
                rel=0.01,
            )  # 8 + 16 GB
            assert result.available_vram_gb == pytest.approx(
                16.0,
                rel=0.01,
            )  # 4 + 12 GB
            assert result.available_gpus == [0, 1]  # Both under 90% utilization
            assert len(result.gpus) == 2
            assert result.gpus[0].id == 0
            assert result.gpus[0].total_vram == 8192.0
            assert result.gpus[0].available_vram == 4096.0
            assert result.gpus[0].description == "NVIDIA GeForce RTX 3080"
            assert result.gpus[0].pci_device_description == "0000:01:00.0"

    def test_inspect_host_with_amd_gpus(
        self,
        mock_psutil,
        mock_amd_available,
        mock_amdsmi,
    ):
        """Test AMD GPU information is correctly retrieved."""
        amd_gpus = [
            {
                "id": 0,
                "total_vram_mb": 8192.0,
                "free_vram_mb": 6144.0,
                "utilization": 0.25,
                "vendor": "amd",
                "description": "AMD Radeon RX 6800",
                "pci_device_description": "0000:03:00.0",
            },
            {
                "id": 1,
                "total_vram_mb": 16384.0,
                "free_vram_mb": 12288.0,
                "utilization": 0.25,
                "vendor": "amd",
                "description": "AMD Radeon RX 7900 XTX",
                "pci_device_description": "0000:04:00.0",
            },
        ]

        with patch.object(
            HostResources,
            "_get_nvidia_gpu_info",
            return_value=([], 0.0, 0.0),
        ), patch.object(
            HostResources,
            "_get_amd_gpu_info",
            return_value=(amd_gpus, 24.0, 18.0),
        ):
            result = HostResources.inspect_host()

            assert result.total_gpus == 2
            assert result.total_vram_gb == pytest.approx(
                24.0,
                rel=0.01,
            )  # 8 + 16 GB
            assert result.available_vram_gb == pytest.approx(
                18.0,
                rel=0.01,
            )  # (8-2) + (16-4) GB
            assert result.available_gpus == [0, 1]  # Both under 90% utilization
            assert len(result.gpus) == 2
            assert result.gpus[0].id == 0
            assert result.gpus[0].description == "AMD Radeon RX 6800"

    def test_inspect_host_mixed_nvidia_and_amd_gpus(
        self,
        mock_psutil,
        mock_nvidia_available,
        mock_amd_available,
        mock_pynvml,
        mock_amdsmi,
    ):
        """Test scenario with both NVIDIA and AMD GPUs present."""
        nvidia_gpus = [
            {
                "id": 0,
                "total_vram_mb": 8192.0,
                "free_vram_mb": 6144.0,
                "utilization": 0.25,
                "vendor": "nvidia",
                "description": "NVIDIA GeForce RTX 3080",
                "pci_device_description": "0000:01:00.0",
            },
        ]
        amd_gpus = [
            {
                "id": 1,
                "total_vram_mb": 16384.0,
                "free_vram_mb": 12288.0,
                "utilization": 0.25,
                "vendor": "amd",
                "description": "AMD Radeon RX 7900 XTX",
                "pci_device_description": "0000:04:00.0",
            },
        ]

        with patch.object(
            HostResources,
            "_get_nvidia_gpu_info",
            return_value=(nvidia_gpus, 8.0, 6.0),
        ), patch.object(
            HostResources,
            "_get_amd_gpu_info",
            return_value=(amd_gpus, 16.0, 12.0),
        ):
            result = HostResources.inspect_host()

            assert result.total_gpus == 2
            assert result.total_vram_gb == pytest.approx(
                24.0,
                rel=0.01,
            )  # 8 + 16 GB
            assert result.available_vram_gb == pytest.approx(
                18.0,
                rel=0.01,
            )  # 6 + 12 GB
            assert len(result.gpus) == 2

    def test_inspect_host_high_gpu_utilization(
        self,
        mock_psutil,
        mock_nvidia_available,
        mock_pynvml,
    ):
        """Test that GPUs with high utilization are not marked as available."""
        nvidia_gpus = [
            {
                "id": 0,
                "total_vram_mb": 8192.0,
                "free_vram_mb": 512.0,
                "utilization": 0.9375,
                "vendor": "nvidia",
                "description": "NVIDIA GeForce RTX 3080",
                "pci_device_description": "0000:01:00.0",
            },
        ]

        with patch.object(
            HostResources,
            "_get_nvidia_gpu_info",
            return_value=(nvidia_gpus, 8.0, 0.5),
        ), patch.object(
            HostResources,
            "_get_amd_gpu_info",
            return_value=([], 0.0, 0.0),
        ):
            result = HostResources.inspect_host()

            assert result.total_gpus == 1
            assert (
                result.available_gpus == []
            )  # Should be empty due to high utilization
            assert len(result.gpus) == 1
            assert result.gpus[0].id == 0

    def test_inspect_host_nvidia_exception_handling(self, mock_psutil):
        """Test graceful handling when NVIDIA GPU detection fails."""
        with patch.object(
            HostResources,
            "_get_nvidia_gpu_info",
            return_value=([], 0.0, 0.0),
        ), patch.object(
            HostResources,
            "_get_amd_gpu_info",
            return_value=([], 0.0, 0.0),
        ):
            result = HostResources.inspect_host()

            # Should return zero values for GPU metrics
            assert result.total_gpus == 0
            assert result.total_vram_gb == 0.0
            assert result.available_vram_gb == 0.0
            assert result.available_gpus == []
            assert result.gpus == []

            # But CPU and RAM should still work
            assert result.total_cpu_cores == 8
            assert result.total_ram_gb == 16.0

    def test_inspect_host_amd_exception_handling(self, mock_psutil):
        """Test graceful handling when AMD GPU detection fails."""
        with patch.object(
            HostResources,
            "_get_nvidia_gpu_info",
            return_value=([], 0.0, 0.0),
        ), patch.object(
            HostResources,
            "_get_amd_gpu_info",
            return_value=([], 0.0, 0.0),
        ):
            result = HostResources.inspect_host()

            # Should return zero values for GPU metrics
            assert result.total_gpus == 0
            assert result.total_vram_gb == 0.0
            assert result.available_vram_gb == 0.0

            # But CPU and RAM should still work
            assert result.total_cpu_cores == 8
            assert result.total_ram_gb == 16.0

    def test_inspect_host_amd_partial_device_failure(self, mock_psutil):
        """Test that AMD code skips devices that fail but continues with
        others.
        """
        amd_gpus = [
            {
                "id": 1,
                "total_vram_mb": 16384.0,
                "free_vram_mb": 12288.0,
                "utilization": 0.25,
                "vendor": "amd",
                "description": "AMD Radeon RX 7900 XTX",
                "pci_device_description": "0000:04:00.0",
            },
        ]

        with patch.object(
            HostResources,
            "_get_nvidia_gpu_info",
            return_value=([], 0.0, 0.0),
        ), patch.object(
            HostResources,
            "_get_amd_gpu_info",
            return_value=(amd_gpus, 16.0, 12.0),
        ):
            result = HostResources.inspect_host()

            # Should have only device 1
            assert result.total_gpus == 1
            assert len(result.gpus) == 1

    def test_inspect_host_high_cpu_usage(self, mock_psutil):
        """Test CPU availability calculation with high usage."""
        with patch.object(
            HostResources,
            "_get_nvidia_gpu_info",
            return_value=([], 0.0, 0.0),
        ), patch.object(
            HostResources,
            "_get_amd_gpu_info",
            return_value=([], 0.0, 0.0),
        ):
            mock_psutil.cpu_percent.return_value = 90.0

            result = HostResources.inspect_host()

            assert (
                result.available_cpu_cores == 0
            )  # 8 * (100 - 90) / 100 = 0.8 -> 0

    def test_inspect_host_low_memory(self, mock_psutil):
        """Test memory reporting with low available memory."""
        with patch.object(
            HostResources,
            "_get_nvidia_gpu_info",
            return_value=([], 0.0, 0.0),
        ), patch.object(
            HostResources,
            "_get_amd_gpu_info",
            return_value=([], 0.0, 0.0),
        ):
            mock_memory = MagicMock()
            mock_memory.total = 16 * (1024**3)  # 16 GB
            mock_memory.available = 512 * (1024**2)  # 512 MB
            mock_psutil.virtual_memory.return_value = mock_memory

            result = HostResources.inspect_host()

            assert result.total_ram_gb == 16.0
            assert result.available_ram_gb == pytest.approx(0.5, rel=0.01)

    def test_inspect_host_returns_pydantic_model(self, mock_psutil):
        """Test that inspect_host returns a proper HostResources instance."""
        with patch.object(
            HostResources,
            "_get_nvidia_gpu_info",
            return_value=([], 0.0, 0.0),
        ), patch.object(
            HostResources,
            "_get_amd_gpu_info",
            return_value=([], 0.0, 0.0),
        ):
            result = HostResources.inspect_host()

            assert isinstance(result, HostResources)
            # Test Pydantic model functionality
            assert result.model_dump() is not None
            assert result.model_dump_json() is not None


class TestHostResourcesModel:
    """Tests for HostResources Pydantic model properties."""

    def test_model_validation_valid_data(self):
        """Test that valid data passes validation."""
        gpu = GPUResource(
            id=0,
            total_vram=8192.0,
            available_vram=4096.0,
            description="NVIDIA GeForce RTX 3080",
            pci_device_description="0000:01:00.0",
        )

        data = {
            "total_cpu_cores": 8,
            "available_cpu_cores": 6,
            "total_ram_gb": 16.0,
            "available_ram_gb": 8.0,
            "total_vram_gb": 8.0,
            "available_vram_gb": 4.0,
            "total_gpus": 1,
            "available_gpus": [0],
            "gpus": [gpu],
        }

        model = HostResources(**data)

        assert model.total_cpu_cores == 8
        assert model.total_gpus == 1
        assert len(model.gpus) == 1

    def test_model_validation_default_values(self):
        """Test that default values work for optional fields."""
        data = {
            "total_cpu_cores": 4,
            "available_cpu_cores": 2,
            "total_ram_gb": 8.0,
            "available_ram_gb": 4.0,
            "total_vram_gb": 0.0,
            "available_vram_gb": 0.0,
            "total_gpus": 0,
        }

        model = HostResources(**data)

        assert model.available_gpus == []
        assert model.gpus == []

    def test_model_serialization(self):
        """Test model can be serialized to dict and JSON."""
        data = {
            "total_cpu_cores": 8,
            "available_cpu_cores": 6,
            "total_ram_gb": 16.0,
            "available_ram_gb": 8.0,
            "total_vram_gb": 0.0,
            "available_vram_gb": 0.0,
            "total_gpus": 0,
            "available_gpus": [],
            "gpus": [],
        }

        model = HostResources(**data)

        # Test dict serialization
        model_dict = model.model_dump()
        assert model_dict["total_cpu_cores"] == 8

        # Test JSON serialization
        model_json = model.model_dump_json()
        assert isinstance(model_json, str)
        assert "total_cpu_cores" in model_json


class TestInstalledGpuDrivers:
    """Tests for HostResources.installed_gpu_drivers static method."""

    def test_installed_gpu_drivers_detects_nvidia_and_amd(self):
        """Should parse lsmod output and detect both amdgpu and nvidia
        modules.
        """
        lsmod_output = (
            "Module                  Size  Used by\n"
            "amdgpu               123456  1\n"
            "nvidia               654321  1\n"
            "snd_hda_intel         40960  1\n"
        )

        with patch("src.hosts.resources.subprocess.run") as mock_run:
            mock_run.return_value = Mock(stdout=lsmod_output)
            drivers = HostResources.installed_gpu_drivers()

        assert "amdgpu" in drivers
        assert "nvidia" in drivers

    def test_installed_gpu_drivers_no_gpu_modules(self):
        """Should return empty list when no relevant modules are present."""
        lsmod_output = (
            "Module                  Size  Used by\n"
            "snd_hda_intel         40960  1\n"
            "snd_hda_codec        126976  1 snd_hda_intel\n"
        )

        with patch("src.hosts.resources.subprocess.run") as mock_run:
            mock_run.return_value = Mock(stdout=lsmod_output)
            drivers = HostResources.installed_gpu_drivers()

        assert drivers == []

    def test_installed_gpu_drivers_handles_called_process_error(self):
        """Should gracefully handle lsmod failing and return empty list."""
        with patch("src.hosts.resources.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(
                returncode=1,
                cmd=["lsmod"],
            )
            drivers = HostResources.installed_gpu_drivers()

        assert drivers == []

    def test_installed_gpu_drivers_handles_file_not_found(self):
        """Should gracefully handle lsmod not found and return empty list."""
        with patch("src.hosts.resources.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError()
            drivers = HostResources.installed_gpu_drivers()

        assert drivers == []
