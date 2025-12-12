import os
from typing import Any, Dict, List

from .http import get as http_get

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
