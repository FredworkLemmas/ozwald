import types

import util.cli as ucli


class TestCliFootprintLogs:
    def test_get_footprint_logs_success(self, mocker):
        resp = types.SimpleNamespace()
        resp.status_code = 200
        resp.json = lambda: {"lines": ["a", "b"]}
        resp.raise_for_status = lambda: None

        http_get = mocker.patch("util.cli.http_get", return_value=resp)
        mocker.patch.dict("os.environ", {"OZWALD_SYSTEM_KEY": "test-key"})

        out = ucli.get_footprint_logs(
            service_name="svc1", port=8123, profile="p1", top=5
        )

        assert out["lines"] == ["a", "b"]
        http_get.assert_called_once()
        args, kwargs = http_get.call_args
        assert "/srv/services/footprint-logs/svc1/" in args[0]
        assert kwargs["params"] == {"profile": "p1", "top": 5}
