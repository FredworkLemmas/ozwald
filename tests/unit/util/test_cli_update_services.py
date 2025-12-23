import types


class TestCliUpdateServices:
    def test_primary_path_success(self, mocker):
        import util.cli as ucli

        resp = types.SimpleNamespace()
        resp.status_code = 202
        resp.json = lambda: {"status": "accepted"}
        resp.raise_for_status = lambda: None

        http_post = mocker.patch("util.cli.http_post", return_value=resp)

        out = ucli.update_services(port=8123, body=[])
        assert out["status"] == "accepted"
        http_post.assert_called_once()
        url = http_post.call_args[0][0]
        assert "/srv/services/active/update/" in url

    def test_legacy_fallback_on_404(self, mocker):
        import util.cli as ucli

        resp404 = types.SimpleNamespace()
        resp404.status_code = 404
        resp404.json = dict
        resp404.raise_for_status = lambda: None

        resp202 = types.SimpleNamespace()
        resp202.status_code = 202
        resp202.json = lambda: {"status": "accepted"}
        resp202.raise_for_status = lambda: None

        http_post = mocker.patch(
            "util.cli.http_post", side_effect=[resp404, resp202]
        )

        out = ucli.update_services(port=8123, body=[{"x": 1}])
        assert out["status"] == "accepted"
        assert http_post.call_count == 2
        url1 = http_post.call_args_list[0][0][0]
        url2 = http_post.call_args_list[1][0][0]
        assert url1.endswith("/srv/services/active/update/")
        assert url2.endswith("/srv/services/update/")
