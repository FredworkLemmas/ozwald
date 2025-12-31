from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

# ============================================================================
# Resource Models
# ============================================================================


class ResourceType(str, Enum):
    GPU = "gpu"
    CPU = "cpu"
    VRAM = "vram"
    MEMORY = "memory"


class Resource(BaseModel):
    name: str
    type: ResourceType
    unit: str
    value: float
    related_resources: list[str] | None
    extended_attributes: dict[str, Any] | None


# ============================================================================
# Host Models
# ============================================================================


class Host(BaseModel):
    name: str
    ip: str
    resources: list[Resource] = Field(default_factory=list)


# ============================================================================
# Footprint Models
# ============================================================================


class ConfiguredServiceIdentifier(BaseModel):
    service_name: str
    profile: str | None = None
    variety: str | None = None


class FootprintAction(BaseModel):
    footprint_all_services: bool = False
    services: list[ConfiguredServiceIdentifier] | None = None
    requested_at: datetime | None = None
    request_id: str | None = None
    footprint_in_progress: bool = False
    footprint_started_at: datetime | None = None


class FootprintLogLines(BaseModel):
    service_name: str
    profile: str | None = None
    variety: str | None = None
    request_datetime: datetime
    is_top_n: bool
    is_bottom_n: bool
    lines: list[str]


# ============================================================================
# Service Definition Models (Templates)
# ============================================================================


class ServiceType(str, Enum):
    """Common service type identifiers used across the system.

    Tests may reference specific members; include a minimal set for
    compatibility.
    """

    CONTAINER = "container"
    SOURCE_FILES = "source-files"
    API = "api"
    SQLITE = "sqlite"


class ServiceDefinitionProfile(BaseModel):
    name: str
    description: str | None = None
    image: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    command: list[str] | str | None = None
    entrypoint: list[str] | str | None = None
    env_file: list[str] = Field(default_factory=list)
    environment: dict[str, Any] = Field(default_factory=dict)
    # Normalized docker volume strings, e.g. "/host:/ctr:ro" or
    # "named_vol:/ctr:rw"
    volumes: list[str] = Field(default_factory=list)


class ServiceDefinitionVariety(BaseModel):
    image: str | None
    depends_on: list[str] | None = Field(default_factory=list)
    command: list[str] | str | None = None
    entrypoint: list[str] | str | None = None
    env_file: list[str] | None = Field(default_factory=list)
    environment: dict[str, Any] | None = Field(default_factory=dict)
    # Normalized docker volume strings, same format as on the base service
    volumes: list[str] = Field(default_factory=list)


class ServiceDefinition(BaseModel):
    service_name: str
    type: str | ServiceType
    description: str | None = None

    # image is required for CONTAINER services, but optional for SOURCE_FILES
    # and API services.
    image: str | None = ""

    depends_on: list[str] | None = Field(default_factory=list)
    command: list[str] | str | None = None
    entrypoint: list[str] | str | None = None
    env_file: list[str] | None = Field(default_factory=list)
    environment: dict[str, Any] | None = Field(default_factory=dict)
    # List of normalized volume mount strings ready for docker CLI, e.g.,
    # "/abs/host:/ctr[:ro|rw]" or "named_vol:/ctr[:ro|rw]".
    volumes: list[str] | None = Field(default_factory=list)
    profiles: dict[str, ServiceDefinitionProfile] | None = Field(
        default_factory=dict,
    )
    varieties: dict[str, ServiceDefinitionVariety] | None = Field(
        default_factory=dict,
    )

    def get_profile_by_name(
        self,
        name: str,
    ) -> ServiceDefinitionProfile | None:
        return (self.profiles or {}).get(name)


# ============================================================================
# Service Usage Models (Runtime)
# ============================================================================
# class SystemUsageDelta(BaseModel):
#     cpu_cores: float = 0.0
#     memory_gb: float = 0.0
#     vram_gb: float = 0.0


# class ServiceInstanceUsage(BaseModel):
#     service_name: str
#     profile: str | None
#     variety: str | None
#     usage: SystemUsageDelta


class ServiceInstanceUsage(BaseModel):
    cpu_cores: float = 0.0
    memory_gb: float = 0.0
    vram_gb: float = 0.0


class SystemUsageDelta(BaseModel):
    service_name: str
    profile: str | None
    variety: str | None
    usage: ServiceInstanceUsage


# ============================================================================
# Service Instance Models (Runtime)
# ============================================================================
class ServiceStatus(str, Enum):
    STARTING = "starting"
    STOPPING = "stopping"
    AVAILABLE = "available"


class Service(BaseModel):
    """Represents an instantiated service in a mode"""

    name: str
    service_name: str  # Reference to ServiceDefinition
    host: str
    parameters: dict[str, Any] | None = None


class ServiceInformation(BaseModel):
    """Information about a service"""

    name: str
    service: str
    variety: str | None = None
    profile: str | None = None
    status: ServiceStatus | None = None
    info: dict[str, Any] | None = {}  # None on request, dict on response


# ============================================================================
# Cache Models
# ============================================================================


class Cache(BaseModel):
    type: str
    parameters: dict[str, Any] | None = None


# ============================================================================
# Provisioner Models
# ============================================================================


class Provisioner(BaseModel):
    name: str
    host: str  # Reference to Host name
    cache: Cache | None = None


class ProvisionerState(BaseModel):
    provisioner: str
    available_resources: list[Resource]
    services: list[Service] | None


# ============================================================================
# Root Configuration Model
# ============================================================================


class OzwaldConfig(BaseModel):
    hosts: list[Host] = Field(default_factory=list)
    services: list[ServiceDefinition] = Field(default_factory=list)
    provisioners: list[Provisioner] = Field(default_factory=list)
    # Top-level named volume specifications (parsed/normalized by reader)
    volumes: dict[str, dict[str, Any]] = Field(default_factory=dict)


# ============================================================================
# Legacy Model (keeping for backward compatibility)
# ============================================================================


class ProvisionerProfile(BaseModel):
    name: str
    services: list[Service]


# ============================================================================
# DSPy/LLM Pipeline Enhancement Models
# ============================================================================


class ResourceConstraints(BaseModel):
    """Resource requirements and constraints for services"""

    gpu_memory_required: str | None = None
    cpu_memory_required: str | None = None
    max_concurrent_instances: int | None = None
    exclusive_gpu: bool = False


class HealthCheck(BaseModel):
    """Health check configuration for services"""

    endpoint: str | None = None
    interval_seconds: int = 30
    timeout_seconds: int = 10
    retries: int = 3


class ServiceDependency(BaseModel):
    """Defines dependencies between services"""

    service_name: str
    required: bool = True
    wait_for_ready: bool = True


class RetryPolicy(BaseModel):
    """Retry policy for service failures"""

    max_retries: int = 3
    backoff_multiplier: float = 2.0
    initial_delay_seconds: float = 1.0
    max_delay_seconds: float = 60.0


class CircuitBreaker(BaseModel):
    """Circuit breaker configuration for service resilience"""

    failure_threshold: int = 5
    recovery_timeout_seconds: int = 60
    half_open_requests: int = 3


class MonitoringConfig(BaseModel):
    """Monitoring and observability configuration"""

    metrics_enabled: bool = True
    metrics_endpoint: str | None = "/metrics"
    metrics_port: int | None = None
    logging_level: str = "INFO"
    tracing_enabled: bool = False
    tracing_endpoint: str | None = None


class TransformerModelConfig(BaseModel):
    """Model management configuration"""

    cache_dir: str | None = None
    download_policy: str = "on_demand"  # on_demand, pre_download, never
    quantization: str | None = None  # e.g., "int4", "int8", "fp16"
    trust_remote_code: bool = False


class DSPyConfig(BaseModel):
    """DSPy-specific configuration"""

    module_class: str | None = None
    optimizer: str | None = None  # e.g., "MIPROv2", "BootstrapFewShot"
    optimizer_params: dict[str, Any] = Field(default_factory=dict)
    evaluation_metrics: list[str] = Field(default_factory=list)
    dataset_path: str | None = None


class NetworkConfig(BaseModel):
    """Network and communication configuration"""

    service_discovery: str = "static"  # static, consul, etcd, kubernetes
    api_version: str = "v1"
    auth_enabled: bool = False
    auth_type: str | None = None  # e.g., "token", "mtls", "oauth2"
    tls_enabled: bool = False
    tls_cert_path: str | None = None
    tls_key_path: str | None = None


class StorageConfig(BaseModel):
    """Storage and persistence configuration"""

    data_dir: str = "/data"
    checkpoint_enabled: bool = True
    checkpoint_interval_seconds: int = 3600
    backup_enabled: bool = False
    backup_retention_days: int = 7


class EnhancedServiceDefinition(ServiceDefinition):
    """Extended ServiceDefinition with DSPy/LLM pipeline features"""

    resource_constraints: ResourceConstraints | None = None
    health_check: HealthCheck | None = None
    dependencies: list[ServiceDependency] = Field(default_factory=list)
    retry_policy: RetryPolicy | None = None
    circuit_breaker: CircuitBreaker | None = None
    monitoring: MonitoringConfig | None = None
    transformer_model_config: TransformerModelConfig | None = None
    dspy_config: DSPyConfig | None = None
    network_config: NetworkConfig | None = None
    storage_config: StorageConfig | None = None


class EnhancedOzwaldConfig(OzwaldConfig):
    """Extended OzwaldConfig with additional pipeline features"""

    global_monitoring: MonitoringConfig | None = None
    global_network: NetworkConfig | None = None
    global_storage: StorageConfig | None = None
