import redis

from orchestration.models import Cache
from util.logger import get_logger

logger = get_logger()


class RunnerLogsCache:
    """Redis-based cache for storing and retrieving runner logs."""

    TTL = 48 * 3600  # 48 hours in seconds

    def __init__(self, cache: Cache):
        self.cache = cache
        self._redis_client = self._initialize_redis_client()

    def _initialize_redis_client(self) -> redis.Redis:
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

    def _get_key(self, container_name: str) -> str:
        return f"runner_logs:{container_name}"

    def add_log_line(self, container_name: str, line: str) -> None:
        key = self._get_key(container_name)
        try:
            self._redis_client.rpush(key, line)
            # Only set expiry if it's the first line or periodically to
            # avoid overhead
            self._redis_client.expire(key, self.TTL)
        except Exception as e:
            logger.error(f"Failed to add log line to Redis: {e}")

    def add_log_lines(self, container_name: str, lines: list[str]) -> None:
        if not lines:
            return
        key = self._get_key(container_name)
        try:
            self._redis_client.rpush(key, *lines)
            self._redis_client.expire(key, self.TTL)
        except Exception as e:
            logger.error(f"Failed to add log lines to Redis: {e}")

    def get_log_lines(self, container_name: str) -> list[str]:
        key = self._get_key(container_name)
        try:
            return self._redis_client.lrange(key, 0, -1)
        except Exception as e:
            logger.error(f"Failed to get log lines from Redis: {e}")
            return []
