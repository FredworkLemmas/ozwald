import pytest

from orchestration.models import Cache
from util.runner_logs_cache import RunnerLogsCache


class TestRunnerLogsCache:
    @pytest.fixture
    def cache_config(self):
        return Cache(
            type="redis",
            parameters={"host": "localhost", "port": 6379, "db": 0},
        )

    @pytest.fixture
    def mock_redis_cls(self, mocker):
        return mocker.patch("redis.Redis")

    def test_add_log_line(self, cache_config, mock_redis_cls):
        cache = RunnerLogsCache(cache_config)
        container_name = "test-container"
        line = "log line 1"

        cache.add_log_line(container_name, line)

        mock_redis = mock_redis_cls.return_value
        mock_redis.rpush.assert_called_once_with(
            "runner_logs:test-container", line
        )
        mock_redis.expire.assert_called_once_with(
            "runner_logs:test-container", RunnerLogsCache.TTL
        )

    def test_get_log_lines(self, cache_config, mock_redis_cls):
        cache = RunnerLogsCache(cache_config)
        container_name = "test-container"
        mock_redis = mock_redis_cls.return_value
        mock_redis.lrange.return_value = ["line1", "line2"]

        lines = cache.get_log_lines(container_name)

        assert lines == ["line1", "line2"]
        mock_redis.lrange.assert_called_once_with(
            "runner_logs:test-container", 0, -1
        )
