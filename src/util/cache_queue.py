"""Redis-based queue implementation using Redis list data structure."""

import json
from typing import Any, Optional

import redis

from src.orchestration.models import Cache


class CacheQueue:
    """
    A FIFO queue implementation on top of Redis lists.

    Uses RPUSH for enqueue and LPOP for dequeue to maintain
    first-in-first-out order.
    """

    def __init__(self, cache: Cache, queue_name: str = "default_queue"):
        """
        Initialize the CacheQueue with a cache model.

        Args:
            cache: Cache model containing Redis connection parameters
            queue_name: Name of the Redis list key to use for this queue
        """
        self.cache = cache
        self.queue_name = queue_name

        # Extract Redis connection parameters
        parameters = cache.parameters or {}
        host = parameters.get("host", "localhost")
        port = parameters.get("port", 6379)
        db = parameters.get("db", 0)
        password = parameters.get("password", None)

        # Initialize Redis client
        self.client = redis.Redis(
            host=host,
            port=port,
            db=db,
            password=password,
            decode_responses=True,
        )

    def enqueue(self, item: Any) -> None:
        """
        Add an item to the end of the queue (FIFO).

        Args:
            item: The item to enqueue. Will be JSON-serialized if not a string.
        """
        value = item if isinstance(item, str) else json.dumps(item)
        self.client.rpush(self.queue_name, value)

    def dequeue(self) -> Optional[Any]:
        """
        Remove and return an item from the front of the queue.

        Returns:
            The item from the front of the queue, or None if the queue is
            empty. Attempts to JSON-decode the value; returns raw string if
            decode fails.
        """
        value = self.client.lpop(self.queue_name)

        if value is None:
            return None

        # Try to JSON-decode, return raw string if it fails
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return value

    def clear(self) -> None:
        """Remove all items from the queue."""
        self.client.delete(self.queue_name)

    def length(self) -> int:
        """
        Get the number of items in the queue.

        Returns:
            The number of items currently in the queue.
        """
        return self.client.llen(self.queue_name)

    def __len__(self) -> int:
        """Support len() built-in function."""
        return self.length()
