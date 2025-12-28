import pytest

from util.openapi import OpenApiDocument


class TestOpenApiDocument:
    @pytest.fixture
    def sample_openapi_data(self):
        return {
            "paths": {
                "/test/endpoint": {
                    "get": {
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "$ref": (
                                                "#/components/schemas/"
                                                "TestResponse"
                                            )
                                        }
                                    }
                                }
                            }
                        }
                    },
                    "post": {
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": (
                                            "#/components/schemas/TestRequest"
                                        )
                                    }
                                }
                            }
                        },
                        "responses": {
                            "201": {
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "$ref": (
                                                "#/components/schemas/"
                                                "TestResponse"
                                            )
                                        }
                                    }
                                }
                            }
                        },
                    },
                }
            },
            "components": {
                "schemas": {
                    "TestRequest": {"type": "object"},
                    "TestResponse": {"type": "object"},
                }
            },
        }

    def test_parse_endpoints(self, sample_openapi_data):
        doc = OpenApiDocument(data=sample_openapi_data)
        endpoints = doc.endpoints

        # We should have 2 endpoints (GET and POST for the same path)
        assert len(endpoints) == 2

        get_ep = next(e for e in endpoints if "GET" in e.supported_methods)
        assert get_ep.url == "/test/endpoint"
        assert get_ep.request_schema is None
        assert get_ep.response_schema == "TestResponse"

        post_ep = next(e for e in endpoints if "POST" in e.supported_methods)
        assert post_ep.url == "/test/endpoint"
        assert post_ep.request_schema == "TestRequest"
        assert post_ep.response_schema == "TestResponse"

    def test_schemas_property(self, sample_openapi_data):
        doc = OpenApiDocument(data=sample_openapi_data)
        assert "TestRequest" in doc.schemas
        assert "TestResponse" in doc.schemas
        assert doc.schemas["TestRequest"] == {"type": "object"}
