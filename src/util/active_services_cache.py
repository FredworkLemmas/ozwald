import json
import logging
from typing import List

import redis

from orchestration.models import Cache, ServiceInformation

logger = logging.getLogger(__name__)


class WriteCollision(Exception):
    """Exception raised when a write collision occurs."""


class ActiveServicesCache:
    """
    Redis-based cache for storing and retrieving active service
    information.
    """

    CACHE_KEY = "active_services"
    LOCK_KEY = "active_services:lock"
    LOCK_TIMEOUT = 1  # seconds

    def __init__(self, cache: Cache):
        """
        Initialize the active services cache.

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

        return redis.Redis(
            host=host,
            port=port,
            db=db,
            password=password,
            decode_responses=True,
        )

    def set_services(self, services: List[ServiceInformation]) -> None:
        """
        Store the active services list in the cache with locking to
        prevent race conditions.

        Args:
            services: List of ServiceInformation objects to cache
        """
        # Acquire lock with timeout
        lock = self._redis_client.lock(self.LOCK_KEY, timeout=self.LOCK_TIMEOUT)

        try:
            # Attempt to acquire the lock
            if lock.acquire(blocking=False):
                try:
                    # Convert ServiceInformation objects to JSON-serializable
                    # dictionaries
                    services_data = [
                        service.model_dump() for service in services
                    ]

                    # Encode as JSON and store in Redis
                    json_data = json.dumps(services_data)
                    self._redis_client.set(self.CACHE_KEY, json_data)
                finally:
                    # Always release the lock
                    lock.release()
            else:
                raise WriteCollision(
                    "Failed to acquire lock for setting active services"
                )
        except redis.exceptions.LockError as e:
            raise RuntimeError(f"Lock error while setting active services: {e}")

    def get_services(self) -> List[ServiceInformation]:
        """
        Retrieve the active services list from the cache.

        Returns:
            List of ServiceInformation objects, or empty list if not found
        """
        # Retrieve JSON data from Redis
        json_data = self._redis_client.get(self.CACHE_KEY)

        if json_data is None:
            return []

        # Decode JSON and convert to ServiceInformation objects
        services_data = json.loads(json_data)
        service_info_list = []
        for service_dict in services_data:
            service_dict["info"] = service_dict["info"] or {}
            service_info_list.append(ServiceInformation(**service_dict))
        return service_info_list
