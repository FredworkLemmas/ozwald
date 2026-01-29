from __future__ import annotations

import os
from typing import Any

from .http import (
    get as http_get,
    post as http_post,
)


def _auth_headers(system_key: str | None = None) -> dict[str, str]:
    key = system_key or os.environ.get("OZWALD_SYSTEM_KEY")
    if not key:
        raise KeyError(
            "OZWALD_SYSTEM_KEY environment variable is not defined",
        )
    return {"Authorization": f"Bearer {key}"}


def get_configured_services(
    *,
    port: int = 8000,
    system_key: str | None = None,
) -> list[dict[str, Any]]:
    url = f"http://localhost:{port}/srv/services/configured/"
    headers = _auth_headers(system_key)
    resp = http_get(url, headers=headers)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list):
        raise ValueError("Unexpected response format for configured services")
    return data


def get_active_services(
    *,
    port: int = 8000,
    system_key: str | None = None,
) -> list[dict[str, Any]]:
    url = f"http://localhost:{port}/srv/services/active/"
    headers = _auth_headers(system_key)
    resp = http_get(url, headers=headers)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list):
        raise ValueError("Unexpected response format for active services")
    return data


def get_host_resources(
    *,
    port: int = 8000,
    system_key: str | None = None,
) -> dict[str, Any]:
    url = f"http://localhost:{port}/srv/host/resources"
    headers = _auth_headers(system_key)
    resp = http_get(url, headers=headers)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict):
        raise ValueError("Unexpected response format for host resources")
    return data


def get_openapi_spec(
    *,
    port: int = 8000,
    system_key: str | None = None,
) -> dict[str, Any]:
    """Fetch the OpenAPI spec from the provisioner API."""
    url = f"http://localhost:{port}/openapi.json"
    headers = _auth_headers(system_key)
    resp = http_get(url, headers=headers)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict):
        raise ValueError("Unexpected response format for OpenAPI spec")
    return data


def update_services(
    *,
    port: int = 8000,
    body: list[dict[str, Any]] | list[Any],
    system_key: str | None = None,
) -> dict[str, Any]:
    """Call the provisioner update services endpoint.

    Tries the primary path first, then falls back to the legacy path
    if the primary returns 404.
    """
    primary = f"http://localhost:{port}/srv/services/active/update/"
    legacy = f"http://localhost:{port}/srv/services/update/"
    headers = _auth_headers(system_key)

    resp = http_post(primary, headers=headers, json=body)
    if resp.status_code == 404:
        resp = http_post(legacy, headers=headers, json=body)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict):
        raise ValueError("Unexpected response format for update_services")
    return data


def footprint_services(
    *,
    port: int = 8000,
    body: dict[str, Any],
    system_key: str | None = None,
) -> dict[str, Any]:
    """Call the provisioner footprint services endpoint."""
    url = f"http://localhost:{port}/srv/services/footprint"
    headers = _auth_headers(system_key)

    resp = http_post(url, headers=headers, json=body)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict):
        raise ValueError("Unexpected response format for footprint_services")
    return data


def get_service_launch_logs(
    *,
    service_name: str,
    port: int = 8000,
    profile: str | None = None,
    variety: str | None = None,
    top: int | None = None,
    last: int | None = None,
    log_type: str = "container",
    system_key: str | None = None,
) -> dict[str, Any]:
    """Call the provisioner service launch logs endpoint."""
    url = f"http://localhost:{port}/srv/services/launch-logs/{service_name}/"
    params = {}
    if profile:
        params["profile"] = profile
    if variety:
        params["variety"] = variety
    if top:
        params["top"] = top
    if last:
        params["last"] = last

    headers = _auth_headers(system_key)
    resp = http_get(url, headers=headers, params=params)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict):
        raise ValueError(
            "Unexpected response format for get_service_launch_logs"
        )
    return data


def get_service_logs(
    *,
    service_name: str,
    port: int = 8000,
    profile: str | None = None,
    variety: str | None = None,
    top: int | None = None,
    last: int | None = None,
    system_key: str | None = None,
) -> dict[str, Any]:
    """Call the provisioner service logs endpoint."""
    url = f"http://localhost:{port}/srv/services/logs/{service_name}/"
    params = {}
    if profile:
        params["profile"] = profile
    if variety:
        params["variety"] = variety
    if top:
        params["top"] = top
    if last:
        params["last"] = last

    headers = _auth_headers(system_key)
    resp = http_get(url, headers=headers, params=params)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict):
        raise ValueError("Unexpected response format for get_service_logs")
    return data


def get_footprint_logs(
    *,
    service_name: str,
    port: int = 8000,
    profile: str | None = None,
    variety: str | None = None,
    top: int | None = None,
    last: int | None = None,
    log_type: str = "container",
    system_key: str | None = None,
) -> dict[str, Any]:
    """Call the provisioner footprint logs endpoint."""
    url = (
        f"http://localhost:{port}/srv/services/footprint-logs/"
        f"{log_type}/{service_name}/"
    )
    params = {}
    if profile:
        params["profile"] = profile
    if variety:
        params["variety"] = variety
    if top:
        params["top"] = top
    if last:
        params["last"] = last

    headers = _auth_headers(system_key)
    resp = http_get(url, headers=headers, params=params)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict):
        raise ValueError("Unexpected response format for get_footprint_logs")
    return data
