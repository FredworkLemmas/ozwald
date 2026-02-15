import redis

from orchestration.models import Cache
from util.logger import get_logger

logger = get_logger()

_registry_instance = None


class ClassCRegistry:
    """Manages Class C subnets from a Class B pool using Redis."""

    CACHE_KEY = "class_c_registry:available_blocks"
    POOL_PREFIX = "172.26"

    def __init__(self, cache: Cache):
        """Initialize with Redis connection parameters."""
        self.cache = cache
        self._redis_client = self._initialize_redis_client()
        self._ensure_pool_initialized()

    def _initialize_redis_client(self) -> redis.Redis:
        """Initialize Redis client from cache configuration."""
        params = self.cache.parameters or {}
        host = params.get("host", "localhost")
        port = params.get("port", 6379)
        db = params.get("db", 0)
        password = params.get("password")

        return redis.Redis(
            host=host,
            port=port,
            db=db,
            password=password,
            decode_responses=True,
        )

    def _ensure_pool_initialized(self):
        """Populate the pool in Redis if it doesn't exist."""
        if not self._redis_client.exists(self.CACHE_KEY):
            logger.info("Initializing Class C network pool in Redis")
            # Populate with 0-255
            blocks = [str(i) for i in range(256)]
            # SADD can take multiple arguments
            self._redis_client.sadd(self.CACHE_KEY, *blocks)

    def checkout_network(self) -> str:
        """Atomically find and mark an available /24 block in Redis."""
        index = self._redis_client.spop(self.CACHE_KEY)
        if index is None:
            raise RuntimeError("No available Class C networks in the pool")
        return f"{self.POOL_PREFIX}.{index}.0/24"

    def release_network(self, ip_range: str) -> None:
        """Mark the specified subnet as available in Redis."""
        if not ip_range or not ip_range.startswith(f"{self.POOL_PREFIX}."):
            return
        try:
            # Extract index from "172.19.index.0/24"
            parts = ip_range.split(".")
            index = parts[2]
            self._redis_client.sadd(self.CACHE_KEY, index)
            logger.info(f"Released Class C network block: {ip_range}")
        except (IndexError, ValueError) as e:
            logger.error(f"Failed to release invalid ip_range {ip_range}: {e}")

    @classmethod
    def singleton(cls):
        """Singleton pattern for ClassCRegistry."""
        global _registry_instance
        if _registry_instance is None:
            from orchestration.provisioner import SystemProvisioner

            provisioner = SystemProvisioner.singleton()
            _registry_instance = cls(provisioner.get_cache())
        return _registry_instance
