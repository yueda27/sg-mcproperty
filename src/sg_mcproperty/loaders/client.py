import os
from typing import Any

import requests
from loguru import logger

URA_API_BASE_URL = "https://eservice.ura.gov.sg"
URA_TOKEN_PATH = "/uraDataService/insertNewToken/v1"
URA_DATA_PATH = "/uraDataService/invokeUraDS/v1"
REQUEST_TIMEOUT_S = 60

# Private residential transactions are served in this many batches (batch=1..4).
TRANSACTION_BATCHES = (1, 2, 3, 4)


class URAError(RuntimeError):
    """Raised when a URA API call fails or returns a non-Success status."""


class URAClient:
    """Sync client for the URA Data Service.

    The access key defaults to the ``URA_ACCESS_KEY`` env var. A daily token is fetched
    lazily on the first data call and reused; if URA rejects it (they expire daily), it is
    refreshed once and the call retried.

        with URAClient() as ura:
            rows = ura.private_transactions()
    """

    def __init__(self, access_key: str | None = None) -> None:
        self._access_key = access_key or os.environ.get("URA_ACCESS_KEY")
        if not self._access_key:
            raise URAError("URA access key missing: set URA_ACCESS_KEY")
        self._token: str | None = None
        self._session = requests.Session()
        self._session.headers.update(
            {"AccessKey": self._access_key, "User-Agent": "sg-mcproperty"}
        )

    def __enter__(self) -> "URAClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self._session.close()

    # -- auth ------------------------------------------------------------------------

    def authenticate(self) -> str:
        """Fetch a fresh daily token and cache it. Returns the token."""
        logger.info("Authenticating with URA")
        payload = self._get(f"{URA_API_BASE_URL}{URA_TOKEN_PATH}")
        token = payload.get("Result")
        if not token:
            raise URAError("URA authentication succeeded but returned no token")
        self._token = token
        return token

    @property
    def token(self) -> str:
        return self._token or self.authenticate()

    # -- generic ---------------------------------------------------------------------

    def fetch(self, service: str, **params: Any) -> list[dict[str, Any]]:
        """Call any URA data service and return its ``Result`` list.

        Extra keyword args are passed as query parameters (e.g. ``batch=1``).
        """
        query = {"service": service, **params}
        try:
            payload = self._get_data(query)
        except URAError as exc:
            # Token may have expired mid-session; refresh once and retry.
            if "token" not in str(exc).lower():
                raise
            logger.warning("URA token rejected, refreshing")
            self.authenticate()
            payload = self._get_data(query)
        return payload.get("Result") or []

    # -- datasets --------------------------------------------------------------------

    def private_transactions(
        self, batches: tuple[int, ...] = TRANSACTION_BATCHES
    ) -> list[dict[str, Any]]:
        """Private residential transactions, last 5 years. One project per element,
        each with a nested ``transaction`` list (``contractDate`` is ``mmyy``)."""
        projects: list[dict[str, Any]] = []
        for batch in batches:
            logger.info(f"Fetching URA private transactions batch {batch}")
            projects.extend(self.fetch("PMI_Resi_Transaction", batch=batch))
        return projects

    def rental_contracts(self, ref_period: str) -> list[dict[str, Any]]:
        """Private residential rental contracts for a quarter, e.g. ``"24q1"``."""
        return self.fetch("PMI_Resi_Rental", refPeriod=ref_period)

    def rental_median(self) -> list[dict[str, Any]]:
        """Median private residential rentals by project."""
        return self.fetch("PMI_Resi_Rental_Median")

    def new_sale_pipeline(self) -> list[dict[str, Any]]:
        """Private residential development pipeline (unsold/launched projects)."""
        return self.fetch("PMI_Resi_Pipeline")

    def car_park_details(self) -> list[dict[str, Any]]:
        """Car park details (rates, hours, location)."""
        return self.fetch("Car_Park_Details")

    # -- internals -------------------------------------------------------------------

    def _get_data(self, query: dict[str, Any]) -> dict[str, Any]:
        return self._get(
            f"{URA_API_BASE_URL}{URA_DATA_PATH}",
            params=query,
            headers={"Token": self.token},
        )

    def _get(self, url: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = self._session.get(url, timeout=REQUEST_TIMEOUT_S, **kwargs)
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise URAError(f"URA request failed: {exc}") from exc

        status = payload.get("Status")
        if status != "Success":
            raise URAError(
                f"URA returned status={status!r}, message={payload.get('Message')!r}"
            )
        return payload
