import subprocess

import psutil
from pydantic import BaseModel, Field

try:
    import pynvml

    NVIDIA_AVAILABLE = True
except ImportError:
    NVIDIA_AVAILABLE = False

try:
    import amdsmi

    AMD_AVAILABLE = True
except (ImportError, AttributeError):
    AMD_AVAILABLE = False


class GPUResource(BaseModel):
    """Pydantic model representing GPU resource information."""

    id: int = Field(..., description="GPU ID")
    total_vram: float = Field(..., description="Total VRAM in MB")
    available_vram: float = Field(..., description="Available VRAM in MB")
    description: str = Field(..., description="GPU description/name")
    pci_device_description: str = Field(
        ..., description="PCI device description"
    )


class HostResources(BaseModel):
    """Pydantic model representing system resource information."""

    total_cpu_cores: int = Field(..., description="Total number of CPU cores")
    available_cpu_cores: int = Field(
        ..., description="Number of available CPU cores"
    )
    total_ram_gb: float = Field(..., description="Total RAM in gigabytes")
    available_ram_gb: float = Field(
        ..., description="Available RAM in gigabytes"
    )
    total_vram_gb: float = Field(..., description="Total VRAM in gigabytes")
    available_vram_gb: float = Field(
        ..., description="Available VRAM in gigabytes"
    )
    total_gpus: int = Field(..., description="Total number of GPUs")
    available_gpus: list[int] = Field(
        default_factory=list, description="List of available GPU IDs"
    )
    gpus: list[GPUResource] = Field(
        default_factory=list, description="List of GPU resources"
    )

    @staticmethod
    def installed_gpu_drivers():
        """Get the list of installed GPU drivers."""
        try:
            result = subprocess.run(
                ["lsmod"], capture_output=True, text=True, check=True
            )
            lsmod_output = result.stdout
        except subprocess.CalledProcessError:
            # If lsmod fails, return empty list
            return []
        except FileNotFoundError:
            # If lsmod command is not found, return empty list
            return []

        lines = lsmod_output.splitlines()

        # find gpu drivers
        drivers = []
        for line in lines:
            # Skip the header line
            if line.startswith("Module"):
                continue

            # Extract the first column (module name)
            parts = line.split()
            if not parts:
                continue

            module_name = parts[0]

            # Check for AMD or NVIDIA drivers
            if module_name == "amdgpu":
                drivers.append("amdgpu")
            elif module_name == "nvidia":
                drivers.append("nvidia")

        return drivers

    @classmethod
    def inspect_host(cls) -> "HostResources":
        """
        Get current system resource information.

        Returns:
            HostResources object with total and available resources
        """
        # CPU information
        total_cpu = psutil.cpu_count()
        cpu_percent = psutil.cpu_percent(interval=1)
        available_cpu = int(total_cpu * (100 - cpu_percent) / 100)

        # RAM information
        memory = psutil.virtual_memory()
        total_ram = memory.total / (1024**3)  # Convert to GB
        available_ram = memory.available / (1024**3)

        # GPU information - try both NVIDIA and AMD
        nvidia_gpus, nvidia_total_vram, nvidia_available_vram = (
            cls._get_nvidia_gpu_info()
        )
        amd_gpus, amd_total_vram, amd_available_vram = cls._get_amd_gpu_info()

        # Combine GPU information
        all_gpus = nvidia_gpus + amd_gpus
        total_gpus = len(all_gpus)
        total_vram = nvidia_total_vram + amd_total_vram
        available_vram = nvidia_available_vram + amd_available_vram

        # Build available GPU list and GPU resources
        available_gpu_ids = []
        gpu_resources = []

        for gpu in all_gpus:
            gpu_id = gpu["id"]

            # Create GPUResource object
            gpu_resource = GPUResource(
                id=gpu_id,
                total_vram=gpu["total_vram_mb"],
                available_vram=gpu["free_vram_mb"],
                description=gpu["description"],
                pci_device_description=gpu["pci_device_description"],
            )
            gpu_resources.append(gpu_resource)

            # Consider GPU available if utilization is below 90%
            if gpu["utilization"] < 0.9:
                available_gpu_ids.append(gpu_id)

        return cls(
            total_cpu_cores=total_cpu,
            available_cpu_cores=available_cpu,
            total_ram_gb=total_ram,
            available_ram_gb=available_ram,
            total_vram_gb=total_vram,
            available_vram_gb=available_vram,
            total_gpus=total_gpus,
            available_gpus=available_gpu_ids,
            gpus=gpu_resources,
        )

    @staticmethod
    def _get_nvidia_gpu_info() -> tuple[list[dict], float, float]:
        """
        Get NVIDIA GPU information using pynvml.

        Returns:
            Tuple of (gpu_list, total_vram_gb, available_vram_gb)
        """
        if not NVIDIA_AVAILABLE:
            return [], 0.0, 0.0

        try:
            pynvml.nvmlInit()
            device_count = pynvml.nvmlDeviceGetCount()
            gpus = []
            total_vram = 0.0
            available_vram = 0.0

            for i in range(device_count):
                handle = pynvml.nvmlDeviceGetHandleByIndex(i)
                mem_info = pynvml.nvmlDeviceGetMemoryInfo(handle)

                total_mb = mem_info.total / (1024**2)
                free_mb = mem_info.free / (1024**2)
                used_mb = mem_info.used / (1024**2)
                util = used_mb / total_mb if total_mb > 0 else 0

                # Get GPU name/description
                try:
                    name = pynvml.nvmlDeviceGetName(handle)
                    if isinstance(name, bytes):
                        name = name.decode("utf-8")
                except Exception:
                    name = f"NVIDIA GPU {i}"

                # Get PCI info
                try:
                    pci_info = pynvml.nvmlDeviceGetPciInfo(handle)
                    bus_id = (
                        pci_info.busId.decode("utf-8")
                        if isinstance(pci_info.busId, bytes)
                        else pci_info.busId
                    )
                    pci_desc = f"{bus_id}"
                except Exception:
                    pci_desc = f"PCI:{i:02x}:00.0"

                gpus.append({
                    "id": i,
                    "total_vram_mb": total_mb,
                    "free_vram_mb": free_mb,
                    "utilization": util,
                    "vendor": "nvidia",
                    "description": name,
                    "pci_device_description": pci_desc,
                })

                total_vram += total_mb
                available_vram += free_mb

            pynvml.nvmlShutdown()
            return gpus, total_vram / 1024, available_vram / 1024
        except Exception:
            return [], 0.0, 0.0

    @staticmethod
    def _get_amd_gpu_info() -> tuple[list[dict], float, float]:
        """
        Get AMD GPU information using amdsmi.

        Returns:
            Tuple of (gpu_list, total_vram_gb, available_vram_gb)
        """
        if not AMD_AVAILABLE:
            return [], 0.0, 0.0

        try:
            amdsmi.amdsmi_init()
            devices = amdsmi.amdsmi_get_processor_handles()
            gpus = []
            total_vram = 0.0
            available_vram = 0.0

            for i, device in enumerate(devices):
                try:
                    # Get memory information
                    mem_info = amdsmi.amdsmi_get_gpu_memory_total(
                        device, amdsmi.AmdSmiMemoryType.VRAM
                    )
                    mem_usage = amdsmi.amdsmi_get_gpu_memory_usage(
                        device, amdsmi.AmdSmiMemoryType.VRAM
                    )

                    total_mb = mem_info / (1024**2)
                    used_mb = mem_usage / (1024**2)
                    free_mb = total_mb - used_mb
                    util = used_mb / total_mb if total_mb > 0 else 0

                    # Get GPU name/description
                    try:
                        name = amdsmi.amdsmi_get_gpu_asic_info(device)[
                            "market_name"
                        ]
                        if not name:
                            name = f"AMD GPU {i}"
                    except Exception:
                        name = f"AMD GPU {i}"

                    # Get PCI info
                    try:
                        pci_info = amdsmi.amdsmi_get_gpu_pci_info(device)
                        bus = f"{pci_info['bus']:02x}"
                        dev = f"{pci_info['device']:02x}"
                        func = f"{pci_info['function']}"
                        pci_desc = f"{bus}:{dev}.{func}"
                    except Exception:
                        pci_desc = f"PCI:{i:02x}:00.0"

                    gpus.append({
                        "id": i,
                        "total_vram_mb": total_mb,
                        "free_vram_mb": free_mb,
                        "utilization": util,
                        "vendor": "amd",
                        "description": name,
                        "pci_device_description": pci_desc,
                    })

                    total_vram += total_mb
                    available_vram += free_mb
                except Exception:
                    # Skip this device if we can't get its info
                    continue

            amdsmi.amdsmi_shut_down()
            return gpus, total_vram / 1024, available_vram / 1024
        except Exception:
            return [], 0.0, 0.0
