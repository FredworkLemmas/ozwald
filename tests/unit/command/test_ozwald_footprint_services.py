import types

import pytest

from command import ozwald


def _fake_service(varieties=None, profiles=None):
    obj = types.SimpleNamespace()
    obj.varieties = {k: object() for k in (varieties or [])}
    obj.profiles = {k: object() for k in (profiles or [])}
    return obj


class TestOzwaldFootprintServices:
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

    def _patch_footprint_helper(self, mocker):
        called = {}

        def fake_footprint_services(port, body):
            called["args"] = {"port": port, "body": body}
            return {"status": "accepted", "request_id": "req-123"}

        mocker.patch(
            "command.ozwald.ucli.footprint_services",
            side_effect=fake_footprint_services,
        )
        return called

    def test_footprint_all(self, mocker):
        self._patch_config(mocker, {})
        called = self._patch_footprint_helper(mocker)

        rc = ozwald.main(["footprint_services", "--all"])
        assert rc == 0
        assert called["args"]["body"] == {"footprint_all_services": True}

    def test_footprint_specific_no_opts(self, mocker):
        self._patch_config(mocker, {"svc1": _fake_service([], [])})
        called = self._patch_footprint_helper(mocker)

        rc = ozwald.main(["footprint_services", "svc1"])
        assert rc == 0
        assert called["args"]["body"] == {
            "footprint_all_services": False,
            "services": [
                {"service_name": "svc1", "profile": None, "variety": None},
            ],
        }

    def test_footprint_specific_with_profile(self, mocker):
        self._patch_config(mocker, {"svc1": _fake_service([], ["p1"])})
        called = self._patch_footprint_helper(mocker)

        rc = ozwald.main(["footprint_services", "svc1[p1]"])
        assert rc == 0
        assert called["args"]["body"]["services"][0] == {
            "service_name": "svc1",
            "profile": "p1",
            "variety": None,
        }

    def test_footprint_specific_with_variety(self, mocker):
        self._patch_config(mocker, {"svc1": _fake_service(["v1"], [])})
        called = self._patch_footprint_helper(mocker)

        rc = ozwald.main(["footprint_services", "svc1[v1]"])
        assert rc == 0
        assert called["args"]["body"]["services"][0] == {
            "service_name": "svc1",
            "profile": None,
            "variety": "v1",
        }

    def test_footprint_specific_with_both(self, mocker):
        self._patch_config(mocker, {"svc1": _fake_service(["v1"], ["p1"])})
        called = self._patch_footprint_helper(mocker)

        rc = ozwald.main(["footprint_services", "svc1[p1][v1]"])
        assert rc == 0
        assert called["args"]["body"]["services"][0] == {
            "service_name": "svc1",
            "profile": "p1",
            "variety": "v1",
        }

    def test_footprint_missing_required_profile(self, mocker):
        self._patch_config(mocker, {"svc1": _fake_service([], ["p1"])})
        spy = mocker.patch("command.ozwald.ucli.footprint_services")

        rc = ozwald.main(["footprint_services", "svc1"])
        assert rc == 2
        assert spy.call_count == 0

    def test_footprint_prohibited_profile(self, mocker):
        self._patch_config(mocker, {"svc1": _fake_service([], [])})
        spy = mocker.patch("command.ozwald.ucli.footprint_services")

        rc = ozwald.main(["footprint_services", "svc1[p1]"])
        assert rc == 2
        assert spy.call_count == 0

    def test_footprint_unknown_service(self, mocker):
        self._patch_config(mocker, {})
        spy = mocker.patch("command.ozwald.ucli.footprint_services")

        rc = ozwald.main(["footprint_services", "unknown"])
        assert rc == 2
        assert spy.call_count == 0

    def test_footprint_multiple_services(self, mocker):
        self._patch_config(
            mocker,
            {
                "s1": _fake_service([], []),
                "s2": _fake_service(["v2"], ["p2"]),
            },
        )
        called = self._patch_footprint_helper(mocker)

        rc = ozwald.main(["footprint_services", "s1, s2[p2][v2]"])
        assert rc == 0
        services = called["args"]["body"]["services"]
        assert len(services) == 2
        assert services[0]["service_name"] == "s1"
        assert services[1]["service_name"] == "s2"
        assert services[1]["profile"] == "p2"
        assert services[1]["variety"] == "v2"
