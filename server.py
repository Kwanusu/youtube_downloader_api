"""Web service: Zoho Flow webhook, background poller, and cohort recordings dashboard.

Run with:  python server.py   (serves on HOST:PORT via waitress)
Put it behind nginx/Caddy with HTTPS before exposing it to the internet.
"""
import hmac
import threading
from functools import wraps

from flask import Flask, Response, abort, redirect, render_template_string, request, url_for

import config
import pipeline
import store

app = Flask(__name__)
conn = store.connect()
_wake = threading.Event()


# ---- auth ------------------------------------------------------------------

def _matches(supplied, expected):
    return bool(expected) and hmac.compare_digest(supplied or "", expected)


def _role():
    auth = request.authorization
    if not auth:
        return None
    if auth.username == config.ADMIN_USER and _matches(auth.password, config.ADMIN_PASSWORD):
        return "admin"
    if auth.username == config.DASHBOARD_USER and _matches(auth.password, config.DASHBOARD_PASSWORD):
        return "mentor"
    return None


def requires(role):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            current = _role()
            if current is None or (role == "admin" and current != "admin"):
                return Response("Login required", 401, {"WWW-Authenticate": 'Basic realm="Class uploader"'})
            return view(*args, **kwargs)
        return wrapped
    return decorator


# ---- routes ----------------------------------------------------------------

@app.post("/webhook/zoho")
def zoho_webhook():
    """Called by a Zoho Flow 'Meeting recording uploaded' trigger. The payload is
    not trusted for anything; it only wakes the worker, which re-reads Zoho."""
    token = request.args.get("token") or request.headers.get("X-Webhook-Token")
    if not _matches(token, config.WEBHOOK_SECRET):
        abort(403)
    _wake.set()
    return {"queued": True}


@app.get("/health")
def health():
    return {"ok": True}


PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Class Recordings</title>
<style>
 body{font-family:system-ui,sans-serif;margin:0 auto;max-width:1100px;padding:16px;color:#1d1d1f;background:#fafafa}
 h1{font-size:1.4rem} h2{font-size:1.1rem;margin-top:2rem}
 table{border-collapse:collapse;width:100%;background:#fff;font-size:.9rem}
 th,td{border-bottom:1px solid #e5e5e5;padding:8px;text-align:left;vertical-align:top}
 .s{padding:2px 8px;border-radius:10px;font-size:.8rem;white-space:nowrap}
 .done{background:#dcfce7}.failed{background:#fee2e2}.needs_review{background:#fef3c7}
 .pending,.uploaded{background:#e0e7ff}
 form.inline{display:inline} input,select{padding:6px;margin:2px} button{padding:6px 10px;cursor:pointer}
 .err{color:#b91c1c;font-size:.8rem} .muted{color:#6b7280;font-size:.8rem} .wrap{overflow-x:auto}
</style></head><body>
<h1>Class Recordings</h1>
<p>Zoho recordings are matched to their cohort's class session in the LMS, uploaded to the cohort's
 YouTube playlist, linked on the session for enrolled students, then removed from Zoho.
 {% if admin %}<form class="inline" method="post" action="{{ url_for('run_now') }}"><button>Check Zoho now</button></form>{% endif %}</p>

<h2>Recent recordings</h2><div class="wrap"><table>
<tr><th>Recorded</th><th>Topic</th><th>Cohort / session</th><th>Status</th><th>Video</th>{% if admin %}<th></th>{% endif %}</tr>
{% for r in recordings %}<tr>
 <td>{{ r.recorded_at or '' }}</td><td>{{ r.topic or '' }}<div class="muted">{{ r.mentor_key or 'unknown host' }}</div></td>
 <td>{% if r.cohort_name %}{{ r.cohort_name }}{% if r.session_id %}<div class="muted">session {{ r.session_id }}{% if r.lms_recording_id %} &middot; linked in LMS{% endif %}</div>{% endif %}
   {% if r.match_reason %}<div class="muted">by {{ r.match_reason }}</div>{% endif %}{% else %}&ndash;{% endif %}</td>
 <td><span class="s {{ r.status }}">{{ r.status.replace('_',' ') }}</span>
   {% if r.last_error and r.status != 'done' %}<div class="err">{{ r.last_error[:200] }}</div>{% endif %}</td>
 <td>{% if r.video_id %}<a href="https://youtu.be/{{ r.video_id }}" target="_blank">watch</a>{% endif %}</td>
 {% if admin %}<td>
   {% if r.status == 'failed' %}<form class="inline" method="post" action="{{ url_for('retry', rec_id=r.erecording_id) }}"><button>Retry</button></form>{% endif %}
   {% if r.status in ('needs_review', 'failed') and cohorts %}
   <form method="post" action="{{ url_for('assign', rec_id=r.erecording_id) }}">
     <select name="cohort_id" required>{% for c in cohorts %}<option value="{{ c.cohort_id }}">{{ c.name }}</option>{% endfor %}</select>
     <input name="session_id" placeholder="LMS session id (optional)" size="14" inputmode="numeric">
     <button>Assign</button></form>{% endif %}
 </td>{% endif %}
</tr>{% else %}<tr><td colspan="6">Nothing processed yet.</td></tr>{% endfor %}
</table></div>

<h2>Cohort playlists</h2>
<p class="muted">Active cohorts are read from the LMS. A playlist is created automatically on a cohort's first upload.</p>
<div class="wrap"><table>
<tr><th>Cohort</th><th>Type</th><th>Playlist</th>{% if admin %}<th>Use an existing playlist</th>{% endif %}</tr>
{% for c in cohorts %}<tr><td>{{ c.name }}<div class="muted">LMS id {{ c.cohort_id }}</div></td><td>{{ c.type }}</td>
 <td>{% if c.playlist_id %}<a href="https://www.youtube.com/playlist?list={{ c.playlist_id }}" target="_blank">{{ c.playlist_id }}</a>{% else %}<span class="muted">created on first upload</span>{% endif %}</td>
 {% if admin %}<td><form class="inline" method="post" action="{{ url_for('set_playlist', cohort_id=c.cohort_id) }}">
   <input name="playlist_id" placeholder="Playlist ID or URL"><button>Save</button></form></td>{% endif %}</tr>
{% else %}<tr><td colspan="4">No cohorts yet; they appear after the first sync with the LMS.</td></tr>{% endfor %}
</table></div>
</body></html>"""


@app.get("/")
@requires("mentor")
def dashboard():
    return render_template_string(
        PAGE, admin=_role() == "admin",
        recordings=store.recent_recordings(conn), cohorts=store.list_cohorts(conn),
    )


def _playlist_id(value):
    value = value.strip()
    if "list=" in value:
        value = value.split("list=", 1)[1].split("&", 1)[0]
    return value


@app.post("/cohorts/<int:cohort_id>/playlist")
@requires("admin")
def set_playlist(cohort_id):
    store.set_cohort_playlist(conn, cohort_id, _playlist_id(request.form.get("playlist_id", "")))
    return redirect(url_for("dashboard"))


@app.post("/recordings/<rec_id>/assign")
@requires("admin")
def assign(rec_id):
    session_id = request.form.get("session_id", "").strip()
    if session_id and not session_id.isdigit():
        abort(400, "Session id must be a number")
    store.assign_recording(conn, rec_id, int(request.form["cohort_id"]), session_id or None)
    _wake.set()
    return redirect(url_for("dashboard"))


@app.post("/recordings/<rec_id>/retry")
@requires("admin")
def retry(rec_id):
    store.retry_recording(conn, rec_id)
    _wake.set()
    return redirect(url_for("dashboard"))


@app.post("/run")
@requires("admin")
def run_now():
    _wake.set()
    return redirect(url_for("dashboard"))


# ---- background worker -------------------------------------------------------

def worker():
    while True:
        try:
            print("Results:", pipeline.run_cycle(conn))
        except Exception as e:
            print(f"Cycle failed: {e}")
        _wake.wait(timeout=config.POLL_INTERVAL_SECONDS)
        _wake.clear()


def main():
    missing = [n for n in ("ZOHO_ZSOID", "ZOHO_CLIENT_ID", "ZOHO_CLIENT_SECRET", "ZOHO_REFRESH_TOKEN",
                           "LMS_ORG_ID", "WEBHOOK_SECRET", "DASHBOARD_PASSWORD", "ADMIN_PASSWORD")
               if not getattr(config, n)]
    if not (config.LMS_ACCESS_TOKEN or (config.LMS_CLIENT_ID and config.LMS_CLIENT_SECRET)):
        missing.append("LMS_CLIENT_ID/LMS_CLIENT_SECRET (or LMS_ACCESS_TOKEN)")
    if missing:
        raise SystemExit(f"Missing settings in .env: {', '.join(missing)}")
    threading.Thread(target=worker, daemon=True).start()
    from waitress import serve
    print(f"Serving on http://{config.HOST}:{config.PORT}")
    serve(app, host=config.HOST, port=config.PORT, threads=4)


if __name__ == "__main__":
    main()
