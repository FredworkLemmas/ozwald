import os
from typing import Any, Dict, Optional, Tuple, Union

import requests


# Parse timeout from env. Supports:
# - single float seconds: "5" or "5.0"
# - two comma-separated floats: "3.05,10" => (connect, read)
def _parse_timeout_env(
    value: Optional[str],
) -> Union[float, Tuple[float, float]]:
    if not value:
        return 5.0
    try:
        if "," in value:
            parts = [p.strip() for p in value.split(",", 1)]
            return float(parts[0]), float(parts[1])
        return float(value)
    except Exception:
        # Fallback to a conservative default
        return 5.0


DEFAULT_HTTP_TIMEOUT: Union[float, Tuple[float, float]] = _parse_timeout_env(
    os.environ.get("OZWALD_HTTP_TIMEOUT")
)


def get(
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    timeout: Optional[Union[float, Tuple[float, float]]] = None,
    session: Optional[requests.Session] = None,
    **kwargs: Any,
) -> requests.Response:
    """HTTP GET wrapper enforcing a timeout by default.

    Args:
        url: URL to fetch
        headers: Optional headers to include
        timeout: Optional timeout override (seconds or (connect, read)).
        session: Optional requests.Session to use
        **kwargs: Passed through to requests.get
    """
    to = timeout if timeout is not None else DEFAULT_HTTP_TIMEOUT
    if session is None:
        return requests.get(url, headers=headers, timeout=to, **kwargs)
    return session.get(url, headers=headers, timeout=to, **kwargs)
