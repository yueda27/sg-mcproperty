import os

import requests
from loguru import logger

URA_API_BASE_URL = "https://eservice.ura.gov.sg"
URA_TOKEN_PATH = "/uraDataService/insertNewToken/v1"
REQUEST_TIMEOUT_S = 30


class URAAuthError(RuntimeError):
    """Raised when a URA daily token cannot be obtained."""


def authenticate_ura(access_key: str | None = None) -> str:
    """Fetch a URA daily token (valid for one day, SGT).

    The access key defaults to the ``URA_ACCESS_KEY`` env var. Returns the token, which
    must be sent alongside the access key as the ``Token`` header on data requests.
    """
    access_key = access_key or os.environ.get("URA_ACCESS_KEY")
    if not access_key:
        raise URAAuthError("URA access key missing: set URA_ACCESS_KEY")

    auth_url = f"{URA_API_BASE_URL}{URA_TOKEN_PATH}"
    logger.info("Authenticating with URA")

    try:
        response = requests.get(
            auth_url,
            headers={"AccessKey": access_key, "User-Agent": "sg-mcproperty"},
            timeout=REQUEST_TIMEOUT_S,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise URAAuthError(f"URA authentication request failed: {exc}") from exc

    auth_status = payload.get("Status")
    logger.info(f"Authentication status: {auth_status}")
    if auth_status != "Success":
        raise URAAuthError(
            f"URA authentication failed: status={auth_status!r}, "
            f"message={payload.get('Message')!r}"
        )

    token = payload.get("Result")
    if not token:
        raise URAAuthError("URA authentication succeeded but returned no token")
    return token
