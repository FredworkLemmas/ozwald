import pytest

from orchestration.models import Cache
from util.class_c_registry import ClassCRegistry


class TestClassCRegistry:
    @pytest.fixture
    def mock_redis(self, mocker):
        return mocker.patch("redis.Redis")

    @pytest.fixture
    def cache_config(self):
        return Cache(
            type="redis", parameters={"host": "localhost", "port": 6379}
        )

    def test_initialization_populates_pool(self, mock_redis, cache_config):
        mock_redis_instance = mock_redis.return_value
        mock_redis_instance.exists.return_value = False

        ClassCRegistry(cache_config)

        assert mock_redis_instance.sadd.called
        # ClassCRegistry.CACHE_KEY + 256 blocks
        call_args = mock_redis_instance.sadd.call_args[0]
        assert len(call_args) == 257
        assert call_args[0] == ClassCRegistry.CACHE_KEY

    def test_checkout_network(self, mock_redis, cache_config):
        mock_redis_instance = mock_redis.return_value
        mock_redis_instance.exists.return_value = True
        mock_redis_instance.spop.return_value = "10"

        registry = ClassCRegistry(cache_config)
        subnet = registry.checkout_network()

        assert subnet == "172.26.10.0/24"
        mock_redis_instance.spop.assert_called_with(ClassCRegistry.CACHE_KEY)

    def test_checkout_network_empty_pool(self, mock_redis, cache_config):
        mock_redis_instance = mock_redis.return_value
        mock_redis_instance.exists.return_value = True
        mock_redis_instance.spop.return_value = None

        registry = ClassCRegistry(cache_config)
        with pytest.raises(RuntimeError, match="No available Class C networks"):
            registry.checkout_network()

    def test_release_network(self, mock_redis, cache_config):
        mock_redis_instance = mock_redis.return_value
        mock_redis_instance.exists.return_value = True

        registry = ClassCRegistry(cache_config)
        registry.release_network("172.26.10.0/24")

        mock_redis_instance.sadd.assert_called_with(
            ClassCRegistry.CACHE_KEY, "10"
        )

    def test_release_network_invalid(self, mock_redis, cache_config):
        mock_redis_instance = mock_redis.return_value
        mock_redis_instance.exists.return_value = True

        registry = ClassCRegistry(cache_config)
        registry.release_network("10.0.0.0/24")

        # mock_redis_instance.sadd should not be called except in __init__
        # but here exists=True, so it shouldn't be called at all.
        assert mock_redis_instance.sadd.call_count == 0
