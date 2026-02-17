import redis

from orchestration.models import Cache
from util.logger import get_logger

logger = get_logger(__name__)


class SecretsStore:
    """Redis-backed store for encrypted secrets."""

    def __init__(self, cache: Cache):
        """Initialize the secrets store.

        Args:
            cache: Cache configuration object containing Redis connection
                parameters
        """
        self.cache = cache
        self._redis_client = self._initialize_redis_client()

    def _initialize_redis_client(self) -> redis.Redis:
        """Initialize Redis client from cache configuration."""
        params = self.cache.parameters or {}
        host = params.get("host", "localhost")
        port = params.get("port", 6379)
        db = params.get("db", 0)
        password = params.get("password")

        logger.info(
            f"Secrets store initializing Redis client: host={host}, port={port}"
        )

        return redis.Redis(
            host=host,
            port=port,
            db=db,
            password=password,
            decode_responses=True,
        )

    def set_secret(self, realm: str, locker: str, encrypted_blob: str) -> None:
        """Store an encrypted secret blob in Redis.

        Args:
            realm: The realm name
            locker: The locker name
            encrypted_blob: The encrypted secret payload
        """
        key = f"vault:{realm}:{locker}"
        self._redis_client.set(key, encrypted_blob)

    def get_secret(self, realm: str, locker: str) -> str | None:
        """Retrieve an encrypted secret blob from Redis.

        Args:
            realm: The realm name
            locker: The locker name

        Returns:
            The encrypted blob or None if not found
        """
        key = f"vault:{realm}:{locker}"
        return self._redis_client.get(key)
