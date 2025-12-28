import json
from typing import List

import redis

from orchestration.models import Cache, FootprintAction


class WriteCollision(Exception):
    """Exception raised when a write collision occurs."""


class FootprintRequestCache:
    """Redis-based cache for storing and retrieving footprint requests."""

    CACHE_KEY = "footprint_requests"
    LOCK_KEY = "footprint_requests:lock"
    LOCK_TIMEOUT = 1  # seconds

    def __init__(self, cache: Cache):
        """Initialize the footprint request cache.

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

    def set_requests(self, requests: List[FootprintAction]) -> None:
        """Store the footprint requests list in the cache with locking to
        prevent race conditions.

        Args:
            requests: List of FootprintAction objects to cache

        """
        lock = self._redis_client.lock(self.LOCK_KEY, timeout=self.LOCK_TIMEOUT)

        try:
            if lock.acquire(blocking=False):
                try:
                    # Convert FootprintAction objects to JSON-serializable
                    # dictionaries
                    requests_data = [
                        req.model_dump(mode="json") for req in requests
                    ]

                    # Encode as JSON and store in Redis
                    json_data = json.dumps(requests_data)
                    self._redis_client.set(self.CACHE_KEY, json_data)
                finally:
                    lock.release()
            else:
                raise WriteCollision(
                    "Failed to acquire lock for setting footprint requests",
                )
        except redis.exceptions.LockError as e:
            raise RuntimeError(
                f"Lock error while setting footprint requests: {e}",
            ) from e

    def get_requests(self) -> List[FootprintAction]:
        """Retrieve the footprint requests list from the cache.

        Returns:
            List of FootprintAction objects, or empty list if not found

        """
        json_data = self._redis_client.get(self.CACHE_KEY)

        if json_data is None:
            return []

        requests_data = json.loads(json_data)
        return [FootprintAction(**req_dict) for req_dict in requests_data]

    def add_footprint_request(self, footprint_request: FootprintAction) -> None:
        """Append a single footprint request to the cached list with locking.

        If no list exists yet, a new list is created.

        Args:
            footprint_request: The footprint action to add

        """
        lock = self._redis_client.lock(self.LOCK_KEY, timeout=self.LOCK_TIMEOUT)

        try:
            if lock.acquire(blocking=False):
                try:
                    # Load existing list (if any)
                    current_json = self._redis_client.get(self.CACHE_KEY)
                    if current_json is None:
                        current_list = []
                    else:
                        current_list = json.loads(current_json)

                    # Append the new request (serialize via model_dump)
                    current_list.append(
                        footprint_request.model_dump(mode="json"),
                    )

                    # Store updated list
                    self._redis_client.set(
                        self.CACHE_KEY,
                        json.dumps(current_list),
                    )
                finally:
                    lock.release()
            else:
                raise WriteCollision(
                    "Failed to acquire lock for adding footprint request",
                )
        except redis.exceptions.LockError as e:
            raise RuntimeError(
                f"Lock error while adding footprint request: {e}",
            ) from e

    def update_footprint_request(
        self,
        footprint_request: FootprintAction,
    ) -> None:
        """Update an existing footprint request in the cache by
        `request_id`.

        The provided `footprint_request` must include a non-empty
        `request_id`. If a cached request with the same `request_id` is not
        found, a KeyError is raised.

        Args:
            footprint_request: The footprint action containing the updates

        """
        request_id = getattr(footprint_request, "request_id", None)
        if not request_id:
            raise ValueError(
                "update_footprint_request requires "
                "footprint_request.request_id to be set",
            )

        lock = self._redis_client.lock(self.LOCK_KEY, timeout=self.LOCK_TIMEOUT)

        try:
            if lock.acquire(blocking=False):
                try:
                    current_json = self._redis_client.get(self.CACHE_KEY)
                    current_list = (
                        json.loads(current_json) if current_json else []
                    )

                    # Find the index by request_id
                    index = -1
                    for i, item in enumerate(current_list):
                        # `item` is a dict in cached JSON
                        if item.get("request_id") == request_id:
                            index = i
                            break

                    if index == -1:
                        raise KeyError(
                            "No footprint request found with "
                            f"request_id={request_id}",
                        )

                    # Replace with the updated request
                    current_list[index] = footprint_request.model_dump(
                        mode="json",
                    )

                    # Save back
                    self._redis_client.set(
                        self.CACHE_KEY,
                        json.dumps(current_list),
                    )
                finally:
                    lock.release()
            else:
                raise WriteCollision(
                    "Failed to acquire lock for updating footprint request",
                )
        except redis.exceptions.LockError as e:
            raise RuntimeError(
                f"Lock error while updating footprint request: {e}",
            ) from e
