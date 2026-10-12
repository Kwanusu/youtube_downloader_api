"""Zindua LMS (Rukia API) client: cohorts, class sessions and session recordings.

Authenticates as a Zitadel machine user (client credentials) or with a static
access token, and scopes every request to the organisation via X-Org-Id.
"""
import threading
import time

import requests

import config

REQUEST_TIMEOUT = (10, 60)
TOKEN_SAFETY_MARGIN = 300
MAX_PAGE_SIZE = 200


class LmsError(RuntimeError):
    pass


class LmsClient:
    def __init__(self, session=None):
        self.session = session or requests.Session()
        self._token = config.LMS_ACCESS_TOKEN or None
        self._token_expires_at = float("inf") if self._token else 0.0
        self._lock = threading.Lock()
        self._emails = {}

    # ---- auth -------------------------------------------------------------

    def access_token(self, force_refresh=False):
        with self._lock:
            if config.LMS_ACCESS_TOKEN:
                return config.LMS_ACCESS_TOKEN
            if force_refresh or not self._token or time.time() >= self._token_expires_at:
                self._refresh()
            return self._token

    def _refresh(self):
        if not (config.LMS_CLIENT_ID and config.LMS_CLIENT_SECRET):
            raise LmsError("Set LMS_CLIENT_ID/LMS_CLIENT_SECRET or LMS_ACCESS_TOKEN in .env")
        response = self.session.post(
            config.LMS_TOKEN_URL,
            data={"grant_type": "client_credentials", "scope": config.LMS_SCOPE},
            auth=(config.LMS_CLIENT_ID, config.LMS_CLIENT_SECRET),
            timeout=REQUEST_TIMEOUT,
        )
        data = response.json() if response.content else {}
        if response.status_code != 200 or "access_token" not in data:
            raise LmsError(f"LMS token request failed ({response.status_code}): {data}")
        self._token = data["access_token"]
        expires_in = int(data.get("expires_in", 3600))
        self._token_expires_at = time.time() + max(expires_in - TOKEN_SAFETY_MARGIN, 60)

    def _request(self, method, path, **kwargs):
        """Send an authorised, org-scoped request, refreshing the token once on 401."""
        if not config.LMS_ORG_ID:
            raise LmsError("LMS_ORG_ID is not set")
        kwargs.setdefault("timeout", REQUEST_TIMEOUT)
        url = f"{config.LMS_API_BASE_URL}{path}"
        for attempt in range(2):
            headers = {
                "Authorization": f"Bearer {self.access_token(force_refresh=attempt > 0)}",
                "X-Org-Id": config.LMS_ORG_ID,
            }
            response = self.session.request(method, url, headers=headers, **kwargs)
            if response.status_code != 401 or attempt == 1 or config.LMS_ACCESS_TOKEN:
                return response
        return response

    def _json(self, method, path, **kwargs):
        response = self._request(method, path, **kwargs)
        if response.status_code >= 400:
            raise LmsError(f"{method} {path} -> {response.status_code}: {_detail(response)}")
        return response.json() if response.content else {}

    def _paged(self, path, key, params=None, limit=None):
        """Yield items across pages of a paginated list endpoint."""
        params = dict(params or {}, page=1, page_size=MAX_PAGE_SIZE)
        seen = 0
        while True:
            data = self._json("GET", path, params=params)
            items = data.get(key) or []
            for item in items:
                yield item
                seen += 1
                if limit and seen >= limit:
                    return
            if not items or params["page"] >= int(data.get("total_pages") or 1):
                return
            params["page"] += 1

    # ---- cohorts & sessions -------------------------------------------------

    def active_cohorts(self):
        return list(self._paged("/cohort/all", "cohorts", {"status": "active"}))

    def past_sessions(self, cohort_id, limit=None):
        """Most recent past sessions of a cohort (all of its classes), newest first."""
        return list(self._paged(
            f"/cohort/{cohort_id}/schedule", "sessions",
            {"scope": "past", "sort_by": "session_date", "sort_order": "desc"},
            limit=limit or config.LMS_SESSION_LOOKBACK,
        ))

    def user_email(self, user_id):
        if user_id not in self._emails:
            try:
                email = self._json("GET", f"/user/users/{user_id}").get("email") or ""
            except LmsError:
                email = ""
            self._emails[user_id] = email.strip().lower()
        return self._emails[user_id]

    # ---- recordings -----------------------------------------------------------

    def session_recordings(self, session_id):
        return self._json("GET", f"/event/session/{session_id}/recordings").get("recordings", [])

    def add_session_recording(self, session_id, recording_url, title):
        """Attach a recording link to a class session. Idempotent: returns the
        existing recording's id if this URL is already on the session."""
        for existing in self.session_recordings(session_id):
            if existing.get("recording_url") == recording_url:
                return existing.get("id")
        response = self._request("POST", "/event/session-recording", json={
            "session_id": int(session_id), "recording_url": recording_url,
            "title": title[:255], "duration": None,
        })
        if response.status_code >= 400:
            raise LmsError(f"Could not add recording to session {session_id} "
                           f"({response.status_code}): {_detail(response)}")
        data = response.json() if response.content else {}
        return (data.get("recording") or data).get("id")


def _detail(response):
    try:
        data = response.json()
    except ValueError:
        return response.text[:300]
    return data.get("detail") or data.get("message") or data
