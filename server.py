"""Web service: Zoho Flow webhook, background poller, and mentor dashboard.

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
 .done{background:#dcfce7}.failed{background:#fee2e2}.needs_mapping{background:#fef3c7}
 .pending,.uploaded{background:#e0e7ff}
 form.inline{display:inline} input{padding:6px;margin:2px} button{padding:6px 10px;cursor:pointer}
 .err{color:#b91c1c;font-size:.8rem} .wrap{overflow-x:auto}
</style></head><body>
<h1>Class Recordings</h1>
<p>Zoho recordings are uploaded to each mentor's class playlist, then removed from Zoho.
 {% if admin %}<form class="inline" method="post" action="{{ url_for('run_now') }}"><button>Check Zoho now</button></form>{% endif %}</p>

<h2>Recent recordings</h2><div class="wrap"><table>
<tr><th>Recorded</th><th>Topic</th><th>Mentor / class</th><th>Status</th><th>Video</th>{% if admin %}<th></th>{% endif %}</tr>
{% for r in recordings %}<tr>
 <td>{{ r.recorded_at or '' }}</td><td>{{ r.topic or '' }}</td>
 <td>{{ r.display_name or r.mentor_key or 'unknown' }}{% if r.class_name %}<br><small>{{ r.class_name }}</small>{% endif %}</td>
 <td><span class="s {{ r.status }}">{{ r.status.replace('_',' ') }}</span>
   {% if r.last_error and r.status != 'done' %}<div class="err">{{ r.last_error[:200] }}</div>{% endif %}</td>
 <td>{% if r.video_id %}<a href="https://youtu.be/{{ r.video_id }}" target="_blank">watch</a>{% endif %}</td>
 {% if admin %}<td>{% if r.status == 'failed' %}<form class="inline" method="post" action="{{ url_for('retry', rec_id=r.erecording_id) }}"><button>Retry</button></form>{% endif %}</td>{% endif %}
</tr>{% else %}<tr><td colspan="6">Nothing processed yet.</td></tr>{% endfor %}
</table></div>

<h2>Mentors &amp; class playlists</h2><div class="wrap"><table>
<tr><th>Zoho host (email)</th><th>Mentor</th><th>Class</th><th>Playlist</th>{% if admin %}<th></th>{% endif %}</tr>
{% for m in mentors %}<tr><td>{{ m.mentor_key }}</td><td>{{ m.display_name }}</td><td>{{ m.class_name }}</td>
 <td><a href="https://www.youtube.com/playlist?list={{ m.playlist_id }}" target="_blank">{{ m.playlist_id }}</a></td>
 {% if admin %}<td><form class="inline" method="post" action="{{ url_for('remove_mentor') }}">
   <input type="hidden" name="mentor_key" value="{{ m.mentor_key }}"><button>Remove</button></form></td>{% endif %}</tr>
{% endfor %}</table></div>
{% if admin %}
<h3>Add / update mentor</h3>
<form method="post" action="{{ url_for('save_mentor') }}">
 <input name="mentor_key" placeholder="Zoho host email" required>
 <input name="display_name" placeholder="Mentor name" required>
 <input name="class_name" placeholder="Class name" required>
 <input name="playlist_id" placeholder="YouTube playlist ID or URL" required>
 <button>Save</button></form>
{% endif %}
</body></html>"""


@app.get("/")
@requires("mentor")
def dashboard():
    return render_template_string(
        PAGE, admin=_role() == "admin",
        recordings=store.recent_recordings(conn), mentors=store.list_mentors(conn),
    )


def _playlist_id(value):
    value = value.strip()
    if "list=" in value:
        value = value.split("list=", 1)[1].split("&", 1)[0]
    return value


@app.post("/mentors")
@requires("admin")
def save_mentor():
    f = request.form
    store.upsert_mentor(conn, f["mentor_key"], f["display_name"], f["class_name"], _playlist_id(f["playlist_id"]))
    _wake.set()
    return redirect(url_for("dashboard"))


@app.post("/mentors/remove")
@requires("admin")
def remove_mentor():
    store.delete_mentor(conn, request.form["mentor_key"])
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
                           "WEBHOOK_SECRET", "DASHBOARD_PASSWORD", "ADMIN_PASSWORD")
               if not getattr(config, n)]
    if missing:
        raise SystemExit(f"Missing settings in .env: {', '.join(missing)}")
    threading.Thread(target=worker, daemon=True).start()
    from waitress import serve
    print(f"Serving on http://{config.HOST}:{config.PORT}")
    serve(app, host=config.HOST, port=config.PORT, threads=4)


if __name__ == "__main__":
    main()
