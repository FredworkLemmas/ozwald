import json
import logging
from typing import List

import redis

from orchestration.models import Cache, ProfilerAction

logger = logging.getLogger(__name__)


class WriteCollision(Exception):
    """Exception raised when a write collision occurs."""


class ProfilerRequestCache:
    """Redis-based cache for storing and retrieving profiler requests."""

    CACHE_KEY = "profiler_requests"
    LOCK_KEY = "profiler_requests:lock"
    LOCK_TIMEOUT = 1  # seconds

    def __init__(self, cache: Cache):
        """
        Initialize the profiler request cache.

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

    def set_requests(self, requests: List[ProfilerAction]) -> None:
        """
        Store the profiler requests list in the cache with locking to
        prevent race conditions.

        Args:
            requests: List of ProfilerDirective objects to cache
        """
        lock = self._redis_client.lock(self.LOCK_KEY, timeout=self.LOCK_TIMEOUT)

        try:
            if lock.acquire(blocking=False):
                try:
                    # Convert ProfilerDirective objects to JSON-serializable
                    # dictionaries
                    requests_data = [req.model_dump() for req in requests]

                    # Encode as JSON and store in Redis
                    json_data = json.dumps(requests_data)
                    self._redis_client.set(self.CACHE_KEY, json_data)
                finally:
                    lock.release()
            else:
                raise WriteCollision(
                    "Failed to acquire lock for setting profiler requests"
                )
        except redis.exceptions.LockError as e:
            raise RuntimeError(
                f"Lock error while setting profiler requests: {e}"
            )

    def get_requests(self) -> List[ProfilerAction]:
        """
        Retrieve the profiler requests list from the cache.

        Returns:
            List of ProfilerDirective objects, or empty list if not found
        """
        json_data = self._redis_client.get(self.CACHE_KEY)

        if json_data is None:
            return []

        requests_data = json.loads(json_data)
        return [ProfilerAction(**req_dict) for req_dict in requests_data]

    def add_profile_request(self, profile_request: ProfilerAction) -> None:
        """Append a single profiler request to the cached list with locking.

        If no list exists yet, a new list is created.

        Args:
            profile_request: The profiler directive to add
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
                    current_list.append(profile_request.model_dump())

                    # Store updated list
                    self._redis_client.set(
                        self.CACHE_KEY, json.dumps(current_list)
                    )
                finally:
                    lock.release()
            else:
                raise WriteCollision(
                    "Failed to acquire lock for adding profiler request"
                )
        except redis.exceptions.LockError as e:
            raise RuntimeError(f"Lock error while adding profiler request: {e}")

    def update_profile_request(self, profile_request: ProfilerAction) -> None:
        """Update an existing profiler request in the cache by
        `request_id`.

        The provided `profile_request` must include a non-empty
        `request_id`. If a cached request with the same `request_id` is not
        found, a KeyError is raised.

        Args:
            profile_request: The profiler directive containing the updates
        """
        request_id = getattr(profile_request, "request_id", None)
        if not request_id:
            raise ValueError(
                "update_profile_request requires "
                "profile_request.request_id to be set"
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
                            "No profiler request found with "
                            f"request_id={request_id}"
                        )

                    # Replace with the updated request
                    current_list[index] = profile_request.model_dump()

                    # Save back
                    self._redis_client.set(
                        self.CACHE_KEY, json.dumps(current_list)
                    )
                finally:
                    lock.release()
            else:
                raise WriteCollision(
                    "Failed to acquire lock for updating profiler request"
                )
        except redis.exceptions.LockError as e:
            raise RuntimeError(
                f"Lock error while updating profiler request: {e}"
            )
