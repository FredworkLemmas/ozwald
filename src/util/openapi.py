from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class Endpoint(BaseModel):
    """Represents an API endpoint operation."""

    url: str
    supported_methods: list[str]
    request_schema: str | None = None
    response_schema: str | None = None


class OpenApiDocument:
    """Helper class to read and navigate OpenAPI specifications."""

    def __init__(self, data: dict[str, Any]):
        self.data = data
        self._endpoints = self._parse_endpoints()
        self._schemas = self.data.get("components", {}).get("schemas", {})

    @property
    def endpoints(self) -> list[Endpoint]:
        """List of endpoints discovered in the specification."""
        return self._endpoints

    @property
    def schemas(self) -> dict[str, Any]:
        """Dictionary of schema definitions."""
        return self._schemas

    def _parse_endpoints(self) -> list[Endpoint]:
        endpoints = []
        paths = self.data.get("paths", {})
        for path, path_item in paths.items():
            for method, operation in path_item.items():
                if method.lower() not in [
                    "get",
                    "post",
                    "put",
                    "delete",
                    "patch",
                ]:
                    continue

                request_schema = self._get_request_schema(operation)
                response_schema = self._get_response_schema(operation)

                endpoints.append(
                    Endpoint(
                        url=path,
                        supported_methods=[method.upper()],
                        request_schema=request_schema,
                        response_schema=response_schema,
                    )
                )
        return endpoints

    def _get_request_schema(self, operation: dict[str, Any]) -> str | None:
        content = operation.get("requestBody", {}).get("content", {})
        json_content = content.get("application/json", {})
        schema = json_content.get("schema", {})
        return self._extract_schema_name(schema)

    def _get_response_schema(self, operation: dict[str, Any]) -> str | None:
        responses = operation.get("responses", {})
        # Look for 200 or 201 response
        success_response = responses.get("200") or responses.get("201")
        if not success_response:
            return None

        content = success_response.get("content", {})
        json_content = content.get("application/json", {})
        schema = json_content.get("schema", {})
        return self._extract_schema_name(schema)

    def _extract_schema_name(self, schema: dict[str, Any]) -> str | None:
        if not schema:
            return None
        ref = schema.get("$ref")
        if ref:
            return ref.split("/")[-1]
        return None
