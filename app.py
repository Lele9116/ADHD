import os, json, uuid, csv, io, hmac
from datetime import datetime, timezone
from functools import wraps

from flask import Flask, request, jsonify, render_template, Response, redirect, url_for, session
from sqlalchemy import create_engine, text
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
from xml.sax.saxutils import escape

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

ITEM_LABELS = {"A0a": {"section": "Motivo della valutazione", "question": "Identificativo del paziente / iniziali / codice interno.", "type": "text"}, "A0b": {"section": "Motivo della valutazione", "question": "Nome dell’intervistatore.", "type": "text"}, "A0c": {"section": "Motivo della valutazione", "question": "Professionista inviante, se presente.", "type": "text"}, "A0d": {"section": "Motivo della valutazione", "question": "Data del colloquio.", "type": "text"}, "A1": {"section": "Motivo della valutazione", "question": "Quali difficoltà l'hanno portata a fare questa valutazione?", "type": "text"}, "A2": {"section": "Motivo della valutazione", "question": "In quali ambiti della sua vita queste difficoltà hanno un impatto maggiore?", "type": "text"}, "A3": {"section": "Motivo della valutazione", "question": "Chi ha suggerito per primo la possibilità di ADHD?", "type": "text"}, "B1": {"section": "Storia evolutiva", "question": "Da bambino/a le dicevano che era distratto/a, con la testa tra le nuvole o che faceva fatica a concentrarsi?", "type": "ynv"}, "B2": {"section": "Storia evolutiva", "question": "Da bambino/a dimenticava o perdeva spesso materiale scolastico, compiti o altri oggetti necessari?", "type": "ynv"}, "B3": {"section": "Storia evolutiva", "question": "Riusciva a iniziare e portare a termine i compiti autonomamente, oppure aveva bisogno che un adulto la seguisse o la sollecitasse frequentemente?", "type": "ynv"}, "B4": {"section": "Storia evolutiva", "question": "Le dicevano che faceva fatica a stare fermo/a o seduto/a oppure che era particolarmente irrequieto/a?", "type": "ynv"}, "B5": {"section": "Storia evolutiva", "question": "Da bambino/a tendeva ad agire senza pensare abbastanza, con difficoltà ad aspettare o a fermarsi un attimo prima di fare qualcosa?", "type": "ynv"}, "B6": {"section": "Storia evolutiva", "question": "Quali fonti esterne sono disponibili per ricostruire il funzionamento nell'infanzia?", "type": "text"}, "B7": {"section": "Storia evolutiva", "question": "Valutazione complessiva dell'evidenza evolutiva.", "type": "evidence"}, "C1a": {"section": "Funzionamento", "question": "STUDIO — Quando doveva preparare un compito, un’interrogazione o un esame con anticipo, come si organizzava?", "type": "text"}, "C1b": {"section": "Funzionamento", "question": "STUDIO — Le capitava di rimandare fino a quando la scadenza diventava urgente, dimenticare consegne o studiare quasi tutto all’ultimo momento?", "type": "text"}, "C1c": {"section": "Funzionamento", "question": "STUDIO — Queste difficoltà hanno avuto conseguenze concrete sul rendimento o sul percorso scolastico o universitario?", "type": "text"}, "C1": {"section": "Funzionamento", "question": "Compromissione complessiva nello studio.", "type": "imp"}, "C2a": {"section": "Funzionamento", "question": "LAVORO — Come gestisce più attività contemporaneamente o lavori con una scadenza non immediata?", "type": "text"}, "C2b": {"section": "Funzionamento", "question": "LAVORO — Le capita di rimandare, dimenticare passaggi, commettere errori evitabili o lasciare attività incomplete?", "type": "text"}, "C2c": {"section": "Funzionamento", "question": "LAVORO — Queste difficoltà hanno avuto conseguenze concrete sul lavoro?", "type": "text"}, "C2": {"section": "Funzionamento", "question": "Compromissione complessiva nel lavoro.", "type": "imp"}, "C3a": {"section": "Funzionamento", "question": "VITA QUOTIDIANA — Come gestisce appuntamenti, pagamenti, documenti, faccende domestiche e altri impegni quotidiani?", "type": "text"}, "C3b": {"section": "Funzionamento", "question": "VITA QUOTIDIANA — Le persone vicine a lei le fanno notare dimenticanze, scarsa capacità d’ascolto, disorganizzazione o attività lasciate incomplete?", "type": "text"}, "C3c": {"section": "Funzionamento", "question": "VITA QUOTIDIANA — Queste difficoltà causano conflitti o altre conseguenze nella vita quotidiana o nelle relazioni?", "type": "text"}, "C3": {"section": "Funzionamento", "question": "Compromissione complessiva nella vita quotidiana e relazionale.", "type": "imp"}, "C4": {"section": "Funzionamento", "question": "Ha avuto problemi legali o amministrativi nei quali ritiene che impulsività, disattenzione o disorganizzazione possano aver avuto un ruolo?", "type": "text"}, "D1": {"section": "Attenzione", "question": "Ha difficoltà nel concentrarsi durante letture, conversazioni, riunioni o altre attività che richiedono attenzione per un certo tempo?", "type": "score"}, "D2": {"section": "Attenzione", "question": "Mentre sta facendo qualcosa, rumori, persone, notifiche o ciò che accade intorno le fanno perdere facilmente il filo di quello che sta facendo?", "type": "score"}, "D3": {"section": "Attenzione", "question": "Anche senza essere disturbato/a, le capita di iniziare a pensare ad altro e accorgersi di non aver più seguito quello che stava leggendo, facendo o ascoltando?", "type": "score"}, "D4": {"section": "Attenzione", "question": "Le capita di arrivare alla fine di una pagina o di una parte di una conversazione e accorgersi di non aver seguito quello che ha appena letto o ascoltato?", "type": "score"}, "D5": {"section": "Attenzione", "question": "Come cambia la sua capacità di concentrarsi quando deve fare qualcosa di ripetitivo, monotono o poco interessante?", "type": "score"}, "D6": {"section": "Attenzione", "question": "Le capita di fare errori di distrazione o di non accorgersi di dettagli importanti, anche in attività che sa svolgere bene?", "type": "score"}, "E1": {"section": "Iperattività/irrequietezza", "question": "Quando dovrebbe stare fermo/a o seduto/a a lungo, sente il bisogno di muoversi, cambiare posizione o manipolare qualcosa?", "type": "score"}, "E2": {"section": "Iperattività/irrequietezza", "question": "Anche quando riesce a stare fermo/a, sente dentro di sé irrequietezza o il bisogno di fare qualcosa?", "type": "score"}, "E3": {"section": "Iperattività/irrequietezza", "question": "Quanto le è difficile stare senza fare niente, rilassarsi o semplicemente rallentare?", "type": "score"}, "E4": {"section": "Iperattività/irrequietezza", "question": "Le capita di sentirsi come se dovesse essere sempre impegnato/a in qualcosa, passando rapidamente da un’attività all’altra?", "type": "score"}, "F1": {"section": "Funzioni esecutive", "question": "Quando ha molte cose da fare, quanto le è difficile organizzarle e decidere da dove iniziare?", "type": "score"}, "F2": {"section": "Funzioni esecutive", "question": "Le capita di sapere che dovrebbe iniziare qualcosa ma di continuare a rimandare, anche sapendo che questo potrebbe crearle problemi?", "type": "score"}, "F3": {"section": "Funzioni esecutive", "question": "Le capita di iniziare diverse attività e passare da una all’altra lasciandone alcune incomplete?", "type": "score"}, "F4": {"section": "Funzioni esecutive", "question": "Dimentica facilmente appuntamenti, scadenze o cose che aveva intenzione di fare se non utilizza promemoria?", "type": "score"}, "F5": {"section": "Funzioni esecutive", "question": "Quando deve tenere a mente più informazioni o più passaggi mentre fa qualcosa, le capita di perderne qualcuno per strada?", "type": "score"}, "F6": {"section": "Funzioni esecutive", "question": "Quanto riesce a stimare in anticipo il tempo necessario per fare qualcosa?", "type": "score"}, "F7": {"section": "Funzioni esecutive", "question": "Quando ha molto tempo a disposizione, tende a concentrare il lavoro soprattutto quando la scadenza diventa vicina?", "type": "score"}, "F8": {"section": "Funzioni esecutive", "question": "Nota una differenza importante tra quanto riesce a concentrarsi e attivarsi per qualcosa che la interessa o la coinvolge e quanto riesce a farlo per qualcosa che considera noioso o poco gratificante?", "type": "score"}, "F9": {"section": "Funzioni esecutive", "question": "Quando qualcosa la interessa molto, le capita di rimanerne assorbito/a al punto da perdere la percezione del tempo o trascurare quello che dovrebbe fare?", "type": "yesno"}, "F10": {"section": "Funzioni esecutive", "question": "Utilizza abitualmente strategie o sistemi per evitare dimenticanze, ritardi o problemi di organizzazione?", "type": "yesno"}, "F10a": {"section": "Funzioni esecutive", "question": "Quali strategie o sistemi utilizza?", "type": "text"}, "F10b": {"section": "Funzioni esecutive", "question": "Cosa succede quando non può utilizzare questi sistemi?", "type": "text"}, "G1": {"section": "Impulsività/autocontrollo", "question": "Le capita di agire d’impulso e rendersi conto solo dopo che sarebbe stato meglio fermarsi a riflettere?", "type": "score"}, "G2": {"section": "Impulsività/autocontrollo", "question": "Questo modo di agire le ha mai causato problemi o conseguenze significative?", "type": "yesno"}, "G3": {"section": "Impulsività/autocontrollo", "question": "Quando sente un impulso forte, quanto le è difficile fermarsi prima di agire?", "type": "score"}, "H1": {"section": "Regolazione emotiva", "question": "Le capita di reagire emotivamente in modo molto intenso a frustrazioni, inconvenienti o contrattempi?", "type": "score"}, "H2": {"section": "Regolazione emotiva", "question": "Quando si irrita o si arrabbia, le è difficile controllare la reazione o le serve molto tempo per tornare tranquillo/a?", "type": "score"}, "H3": {"section": "Regolazione emotiva", "question": "Queste reazioni le hanno causato problemi nelle relazioni, nello studio, nel lavoro o in altri ambiti?", "type": "yesno"}, "I1": {"section": "Pregresse valutazioni e trattamenti", "question": "Uno psicologo o uno psichiatra le ha mai formulato una diagnosi o ipotizzato la presenza di un disturbo psicologico o psichiatrico?", "type": "yesno"}, "I1a": {"section": "Pregresse valutazioni e trattamenti", "question": "Quale diagnosi o ipotesi è stata formulata?", "type": "text"}, "I1b": {"section": "Pregresse valutazioni e trattamenti", "question": "Da quale professionista?", "type": "text"}, "I1c": {"section": "Pregresse valutazioni e trattamenti", "question": "In quale periodo?", "type": "text"}, "I2": {"section": "Pregresse valutazioni e trattamenti", "question": "Ha effettuato trattamenti psicologici o psichiatrici in relazione a queste difficoltà?", "type": "yesno"}, "I2a": {"section": "Pregresse valutazioni e trattamenti", "question": "Quali trattamenti?", "type": "text"}, "I3": {"section": "Pregresse valutazioni e trattamenti", "question": "Ha assunto o assume farmaci per queste difficoltà?", "type": "yesno"}, "I3a": {"section": "Pregresse valutazioni e trattamenti", "question": "Quali farmaci?", "type": "text"}}

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

def serialize_row(row):
    d = dict(row)
    try:
        d["answers"] = json.loads(d.get("answers_json") or "{}")
    except Exception:
        d["answers"] = {}
    return d

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

@app.get("/dashboard/interview/<iid>")
@require_admin
def interview_detail(iid):
    with engine.begin() as c:
        r = c.execute(text("SELECT * FROM interviews WHERE id=:id"), {"id": iid}).mappings().first()
    if not r:
        return "Colloquio non trovato", 404
    d = serialize_row(r)

    grouped = {}
    for code, entry in (d["answers"] or {}).items():
        if code.startswith("__"):
            continue
        meta = ITEM_LABELS.get(code, {"section":"Altro","question":code,"type":""})
        value = entry.get("value","") if isinstance(entry, dict) else entry
        note = entry.get("note","") if isinstance(entry, dict) else ""
        if value in ("", None) and not note:
            continue
        grouped.setdefault(meta["section"], []).append({
            "code": code,
            "question": meta["question"],
            "value": value,
            "note": note
        })

    return render_template("detail.html", interview=d, grouped=grouped)


@app.get("/api/interviews/<iid>/export.pdf")
@require_admin
def export_interview_pdf(iid):
    with engine.begin() as c:
        r = c.execute(text("SELECT * FROM interviews WHERE id=:id"), {"id": iid}).mappings().first()
    if not r:
        return "Colloquio non trovato", 404

    d = serialize_row(r)

    grouped = {}
    for code, entry in (d["answers"] or {}).items():
        if code.startswith("__"):
            continue
        meta = ITEM_LABELS.get(code, {"section":"Altro","question":code,"type":""})
        value = entry.get("value","") if isinstance(entry, dict) else entry
        note = entry.get("note","") if isinstance(entry, dict) else ""
        if value in ("", None) and not note:
            continue
        grouped.setdefault(meta["section"], []).append({
            "code": code,
            "question": meta["question"],
            "value": value,
            "note": note
        })

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        rightMargin=16*mm, leftMargin=16*mm,
        topMargin=16*mm, bottomMargin=16*mm,
        title="Colloquio ADHD"
    )

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name="SmallMuted", parent=styles["Normal"],
        fontSize=8.5, leading=11, textColor=colors.HexColor("#6b7280")
    ))
    styles.add(ParagraphStyle(
        name="SectionTitle", parent=styles["Heading2"],
        fontSize=14, leading=17, spaceBefore=10, spaceAfter=7
    ))
    styles.add(ParagraphStyle(
        name="Question", parent=styles["Normal"],
        fontSize=9.5, leading=12, spaceAfter=3
    ))
    styles.add(ParagraphStyle(
        name="BodyPDF", parent=styles["Normal"],
        fontSize=9.5, leading=13
    ))

    story = []
    story.append(Paragraph("Report colloquio ADHD", styles["Title"]))
    story.append(Paragraph("Valutazione clinica preliminare strutturata", styles["SmallMuted"]))
    story.append(Spacer(1, 6*mm))

    meta_rows = [
        ["ID paziente", d.get("patient_id") or "—"],
        ["Intervistatore", d.get("interviewer") or "—"],
        ["Professionista inviante", d.get("referrer") or "—"],
        ["Data colloquio", d.get("visit_date") or "—"],
        ["Stato", d.get("status") or "—"],
        ["Durata registrata", f'{round((d.get("duration_ms") or 0)/60000)} min'],
    ]
    mt = Table(meta_rows, colWidths=[45*mm, 125*mm])
    mt.setStyle(TableStyle([
        ("FONTNAME",(0,0),(-1,-1),"Helvetica"),
        ("FONTSIZE",(0,0),(-1,-1),9),
        ("TEXTCOLOR",(0,0),(0,-1),colors.HexColor("#6b7280")),
        ("VALIGN",(0,0),(-1,-1),"TOP"),
        ("BOTTOMPADDING",(0,0),(-1,-1),5),
        ("LINEBELOW",(0,-1),(-1,-1),0.5,colors.HexColor("#dddddd")),
    ]))
    story.append(mt)

    if d.get("summary_text"):
        story.append(Paragraph("Sintesi clinica automatica", styles["SectionTitle"]))
        story.append(Paragraph(escape(d["summary_text"]).replace("\n","<br/>"), styles["BodyPDF"]))

    if d.get("conclusions"):
        story.append(Paragraph("Conclusioni dell’intervistatore", styles["SectionTitle"]))
        story.append(Paragraph(escape(d["conclusions"]).replace("\n","<br/>"), styles["BodyPDF"]))

    story.append(Paragraph("Feedback dell’operatore", styles["SectionTitle"]))
    feedback_rows = [
        ["Aderenza sintesi", d.get("feedback_summary_fit") or "—"],
        ["Completezza", d.get("feedback_completeness") or "—"],
        ["Fluidità", d.get("feedback_flow") or "—"],
        ["Domande problematiche", d.get("feedback_unclear") or "—"],
        ["Informazioni mancanti", d.get("feedback_missing") or "—"],
        ["Note", d.get("feedback_notes") or "—"],
    ]
    ft = Table(feedback_rows, colWidths=[45*mm, 125*mm])
    ft.setStyle(TableStyle([
        ("FONTNAME",(0,0),(-1,-1),"Helvetica"),
        ("FONTSIZE",(0,0),(-1,-1),8.5),
        ("TEXTCOLOR",(0,0),(0,-1),colors.HexColor("#6b7280")),
        ("VALIGN",(0,0),(-1,-1),"TOP"),
        ("BOTTOMPADDING",(0,0),(-1,-1),5),
    ]))
    story.append(ft)

    story.append(PageBreak())
    story.append(Paragraph("Risposte e annotazioni raccolte", styles["SectionTitle"]))

    if grouped:
        for section, rows in grouped.items():
            story.append(Paragraph(escape(section), styles["Heading3"]))
            for row in rows:
                story.append(Paragraph(f'<b>{escape(str(row["code"]))}</b> · {escape(str(row["question"]))}', styles["Question"]))
                story.append(Paragraph(f'<b>Risposta:</b> {escape(str(row["value"]))}', styles["BodyPDF"]))
                if row["note"]:
                    story.append(Paragraph(f'<b>Annotazione/esempio:</b> {escape(str(row["note"]))}', styles["BodyPDF"]))
                story.append(Spacer(1, 3*mm))
    else:
        story.append(Paragraph("Nessuna risposta registrata.", styles["BodyPDF"]))

    story.append(Spacer(1, 6*mm))
    story.append(Paragraph(
        "Il presente documento riassume una valutazione clinica preliminare strutturata e non costituisce, isolatamente, diagnosi di ADHD.",
        styles["SmallMuted"]
    ))

    doc.build(story)
    pdf = buf.getvalue()
    buf.close()

    safe_id = (d.get("patient_id") or iid).replace("/", "_").replace("\\", "_").replace(" ", "_")
    return Response(
        pdf,
        mimetype="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="colloquio_ADHD_{safe_id}.pdf"'}
    )


@app.get("/api/interviews/<iid>/export.json")
@require_admin
def export_interview_json(iid):
    with engine.begin() as c:
        r = c.execute(text("SELECT * FROM interviews WHERE id=:id"), {"id": iid}).mappings().first()
    if not r:
        return jsonify({"error":"not_found"}), 404
    d = serialize_row(r)
    d.pop("answers_json", None)
    payload = json.dumps(d, ensure_ascii=False, indent=2)
    return Response(payload, mimetype="application/json; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename=colloquio_{iid}.json"})

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
    d = serialize_row(r)
    d.pop("answers_json", None)
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
    return Response(s.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=adhd_pilot_export.csv"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
