import os
from typing import Any, Dict, List

from .http import (
    get as http_get,
    post as http_post,
)

DEFAULT_SYSTEM_KEY = "jenny8675"


def _auth_headers(system_key: str | None = None) -> Dict[str, str]:
    key = system_key or os.environ.get("OZWALD_SYSTEM_KEY", DEFAULT_SYSTEM_KEY)
    return {"Authorization": f"Bearer {key}"}


def get_configured_services(
    *, port: int = 8000, system_key: str | None = None
) -> List[Dict[str, Any]]:
    url = f"http://localhost:{port}/srv/services/configured/"
    headers = _auth_headers(system_key)
    resp = http_get(url, headers=headers)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list):
        raise ValueError("Unexpected response format for configured services")
    return data


def get_active_services(
    *, port: int = 8000, system_key: str | None = None
) -> List[Dict[str, Any]]:
    url = f"http://localhost:{port}/srv/services/active/"
    headers = _auth_headers(system_key)
    resp = http_get(url, headers=headers)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list):
        raise ValueError("Unexpected response format for active services")
    return data


def get_host_resources(
    *, port: int = 8000, system_key: str | None = None
) -> Dict[str, Any]:
    url = f"http://localhost:{port}/srv/host/resources"
    headers = _auth_headers(system_key)
    resp = http_get(url, headers=headers)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict):
        raise ValueError("Unexpected response format for host resources")
    return data


def update_services(
    *,
    port: int = 8000,
    body: List[Dict[str, Any]] | List[Any],
    system_key: str | None = None,
) -> Dict[str, Any]:
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
