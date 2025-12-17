from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    pass

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
    related_resources: List[str] | None
    extended_attributes: Dict[str, Any] | None


# ============================================================================
# Host Models
# ============================================================================


class Host(BaseModel):
    name: str
    ip: str
    resources: List[Resource] = Field(default_factory=list)


# ============================================================================
# Profiler Models
# ============================================================================


class ConfiguredServiceIdentifier(BaseModel):
    service_name: str
    profile: str


class ProfilerAction(BaseModel):
    profile_all_services: bool = False
    services: Optional[List[ConfiguredServiceIdentifier]] = None
    requested_at: Optional[datetime] = None
    request_id: Optional[str] = None
    profile_in_progress: bool = False
    profile_started_at: Optional[datetime] = None


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
    description: Optional[str] = None
    image: Optional[str] = None
    depends_on: List[str] = Field(default_factory=list)
    command: Optional[List[str] | str] = None
    entrypoint: Optional[List[str] | str] = None
    env_file: List[str] = Field(default_factory=list)
    environment: Dict[str, Any] = Field(default_factory=dict)
    # Normalized docker volume strings, e.g. "/host:/ctr:ro" or
    # "named_vol:/ctr:rw"
    volumes: List[str] = Field(default_factory=list)


class ServiceDefinitionVariety(BaseModel):
    image: Optional[str]
    depends_on: Optional[List[str]] = Field(default_factory=list)
    command: Optional[List[str] | str] = None
    entrypoint: Optional[List[str] | str] = None
    env_file: Optional[List[str]] = Field(default_factory=list)
    environment: Optional[Dict[str, Any]] = Field(default_factory=dict)
    # Normalized docker volume strings, same format as on the base service
    volumes: List[str] = Field(default_factory=list)


class ServiceDefinition(BaseModel):
    service_name: str
    type: str | ServiceType
    description: Optional[str] = None

    # image is required for CONTAINER services, but optional for SOURCE_FILES
    # and API services.
    image: Optional[str] = ""

    depends_on: Optional[List[str]] = Field(default_factory=list)
    command: Optional[List[str] | str] = None
    entrypoint: Optional[List[str] | str] = None
    env_file: Optional[List[str]] = Field(default_factory=list)
    environment: Optional[Dict[str, Any]] = Field(default_factory=dict)
    # List of normalized volume mount strings ready for docker CLI, e.g.,
    # "/abs/host:/ctr[:ro|rw]" or "named_vol:/ctr[:ro|rw]".
    volumes: Optional[List[str]] = Field(default_factory=list)
    profiles: Optional[Dict[str, ServiceDefinitionProfile]] = Field(
        default_factory=dict
    )
    varieties: Optional[Dict[str, ServiceDefinitionVariety]] = Field(
        default_factory=dict
    )

    def get_profile_by_name(
        self, name: str
    ) -> Optional[ServiceDefinitionProfile]:
        return (self.profiles or {}).get(name)


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
    parameters: Optional[Dict[str, Any]] = None


class ServiceInformation(BaseModel):
    """Information about a service"""

    name: str
    service: str
    variety: Optional[str] = None
    profile: Optional[str] = None
    status: Optional[ServiceStatus] = None
    info: Optional[Dict[str, Any]] = {}  # None on request, dict on response


# ============================================================================
# Cache Models
# ============================================================================


class Cache(BaseModel):
    type: str
    parameters: Dict[str, Any] | None = None


# ============================================================================
# Provisioner Models
# ============================================================================


class Provisioner(BaseModel):
    name: str
    host: str  # Reference to Host name
    cache: Cache | None = None


class ProvisionerState(BaseModel):
    provisioner: str
    available_resources: List[Resource]
    services: List[Service] | None


# ============================================================================
# Root Configuration Model
# ============================================================================


class OzwaldConfig(BaseModel):
    hosts: List[Host] = Field(default_factory=list)
    services: List[ServiceDefinition] = Field(default_factory=list)
    provisioners: List[Provisioner] = Field(default_factory=list)
    # Top-level named volume specifications (parsed/normalized by reader)
    volumes: Dict[str, Dict[str, Any]] = Field(default_factory=dict)


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

    gpu_memory_required: Optional[str] = None
    cpu_memory_required: Optional[str] = None
    max_concurrent_instances: Optional[int] = None
    exclusive_gpu: bool = False


class HealthCheck(BaseModel):
    """Health check configuration for services"""

    endpoint: Optional[str] = None
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
    metrics_endpoint: Optional[str] = "/metrics"
    metrics_port: Optional[int] = None
    logging_level: str = "INFO"
    tracing_enabled: bool = False
    tracing_endpoint: Optional[str] = None


class TransformerModelConfig(BaseModel):
    """Model management configuration"""

    cache_dir: Optional[str] = None
    download_policy: str = "on_demand"  # on_demand, pre_download, never
    quantization: Optional[str] = None  # e.g., "int4", "int8", "fp16"
    trust_remote_code: bool = False


class DSPyConfig(BaseModel):
    """DSPy-specific configuration"""

    module_class: Optional[str] = None
    optimizer: Optional[str] = None  # e.g., "MIPROv2", "BootstrapFewShot"
    optimizer_params: Dict[str, Any] = Field(default_factory=dict)
    evaluation_metrics: List[str] = Field(default_factory=list)
    dataset_path: Optional[str] = None


class NetworkConfig(BaseModel):
    """Network and communication configuration"""

    service_discovery: str = "static"  # static, consul, etcd, kubernetes
    api_version: str = "v1"
    auth_enabled: bool = False
    auth_type: Optional[str] = None  # e.g., "token", "mtls", "oauth2"
    tls_enabled: bool = False
    tls_cert_path: Optional[str] = None
    tls_key_path: Optional[str] = None


class StorageConfig(BaseModel):
    """Storage and persistence configuration"""

    data_dir: str = "/data"
    checkpoint_enabled: bool = True
    checkpoint_interval_seconds: int = 3600
    backup_enabled: bool = False
    backup_retention_days: int = 7


class EnhancedServiceDefinition(ServiceDefinition):
    """Extended ServiceDefinition with DSPy/LLM pipeline features"""

    resource_constraints: Optional[ResourceConstraints] = None
    health_check: Optional[HealthCheck] = None
    dependencies: List[ServiceDependency] = Field(default_factory=list)
    retry_policy: Optional[RetryPolicy] = None
    circuit_breaker: Optional[CircuitBreaker] = None
    monitoring: Optional[MonitoringConfig] = None
    transformer_model_config: Optional[TransformerModelConfig] = None
    dspy_config: Optional[DSPyConfig] = None
    network_config: Optional[NetworkConfig] = None
    storage_config: Optional[StorageConfig] = None


class EnhancedOzwaldConfig(OzwaldConfig):
    """Extended OzwaldConfig with additional pipeline features"""

    global_monitoring: Optional[MonitoringConfig] = None
    global_network: Optional[NetworkConfig] = None
    global_storage: Optional[StorageConfig] = None
