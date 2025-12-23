import types


class TestHttpPost:
    def test_post_uses_default_timeout(self, mocker):
        import util.http as http

        called = {}

        def fake_post(url, headers=None, timeout=None, **kwargs):
            called["timeout"] = timeout
            resp = types.SimpleNamespace()
            resp.status_code = 200
            return resp

        mocker.patch("requests.post", side_effect=fake_post)

        http.post("http://x/y", headers={"A": "b"})

        assert "timeout" in called
        assert called["timeout"] == http.DEFAULT_HTTP_TIMEOUT
