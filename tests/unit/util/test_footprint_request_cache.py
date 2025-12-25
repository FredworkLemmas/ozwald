import json

import pytest
import redis

from orchestration.models import (
    Cache,
    FootprintAction,
)
from util.footprint_request_cache import FootprintRequestCache, WriteCollision


class TestFootprintRequestCache:
    @pytest.fixture
    def mock_redis(self, mocker):
        mock_client = mocker.Mock(spec=redis.Redis)
        mocker.patch("redis.Redis", return_value=mock_client)
        return mock_client

    @pytest.fixture
    def cache_config(self):
        return Cache(
            type="redis",
            parameters={"host": "localhost", "port": 6379, "db": 0},
        )

    @pytest.fixture
    def footprint_cache(self, cache_config, mock_redis):
        return FootprintRequestCache(cache_config)

    def test_set_requests_success(self, footprint_cache, mock_redis, mocker):
        mock_lock = mocker.Mock()
        mock_lock.acquire.return_value = True
        mock_redis.lock.return_value = mock_lock

        requests = [
            FootprintAction(request_id="1", footprint_all_services=True),
        ]
        footprint_cache.set_requests(requests)

        mock_redis.set.assert_called_once()
        call_args = mock_redis.set.call_args
        assert call_args[0][0] == FootprintRequestCache.CACHE_KEY
        data = json.loads(call_args[0][1])
        assert len(data) == 1
        assert data[0]["request_id"] == "1"
        mock_lock.release.assert_called_once()

    def test_set_requests_lock_collision(
        self,
        footprint_cache,
        mock_redis,
        mocker,
    ):
        mock_lock = mocker.Mock()
        mock_lock.acquire.return_value = False
        mock_redis.lock.return_value = mock_lock

        with pytest.raises(WriteCollision):
            footprint_cache.set_requests([])

    def test_get_requests_empty(self, footprint_cache, mock_redis):
        mock_redis.get.return_value = None
        assert footprint_cache.get_requests() == []

    def test_get_requests_success(self, footprint_cache, mock_redis):
        requests_data = [{"request_id": "1", "footprint_all_services": True}]
        mock_redis.get.return_value = json.dumps(requests_data)

        requests = footprint_cache.get_requests()
        assert len(requests) == 1
        assert isinstance(requests[0], FootprintAction)
        assert requests[0].request_id == "1"

    def test_add_footprint_request_success(
        self,
        footprint_cache,
        mock_redis,
        mocker,
    ):
        mock_lock = mocker.Mock()
        mock_lock.acquire.return_value = True
        mock_redis.lock.return_value = mock_lock
        mock_redis.get.return_value = None

        new_request = FootprintAction(request_id="2")
        footprint_cache.add_footprint_request(new_request)

        mock_redis.set.assert_called_once()
        mock_lock.release.assert_called_once()

    def test_update_footprint_request_success(
        self,
        footprint_cache,
        mock_redis,
        mocker,
    ):
        mock_lock = mocker.Mock()
        mock_lock.acquire.return_value = True
        mock_redis.lock.return_value = mock_lock

        existing_requests = [
            {"request_id": "1", "footprint_all_services": False},
        ]
        mock_redis.get.return_value = json.dumps(existing_requests)

        updated_request = FootprintAction(
            request_id="1",
            footprint_all_services=True,
        )
        footprint_cache.update_footprint_request(updated_request)

        mock_redis.set.assert_called_once()
        call_args = mock_redis.set.call_args
        data = json.loads(call_args[0][1])
        assert data[0]["footprint_all_services"] is True

    def test_update_footprint_request_not_found(
        self,
        footprint_cache,
        mock_redis,
        mocker,
    ):
        mock_lock = mocker.Mock()
        mock_lock.acquire.return_value = True
        mock_redis.lock.return_value = mock_lock
        mock_redis.get.return_value = json.dumps([])

        with pytest.raises(KeyError):
            footprint_cache.update_footprint_request(
                FootprintAction(request_id="unknown"),
            )
