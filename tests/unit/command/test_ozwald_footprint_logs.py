from command import ozwald


class TestOzwaldFootprintLogs:
    def test_get_footprint_logs_basic(self, mocker):
        mock_get_logs = mocker.patch("command.ozwald.ucli.get_footprint_logs")
        mock_get_logs.return_value = {
            "service_name": "svc1",
            "profile": "p1",
            "variety": "v1",
            "lines": ["log1", "log2"],
        }

        # We need to set OZWALD_SYSTEM_KEY
        mocker.patch.dict("os.environ", {"OZWALD_SYSTEM_KEY": "test-key"})

        rc = ozwald.main([
            "get_footprint_logs",
            "svc1",
            "--profile",
            "p1",
            "--variety",
            "v1",
            "--top",
            "10",
        ])

        assert rc == 0
        mock_get_logs.assert_called_once_with(
            port=8000,
            service_name="svc1",
            profile="p1",
            variety="v1",
            top=10,
            last=None,
        )

    def test_get_footprint_logs_no_service(self, mocker):
        # We need to set OZWALD_SYSTEM_KEY
        mocker.patch.dict("os.environ", {"OZWALD_SYSTEM_KEY": "test-key"})

        rc = ozwald.main(["get_footprint_logs"])
        assert rc == 2
