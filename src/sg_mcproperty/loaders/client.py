import os
import threading
from concurrent.futures import ThreadPoolExecutor
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
        self._auth_lock = threading.Lock()
        self._session = requests.Session()
        self._session.headers.update({"AccessKey": self._access_key, "User-Agent": "sg-mcproperty"})

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
        stale_token = self.token
        try:
            payload = self._get_data(query)
        except URAError as exc:
            # Token may have expired mid-session; refresh once and retry.
            if "token" not in str(exc).lower():
                raise
            logger.warning("URA token rejected, refreshing")
            with self._auth_lock:
                # Another thread may already have refreshed it while we waited.
                if self._token == stale_token:
                    self.authenticate()
            payload = self._get_data(query)
        return payload.get("Result") or []

    # -- datasets --------------------------------------------------------------------

    def private_transactions(
        self, batches: tuple[int, ...] = TRANSACTION_BATCHES
    ) -> list[dict[str, Any]]:
        """Private residential transactions, last 5 years. One project per element,
        each with a nested ``transaction`` list (``contractDate`` is ``mmyy``)."""
        self.token  # authenticate once up front so threads don't race to get tokens

        def fetch_batch(batch: int) -> list[dict[str, Any]]:
            logger.info(f"Fetching URA private transactions batch {batch}")
            return self.fetch("PMI_Resi_Transaction", batch=batch)

        # Batches are independent, so fetch them concurrently (I/O bound). map()
        # preserves batch order and re-raises the first failure.
        with ThreadPoolExecutor(max_workers=len(batches)) as pool:
            results = list(pool.map(fetch_batch, batches))
        return [project for result in results for project in result]

    def rental_contracts(self, ref_period: str) -> list[dict[str, Any]]:
        """Private residential rental contracts for a quarter, e.g. ``"24q1"``."""
        return self.fetch("PMI_Resi_Rental", refPeriod=ref_period)

    def rental_contracts_many(
        self, ref_periods: list[str], max_workers: int = 6
    ) -> dict[str, list[dict[str, Any]]]:
        """Rental contracts for several quarters, fetched in parallel. Keys are the quarters.

        Quarters URA has no data for (too old, or not yet published) come back as empty lists.
        """
        self.token  # authenticate once up front so threads do not race to get tokens

        def fetch_quarter(ref_period: str) -> list[dict[str, Any]]:
            logger.info(f"Fetching URA rental contracts {ref_period}")
            return self.rental_contracts(ref_period)

        with ThreadPoolExecutor(max_workers=min(max_workers, len(ref_periods)) or 1) as pool:
            results = list(pool.map(fetch_quarter, ref_periods))
        return dict(zip(ref_periods, results))

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
            raise URAError(f"URA returned status={status!r}, message={payload.get('Message')!r}")
        return payload
