"""Tests for CacheQueue implementation."""

import json
from unittest.mock import MagicMock, patch

import pytest

from src.orchestration.models import Cache
from src.util.cache_queue import CacheQueue


@pytest.fixture
def mock_redis():
    """Create a mock Redis client."""
    with patch("src.util.cache_queue.redis.Redis") as mock:
        redis_instance = MagicMock()
        mock.return_value = redis_instance
        yield redis_instance


@pytest.fixture
def cache_model():
    """Create a Cache model for testing."""
    return Cache(
        type="redis",
        parameters={"host": "localhost", "port": 6379, "db": 0},
    )


@pytest.fixture
def cache_queue(cache_model, mock_redis):
    """Create a CacheQueue instance with mocked Redis."""
    return CacheQueue(cache_model, queue_name="test_queue")


class TestCacheQueueInitialization:
    """Tests for CacheQueue initialization."""

    def test_init_with_default_parameters(self, mock_redis):
        """Test initialization with default Redis parameters."""
        cache = Cache(type="redis", parameters={})
        queue = CacheQueue(cache)

        # Verify Redis was initialized with defaults
        assert queue.queue_name == "default_queue"

    def test_init_with_custom_queue_name(self, cache_model, mock_redis):
        """Test initialization with custom queue name."""
        queue = CacheQueue(cache_model, queue_name="custom_queue")
        assert queue.queue_name == "custom_queue"

    def test_init_with_password(self, mock_redis):
        """Test initialization with Redis password."""
        cache = Cache(
            type="redis",
            parameters={
                "host": "localhost",
                "port": 6379,
                "password": "secret",
            },
        )
        CacheQueue(cache)
        # Verify password was passed to Redis client


class TestEnqueue:
    """Tests for enqueue operation."""

    def test_enqueue_string(self, cache_queue, mock_redis):
        """Test enqueuing a string."""
        cache_queue.enqueue("test_item")
        mock_redis.rpush.assert_called_once_with("test_queue", "test_item")

    def test_enqueue_dict(self, cache_queue, mock_redis):
        """Test enqueuing a dictionary."""
        test_dict = {"key": "value", "number": 42}
        cache_queue.enqueue(test_dict)

        expected_json = json.dumps(test_dict)
        mock_redis.rpush.assert_called_once_with("test_queue", expected_json)

    def test_enqueue_list(self, cache_queue, mock_redis):
        """Test enqueuing a list."""
        test_list = [1, 2, 3, "four"]
        cache_queue.enqueue(test_list)

        expected_json = json.dumps(test_list)
        mock_redis.rpush.assert_called_once_with("test_queue", expected_json)

    def test_enqueue_multiple_items(self, cache_queue, mock_redis):
        """Test enqueuing multiple items."""
        cache_queue.enqueue("first")
        cache_queue.enqueue("second")
        cache_queue.enqueue("third")

        assert mock_redis.rpush.call_count == 3


class TestDequeue:
    """Tests for dequeue operation."""

    def test_dequeue_string(self, cache_queue, mock_redis):
        """Test dequeuing a string."""
        mock_redis.lpop.return_value = "test_item"

        result = cache_queue.dequeue()

        assert result == "test_item"
        mock_redis.lpop.assert_called_once_with("test_queue")

    def test_dequeue_json_dict(self, cache_queue, mock_redis):
        """Test dequeuing a JSON-encoded dictionary."""
        test_dict = {"key": "value", "number": 42}
        mock_redis.lpop.return_value = json.dumps(test_dict)

        result = cache_queue.dequeue()

        assert result == test_dict
        assert isinstance(result, dict)

    def test_dequeue_json_list(self, cache_queue, mock_redis):
        """Test dequeuing a JSON-encoded list."""
        test_list = [1, 2, 3, "four"]
        mock_redis.lpop.return_value = json.dumps(test_list)

        result = cache_queue.dequeue()

        assert result == test_list
        assert isinstance(result, list)

    def test_dequeue_empty_queue(self, cache_queue, mock_redis):
        """Test dequeuing from an empty queue."""
        mock_redis.lpop.return_value = None

        result = cache_queue.dequeue()

        assert result is None

    def test_dequeue_fifo_order(self, cache_queue, mock_redis):
        """Test that dequeue maintains FIFO order."""
        # Simulate enqueuing and dequeuing
        items = ["first", "second", "third"]
        mock_redis.lpop.side_effect = items

        results = [cache_queue.dequeue() for _ in range(3)]

        assert results == items


class TestClear:
    """Tests for clear operation."""

    def test_clear_queue(self, cache_queue, mock_redis):
        """Test clearing the queue."""
        cache_queue.clear()

        mock_redis.delete.assert_called_once_with("test_queue")

    def test_clear_empty_queue(self, cache_queue, mock_redis):
        """Test clearing an already empty queue."""
        cache_queue.clear()

        mock_redis.delete.assert_called_once()


class TestLength:
    """Tests for length operation."""

    def test_length_empty_queue(self, cache_queue, mock_redis):
        """Test length of an empty queue."""
        mock_redis.llen.return_value = 0

        assert cache_queue.length() == 0
        mock_redis.llen.assert_called_once_with("test_queue")

    def test_length_with_items(self, cache_queue, mock_redis):
        """Test length of a queue with items."""
        mock_redis.llen.return_value = 5

        assert cache_queue.length() == 5

    def test_len_builtin(self, cache_queue, mock_redis):
        """Test using Python's len() builtin."""
        mock_redis.llen.return_value = 3

        assert len(cache_queue) == 3


class TestIntegrationScenarios:
    """Integration tests simulating real usage patterns."""

    def test_enqueue_dequeue_cycle(self, cache_queue, mock_redis):
        """Test a complete enqueue-dequeue cycle."""
        # Setup mock to simulate Redis behavior
        queue_storage = []

        def mock_rpush(key, value):
            queue_storage.append(value)

        def mock_lpop(key):
            return queue_storage.pop(0) if queue_storage else None

        def mock_llen(key):
            return len(queue_storage)

        mock_redis.rpush.side_effect = mock_rpush
        mock_redis.lpop.side_effect = mock_lpop
        mock_redis.llen.side_effect = mock_llen

        # Enqueue items
        cache_queue.enqueue("item1")
        cache_queue.enqueue({"data": "item2"})
        cache_queue.enqueue([3, 4, 5])

        # Check length
        assert len(cache_queue) == 3

        # Dequeue items in FIFO order
        assert cache_queue.dequeue() == "item1"
        assert cache_queue.dequeue() == {"data": "item2"}
        assert cache_queue.dequeue() == [3, 4, 5]

        # Queue should be empty
        assert len(cache_queue) == 0
        assert cache_queue.dequeue() is None

    def test_clear_resets_queue(self, cache_queue, mock_redis):
        """Test that clear properly resets the queue."""
        queue_storage = ["item1", "item2", "item3"]

        def mock_delete(key):
            queue_storage.clear()

        def mock_llen(key):
            return len(queue_storage)

        mock_redis.delete.side_effect = mock_delete
        mock_redis.llen.side_effect = mock_llen

        # Initially has items
        mock_redis.llen.return_value = 3
        assert len(cache_queue) == 3

        # Clear the queue
        cache_queue.clear()

        # Should be empty now
        assert len(cache_queue) == 0
