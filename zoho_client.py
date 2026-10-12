"""Zoho Meeting API client with automatic access-token refresh."""
import threading
import time

import requests

import config

REQUEST_TIMEOUT = (10, 60)
DOWNLOAD_CHUNK_SIZE = 8 * 1024 * 1024
TOKEN_SAFETY_MARGIN = 300  # refresh 5 minutes before Zoho's 1-hour expiry


class ZohoClient:
    def __init__(self, session=None):
        self.session = session or requests.Session()
        self._token = None
        self._token_expires_at = 0.0
        self._lock = threading.Lock()

    # ---- auth -------------------------------------------------------------

    def access_token(self, force_refresh=False):
        with self._lock:
            if force_refresh or not self._token or time.time() >= self._token_expires_at:
                self._refresh()
            return self._token

    def _refresh(self):
        response = self.session.post(
            f"https://accounts.{config.ZOHO_DOMAIN}/oauth/v2/token",
            data={
                "refresh_token": config.ZOHO_REFRESH_TOKEN,
                "client_id": config.ZOHO_CLIENT_ID,
                "client_secret": config.ZOHO_CLIENT_SECRET,
                "grant_type": "refresh_token",
            },
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
        if "access_token" not in data:
            raise RuntimeError(f"Zoho OAuth refresh failed: {data}")
        self._token = data["access_token"]
        expires_in = int(data.get("expires_in", 3600))
        self._token_expires_at = time.time() + max(expires_in - TOKEN_SAFETY_MARGIN, 60)

    def _request(self, method, url, **kwargs):
        """Send an authorised request, refreshing the token once on 401."""
        kwargs.setdefault("timeout", REQUEST_TIMEOUT)
        for attempt in range(2):
            headers = {"Authorization": f"Zoho-oauthtoken {self.access_token(force_refresh=attempt > 0)}"}
            response = self.session.request(method, url, headers=headers, **kwargs)
            if response.status_code != 401 or attempt == 1:
                return response
            response.close()
        return response

    # ---- recordings -------------------------------------------------------

    def list_recordings(self):
        response = self._request("GET", f"{config.ZOHO_API_BASE}/{config.ZOHO_ZSOID}/recordings.json")
        response.raise_for_status()
        return response.json().get("recordings", [])

    def download_recording(self, download_url, output_path):
        """Stream a recording to disk. Returns the Content-Type Zoho reported."""
        with self._request("GET", download_url, stream=True) as response:
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "")
            with open(output_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE):
                    if chunk:
                        f.write(chunk)
        return content_type

    def delete_recording(self, erecording_id):
        response = self._request(
            "DELETE", f"{config.ZOHO_API_BASE}/{config.ZOHO_ZSOID}/recordings/{erecording_id}.json"
        )
        response.raise_for_status()
        return response.status_code in (200, 204)


def mentor_keys(rec):
    """Candidate identifiers for the mentor who hosted a recording, best first.

    Zoho's field names vary by API version, so several are checked.
    """
    keys = []
    for field in ("creatorEmail", "hostEmail", "presenterEmail", "email",
                  "creatorName", "hostName", "presenterName", "creator"):
        value = rec.get(field)
        if isinstance(value, str) and value.strip():
            key = value.strip().lower()
            if key not in keys:
                keys.append(key)
    return keys
