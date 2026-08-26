
import os, json, uuid, csv, io, hmac
from datetime import datetime, timezone
from functools import wraps

from flask import Flask, request, jsonify, render_template, Response, redirect, url_for, session
from sqlalchemy import create_engine, text

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_URL = os.environ.get("DATABASE_URL") or f"sqlite:///{os.path.join(BASE_DIR, 'adhd_local.sqlite3')}"
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = "postgresql://" + DATABASE_URL[len("postgres://"):]

app = Flask(__name__, template_folder=BASE_DIR)
app.secret_key = os.environ.get("SECRET_KEY", "local-development-secret-change-me")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("RENDER") == "true",
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)

SCHEMA = """
CREATE TABLE IF NOT EXISTS interviews (
    id VARCHAR(64) PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    patient_id TEXT,
    interviewer TEXT,
    referrer TEXT,
    visit_date TEXT,
    status TEXT DEFAULT 'in_progress',
    answers_json TEXT NOT NULL DEFAULT '{}',
    summary_text TEXT DEFAULT '',
    conclusions TEXT DEFAULT '',
    duration_ms BIGINT DEFAULT 0,
    feedback_summary_fit TEXT DEFAULT '',
    feedback_completeness TEXT DEFAULT '',
    feedback_flow TEXT DEFAULT '',
    feedback_unclear TEXT DEFAULT '',
    feedback_missing TEXT DEFAULT '',
    feedback_notes TEXT DEFAULT ''
)
"""

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def init_db():
    with engine.begin() as c:
        c.execute(text(SCHEMA))

init_db()

def safe_equal(a, b):
    return hmac.compare_digest(str(a or ""), str(b or ""))

def require_operator(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if session.get("operator_ok") or session.get("admin_ok"):
            return fn(*args, **kwargs)
        return redirect(url_for("login", next=request.path))
    return wrapped

def require_admin(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if session.get("admin_ok"):
            return fn(*args, **kwargs)
        return redirect(url_for("admin_login", next=request.path))
    return wrapped

@app.get("/health")
def health():
    return jsonify(ok=True)

@app.route("/login", methods=["GET", "POST"])
def login():
    error = ""
    if request.method == "POST":
        if safe_equal(request.form.get("password"), os.environ.get("OPERATOR_PASSWORD", "operator")):
            session["operator_ok"] = True
            return redirect(request.args.get("next") or url_for("home"))
        error = "Password non corretta."
    return render_template("login.html", title="Accesso operatori", error=error, admin=False)

@app.route("/admin-login", methods=["GET", "POST"])
def admin_login():
    error = ""
    if request.method == "POST":
        if safe_equal(request.form.get("password"), os.environ.get("ADMIN_PASSWORD", "admin")):
            session["admin_ok"] = True
            return redirect(request.args.get("next") or url_for("dashboard"))
        error = "Password non corretta."
    return render_template("login.html", title="Accesso amministratore", error=error, admin=True)

@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.get("/")
@require_operator
def home():
    return render_template("interview.html")

@app.get("/dashboard")
@require_admin
def dashboard():
    return render_template("dashboard.html")

@app.post("/api/interviews")
@require_operator
def create_interview():
    iid = str(uuid.uuid4())
    t = now_iso()
    with engine.begin() as c:
        c.execute(text("""
            INSERT INTO interviews(id,created_at,updated_at,status,answers_json,duration_ms)
            VALUES(:id,:created,:updated,'in_progress','{}',0)
        """), {"id": iid, "created": t, "updated": t})
    return jsonify({"id": iid})

@app.get("/api/interviews/<iid>")
@require_operator
def get_interview(iid):
    with engine.begin() as c:
        r = c.execute(text("SELECT * FROM interviews WHERE id=:id"), {"id": iid}).mappings().first()
    if not r:
        return jsonify({"error": "not_found"}), 404
    d = dict(r)
    try:
        d["answers"] = json.loads(d.pop("answers_json") or "{}")
    except Exception:
        d["answers"] = {}
    return jsonify(d)

@app.put("/api/interviews/<iid>")
@require_operator
def save_interview(iid):
    x = request.get_json(force=True) or {}
    a = x.get("answers") or {}
    f = x.get("operator_feedback") or {}
    def val(k):
        return (a.get(k) or {}).get("value", "")
    with engine.begin() as c:
        exists = c.execute(text("SELECT 1 FROM interviews WHERE id=:id"), {"id": iid}).first()
        if not exists:
            return jsonify({"error": "not_found"}), 404
        c.execute(text("""
            UPDATE interviews SET
              updated_at=:updated, patient_id=:patient, interviewer=:interviewer,
              referrer=:referrer, visit_date=:visit_date, status=:status,
              answers_json=:answers, summary_text=:summary, conclusions=:conclusions,
              duration_ms=:duration, feedback_summary_fit=:fit,
              feedback_completeness=:completeness, feedback_flow=:flow,
              feedback_unclear=:unclear, feedback_missing=:missing,
              feedback_notes=:notes
            WHERE id=:id
        """), {
            "updated": now_iso(), "patient": val("A0a"), "interviewer": val("A0b"),
            "referrer": val("A0c"), "visit_date": val("A0d"),
            "status": x.get("status", "in_progress"),
            "answers": json.dumps(a, ensure_ascii=False),
            "summary": x.get("summary", ""), "conclusions": x.get("conclusions", ""),
            "duration": int(x.get("duration_ms") or 0),
            "fit": f.get("summaryFit", ""), "completeness": f.get("completeness", ""),
            "flow": f.get("flow", ""), "unclear": f.get("unclear", ""),
            "missing": f.get("missing", ""), "notes": f.get("notes", ""), "id": iid
        })
    return jsonify({"ok": True, "id": iid})

@app.get("/api/interviews")
@require_admin
def list_interviews():
    with engine.begin() as c:
        rows = c.execute(text("""
            SELECT id,created_at,updated_at,patient_id,interviewer,visit_date,status,duration_ms,
                   feedback_summary_fit,feedback_completeness,feedback_flow
            FROM interviews ORDER BY created_at DESC
        """)).mappings().all()
    return jsonify([dict(r) for r in rows])

@app.get("/api/stats")
@require_admin
def stats():
    with engine.begin() as c:
        total = c.execute(text("SELECT COUNT(*) AS n FROM interviews")).mappings().first()["n"]
        completed = c.execute(text("SELECT COUNT(*) AS n FROM interviews WHERE status='completed'")).mappings().first()["n"]
        avg_ms = c.execute(text("SELECT AVG(duration_ms) AS v FROM interviews WHERE duration_ms>0")).mappings().first()["v"] or 0
        fit = c.execute(text("""
            SELECT feedback_summary_fit AS k, COUNT(*) AS n
            FROM interviews WHERE feedback_summary_fit <> ''
            GROUP BY feedback_summary_fit
        """)).mappings().all()
    return jsonify({
        "total": int(total or 0), "completed": int(completed or 0),
        "avg_duration_ms": int(avg_ms or 0),
        "summary_fit": {r["k"]: int(r["n"]) for r in fit}
    })

@app.get("/api/export.csv")
@require_admin
def export_csv():
    with engine.begin() as c:
        rows = c.execute(text("SELECT * FROM interviews ORDER BY created_at")).mappings().all()
    fields = [
        "id","created_at","updated_at","patient_id","interviewer","referrer","visit_date","status",
        "duration_ms","feedback_summary_fit","feedback_completeness","feedback_flow",
        "feedback_unclear","feedback_missing","feedback_notes","summary_text","conclusions"
    ]
    s = io.StringIO()
    w = csv.writer(s)
    w.writerow(fields)
    for r in rows:
        w.writerow([r.get(k, "") for k in fields])
    return Response(
        s.getvalue(), mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=adhd_pilot_export.csv"}
    )

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
