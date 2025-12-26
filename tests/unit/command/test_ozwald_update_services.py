import types

import pytest


def _fake_service(varieties=None, profiles=None):
    obj = types.SimpleNamespace()
    obj.varieties = {k: object() for k in (varieties or [])}
    obj.profiles = {k: object() for k in (profiles or [])}
    return obj


class TestOzwaldUpdateServices:
    @pytest.fixture(autouse=True)
    def _no_env_cross(self, monkeypatch):
        # Ensure env does not influence ports
        monkeypatch.delenv("OZWALD_PROVISIONER_PORT", raising=False)

    def _patch_config(self, mocker, service_map):
        singleton = mocker.patch("command.ozwald.SystemConfigReader.singleton")
        cfg = types.SimpleNamespace()

        def get_service_by_name(name):
            return service_map.get(name)

        cfg.get_service_by_name = get_service_by_name
        singleton.return_value = cfg
        return cfg

    def _patch_update_helper(self, mocker):
        called = {}

        def fake_update_services(port, body):
            called["args"] = {"port": port, "body": body}
            return {"status": "accepted"}

        mocker.patch(
            "command.ozwald.ucli.update_services",
            side_effect=fake_update_services,
        )
        return called

    def test_clear_sends_empty_list(self, mocker):
        from command import ozwald

        self._patch_config(mocker, {})
        called = self._patch_update_helper(mocker)

        rc = ozwald.main(["update_services", "--clear"])
        assert rc == 0
        assert called["args"]["body"] == []

    def test_profiles_only_second_token_is_profile(self, mocker):
        from command import ozwald

        self._patch_config(mocker, {"srv": _fake_service([], ["GPU"])})
        called = self._patch_update_helper(mocker)

        rc = ozwald.main(["update_services", "n1[srv][GPU]"])
        assert rc == 0
        body = called["args"]["body"]
        assert body == [
            {
                "name": "n1",
                "service": "srv",
                "variety": None,
                "profile": "GPU",
            },
        ]

    def test_varieties_only_second_token_is_variety(self, mocker):
        from command import ozwald

        self._patch_config(mocker, {"srv": _fake_service(["A"], [])})
        called = self._patch_update_helper(mocker)

        rc = ozwald.main(["update_services", "n1[srv][A]"])
        assert rc == 0
        body = called["args"]["body"]
        assert body == [
            {
                "name": "n1",
                "service": "srv",
                "variety": "A",
                "profile": None,
            },
        ]

    def test_both_sets_token_matches_neither_fails_fast(self, mocker):
        from command import ozwald

        self._patch_config(mocker, {"srv": _fake_service(["A"], ["P"])})
        # Do not patch update_services to ensure it isn't called
        spy = mocker.patch("command.ozwald.ucli.update_services")

        rc = ozwald.main(["update_services", "n1[srv][X]"])
        assert rc == 2
        assert spy.call_count == 0

    def test_update_services_fails_without_system_key(
        self,
        mocker,
        monkeypatch,
    ):
        from command import ozwald

        monkeypatch.delenv("OZWALD_SYSTEM_KEY", raising=False)
        # Ensure it doesn't even get to config reading or CLI call
        spy_cfg = mocker.patch("command.ozwald.SystemConfigReader.singleton")
        spy_cli = mocker.patch("command.ozwald.ucli.update_services")

        rc = ozwald.main(["update_services", "--clear"])
        assert rc == 1
        assert spy_cfg.call_count == 0
        assert spy_cli.call_count == 0
