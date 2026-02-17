import pytest

from orchestration.models import Cache
from util.secrets_store import SecretsStore


class TestSecretsStore:
    @pytest.fixture
    def redis_mock(self, mocker):
        mock = mocker.patch("redis.Redis")
        return mock.return_value

    @pytest.fixture
    def secrets_store(self, redis_mock):
        cache = Cache(
            type="redis", parameters={"host": "localhost", "port": 6379}
        )
        return SecretsStore(cache)

    def test_set_secret(self, secrets_store, redis_mock):
        secrets_store.set_secret("test-realm", "test-locker", "encrypted-blob")
        redis_mock.set.assert_called_once_with(
            "vault:test-realm:test-locker", "encrypted-blob"
        )

    def test_get_secret(self, secrets_store, redis_mock):
        redis_mock.get.return_value = "encrypted-blob"
        result = secrets_store.get_secret("test-realm", "test-locker")
        assert result == "encrypted-blob"
        redis_mock.get.assert_called_once_with("vault:test-realm:test-locker")
