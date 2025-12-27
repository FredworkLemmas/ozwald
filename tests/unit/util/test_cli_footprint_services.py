import pytest
import requests

from util import cli


class MockResponse:
    def __init__(self, status_code, json_data=None):
        self.status_code = status_code
        self.json_data = json_data or {}

    def json(self):
        return self.json_data

    def raise_for_status(self):
        if 400 <= self.status_code < 600:
            raise requests.HTTPError(f"{self.status_code} Error", response=self)


class TestCliFootprintServices:
    def test_footprint_services_primary_success(self, mocker, monkeypatch):
        monkeypatch.setenv("OZWALD_SYSTEM_KEY", "testkey")
        mock_post = mocker.patch("util.cli.http_post")
        mock_post.return_value = MockResponse(
            202, {"status": "accepted", "request_id": "123"}
        )

        body = {"footprint_all_services": True}
        result = cli.footprint_services(port=8000, body=body)

        assert result["status"] == "accepted"
        assert result["request_id"] == "123"
        assert mock_post.call_count == 1
        assert (
            mock_post.call_args[0][0]
            == "http://localhost:8000/srv/services/footprint/"
        )

    def test_footprint_services_fallback_success(self, mocker, monkeypatch):
        monkeypatch.setenv("OZWALD_SYSTEM_KEY", "testkey")
        mock_post = mocker.patch("util.cli.http_post")

        # First call returns 404, second returns 202
        mock_post.side_effect = [
            MockResponse(404),
            MockResponse(202, {"status": "accepted", "request_id": "456"}),
        ]

        body = {"footprint_all_services": True}
        result = cli.footprint_services(port=8000, body=body)

        assert result["status"] == "accepted"
        assert result["request_id"] == "456"
        assert mock_post.call_count == 2
        assert (
            mock_post.call_args_list[0][0][0]
            == "http://localhost:8000/srv/services/footprint/"
        )
        assert (
            mock_post.call_args_list[1][0][0]
            == "http://localhost:8000/srv/services/footprint"
        )

    def test_footprint_services_fail_both(self, mocker, monkeypatch):
        monkeypatch.setenv("OZWALD_SYSTEM_KEY", "testkey")
        mock_post = mocker.patch("util.cli.http_post")

        mock_post.return_value = MockResponse(404)

        body = {"footprint_all_services": True}
        with pytest.raises(requests.HTTPError) as excinfo:
            cli.footprint_services(port=8000, body=body)

        assert excinfo.value.response.status_code == 404
        assert mock_post.call_count == 2
