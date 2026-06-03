from __future__ import annotations

import io
import os
import re
import smtplib
import sqlite3
import uuid
from datetime import datetime, timezone
from email.message import EmailMessage
from functools import wraps
from pathlib import Path
from urllib.parse import urlparse

import qrcode
from PIL import Image, ImageDraw, ImageFont
from flask import (
    Flask,
    current_app,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)


TOKEN_RE = re.compile(r"^[a-f0-9]{32}$")
MANUAL_CODE_RE = re.compile(r"^[A-Z0-9]{8}$")


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-change-me"),
        DATABASE=str(Path(app.instance_path) / "convegno.sqlite"),
        ADMIN_PASSWORD=os.environ.get("ADMIN_PASSWORD", "admin"),
        EVENT_NAME=os.environ.get("EVENT_NAME", "Convegno"),
        SMTP_HOST=os.environ.get("SMTP_HOST", ""),
        SMTP_PORT=int(os.environ.get("SMTP_PORT", "587")),
        SMTP_USERNAME=os.environ.get("SMTP_USERNAME", ""),
        SMTP_PASSWORD=os.environ.get("SMTP_PASSWORD", ""),
        SMTP_FROM=os.environ.get("SMTP_FROM", "noreply@convegno.local"),
        SMTP_USE_TLS=os.environ.get("SMTP_USE_TLS", "1") == "1",
    )

    if test_config:
        app.config.update(test_config)

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)

    @app.context_processor
    def inject_event_name() -> dict:
        return {
            "event_name": app.config["EVENT_NAME"],
            "format_manual_code": format_manual_code,
        }

    @app.teardown_appcontext
    def close_db(error: Exception | None = None) -> None:
        db = g.pop("db", None)
        if db is not None:
            db.close()

    @app.get("/")
    def index():
        events = get_events_with_stats(public_only=True)
        return render_template("event_list.html", events=events)

    @app.route("/events/<int:event_id>/register", methods=["GET", "POST"])
    def register_event(event_id: int):
        event = get_event_with_stats(event_id)
        if event is None:
            return render_template("not_found.html"), 404
        if not event["is_open"]:
            flash("Le iscrizioni per questo evento sono chiuse.", "error")
            return redirect(url_for("index"))

        if request.method == "GET":
            return render_template("register.html", event=event)

        return handle_registration(event)

    @app.post("/register")
    def register():
        event_id = request.form.get("event_id", "").strip()
        if not event_id.isdigit():
            flash("Seleziona un evento valido.", "error")
            return redirect(url_for("index"))

        event = get_event_with_stats(int(event_id))
        if event is None:
            return render_template("not_found.html"), 404
        if not event["is_open"]:
            flash("Le iscrizioni per questo evento sono chiuse.", "error")
            return redirect(url_for("index"))

        return handle_registration(event)

    def handle_registration(event: sqlite3.Row):
        first_name = request.form.get("first_name", "").strip()
        last_name = request.form.get("last_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone = request.form.get("phone", "").strip()
        organization = request.form.get("organization", "").strip()
        accessible_required = 1 if request.form.get("accessible_required") == "on" else 0

        if not first_name or not last_name or not email:
            flash("Inserisci nome, cognome ed email.", "error")
            return render_template("register.html", event=event, form=request.form), 400

        if "@" not in email or "." not in email:
            flash("Inserisci un indirizzo email valido.", "error")
            return render_template("register.html", event=event, form=request.form), 400

        if event["remaining_seats"] <= 0:
            flash("I posti disponibili per questo evento sono esauriti.", "error")
            return render_template("register.html", event=event, form=request.form), 409

        if accessible_required and event["remaining_accessible_seats"] <= 0:
            flash("I posti accessibili per questo evento sono esauriti.", "error")
            return render_template("register.html", event=event, form=request.form), 409

        token = uuid.uuid4().hex
        manual_code = generate_unique_manual_code()
        db = get_db()
        try:
            db.execute(
                """
                INSERT INTO participants
                    (event_id, first_name, last_name, email, phone, organization,
                     accessible_required, token, manual_code, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event["id"],
                    first_name,
                    last_name,
                    email,
                    phone,
                    organization,
                    accessible_required,
                    token,
                    manual_code,
                    now_iso(),
                ),
            )
            db.commit()
        except sqlite3.IntegrityError:
            flash("Questa email risulta gia iscritta a questo evento.", "error")
            return render_template("register.html", event=event, form=request.form), 409

        participant = get_participant_by_token(token)
        if send_confirmation_email(participant):
            flash("Iscrizione completata. Abbiamo inviato il biglietto via email.", "success")

        return redirect(url_for("success", token=token))

    @app.get("/success/<token>")
    def success(token: str):
        participant = get_participant_by_token(token)
        if participant is None:
            return render_template("not_found.html"), 404
        return render_template("success.html", participant=participant)

    @app.get("/ticket/<token>")
    def ticket(token: str):
        participant = get_participant_by_token(token)
        if participant is None:
            return render_template("not_found.html"), 404
        output = create_ticket_image(participant)
        filename = f"biglietto-{participant['last_name']}-{participant['manual_code']}.png"
        return send_file(
            output,
            mimetype="image/png",
            as_attachment=True,
            download_name=filename,
        )

    @app.get("/qr/<token>.png")
    def qr_image(token: str):
        participant = get_participant_by_token(token)
        if participant is None:
            return render_template("not_found.html"), 404

        img = qrcode.make(url_for("checkin", token=token, _external=True))
        output = io.BytesIO()
        img.save(output, format="PNG")
        output.seek(0)
        return send_file(output, mimetype="image/png")

    @app.route("/admin", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            password = request.form.get("password", "")
            if password == current_app.config["ADMIN_PASSWORD"]:
                session["admin"] = True
                return redirect(request.args.get("next") or url_for("participants"))
            flash("Password non corretta.", "error")
        return render_template("login.html")

    @app.get("/logout")
    def logout():
        session.clear()
        return redirect(url_for("index"))

    @app.get("/staff/events")
    @admin_required
    def staff_events():
        events = get_events_with_stats(public_only=False)
        return render_template("events.html", events=events)

    @app.route("/staff/events/new", methods=["GET", "POST"])
    @admin_required
    def new_event():
        if request.method == "GET":
            return render_template("event_form.html", event=None)
        return save_event()

    @app.route("/staff/events/<int:event_id>/edit", methods=["GET", "POST"])
    @admin_required
    def edit_event(event_id: int):
        event = get_event(event_id)
        if event is None:
            return render_template("not_found.html"), 404
        if request.method == "GET":
            return render_template("event_form.html", event=event)
        return save_event(event_id)

    @app.get("/participants")
    @admin_required
    def participants():
        q = request.args.get("q", "").strip()
        selected_event_id = request.args.get("event_id", "all")
        events = get_events_with_stats(public_only=False)
        db = get_db()
        where = []
        params = []

        if selected_event_id != "all" and selected_event_id.isdigit():
            where.append("p.event_id = ?")
            params.append(int(selected_event_id))

        if q:
            like = f"%{q.lower()}%"
            where.append(
                """
                (lower(p.first_name) LIKE ?
                 OR lower(p.last_name) LIKE ?
                 OR lower(p.email) LIKE ?
                 OR lower(p.organization) LIKE ?
                 OR lower(p.manual_code) LIKE ?
                 OR lower(e.name) LIKE ?)
                """
            )
            params.extend([like, like, like, like, like, like])

        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        rows = db.execute(
            f"""
            SELECT p.*, e.name AS event_name
            FROM participants p
            JOIN events e ON e.id = p.event_id
            {where_sql}
            ORDER BY p.created_at DESC
            """,
            params,
        ).fetchall()

        stats = db.execute(
            f"""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN p.checked_in_at IS NOT NULL THEN 1 ELSE 0 END) AS checked,
                SUM(CASE WHEN p.accessible_required = 1 THEN 1 ELSE 0 END) AS accessible
            FROM participants p
            JOIN events e ON e.id = p.event_id
            {where_sql}
            """,
            params,
        ).fetchone()
        return render_template(
            "participants.html",
            participants=rows,
            stats=stats,
            q=q,
            events=events,
            selected_event_id=selected_event_id,
        )

    @app.post("/participants/<int:participant_id>/delete")
    @admin_required
    def delete_participant(participant_id: int):
        db = get_db()
        participant = db.execute(
            "SELECT * FROM participants WHERE id = ?", (participant_id,)
        ).fetchone()
        event_id = request.args.get("event_id", "all")
        q = request.args.get("q", "")
        if participant is None:
            flash("Iscritto non trovato.", "error")
            return redirect(url_for("participants", event_id=event_id, q=q))

        db.execute("DELETE FROM participants WHERE id = ?", (participant_id,))
        db.commit()
        flash(
            f"Iscritto eliminato: {participant['first_name']} {participant['last_name']}.",
            "success",
        )
        return redirect(url_for("participants", event_id=event_id, q=q))

    def save_event(event_id: int | None = None):
        name = request.form.get("name", "").strip()
        event_date = request.form.get("event_date", "").strip()
        venue = request.form.get("venue", "").strip()
        capacity_raw = request.form.get("capacity", "").strip()
        accessible_capacity_raw = request.form.get("accessible_capacity", "0").strip()
        is_open = 1 if request.form.get("is_open") == "on" else 0

        try:
            capacity = int(capacity_raw)
            accessible_capacity = int(accessible_capacity_raw or "0")
        except ValueError:
            flash("Inserisci numeri validi per le capienze.", "error")
            return render_template("event_form.html", event=request.form), 400

        if not name or capacity < 0 or accessible_capacity < 0:
            flash("Nome e capienze sono obbligatori.", "error")
            return render_template("event_form.html", event=request.form), 400

        if accessible_capacity > capacity:
            flash("I posti accessibili non possono superare la capienza totale.", "error")
            return render_template("event_form.html", event=request.form), 400

        db = get_db()
        if event_id is None:
            db.execute(
                """
                INSERT INTO events
                    (name, event_date, venue, capacity, accessible_capacity, is_open, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    event_date,
                    venue,
                    capacity,
                    accessible_capacity,
                    is_open,
                    now_iso(),
                ),
            )
            flash("Evento creato.", "success")
        else:
            db.execute(
                """
                UPDATE events
                SET name = ?, event_date = ?, venue = ?, capacity = ?,
                    accessible_capacity = ?, is_open = ?
                WHERE id = ?
                """,
                (
                    name,
                    event_date,
                    venue,
                    capacity,
                    accessible_capacity,
                    is_open,
                    event_id,
                ),
            )
            flash("Evento aggiornato.", "success")

        db.commit()
        return redirect(url_for("staff_events"))

    @app.get("/scan")
    @admin_required
    def scan():
        return render_template("scan.html")

    @app.post("/api/checkin")
    @admin_required
    def api_checkin():
        data = request.get_json(silent=True) or request.form
        code = normalize_code(data.get("code", ""))
        status, participant = checkin_code(code)

        payload = {"status": status}
        if participant:
            payload["participant"] = dict(participant)
        return jsonify(payload), 200 if status != "invalid" else 404

    @app.get("/checkin/<token>")
    @admin_required
    def checkin(token: str):
        status, participant = checkin_code(normalize_code(token))
        return render_template(
            "checkin_result.html", status=status, participant=participant
        ), 200 if status != "invalid" else 404

    with app.app_context():
        init_db()

    return app


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
    return g.db


def init_db() -> None:
    db = get_db()
    schema = Path(current_app.root_path) / "schema.sql"
    db.executescript(schema.read_text(encoding="utf-8"))
    migrate_db(db)
    db.commit()


def migrate_db(db: sqlite3.Connection) -> None:
    default_event_id = ensure_default_event(db)
    columns = {
        row["name"]
        for row in db.execute("PRAGMA table_info(participants)").fetchall()
    }

    table_sql = db.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'participants'"
    ).fetchone()["sql"]
    needs_rebuild = (
        "event_id" not in columns
        or "accessible_required" not in columns
        or "email TEXT NOT NULL UNIQUE" in table_sql
    )

    if needs_rebuild:
        rebuild_participants_table(db, columns, default_event_id)
        columns = {
            row["name"]
            for row in db.execute("PRAGMA table_info(participants)").fetchall()
        }

    if "manual_code" not in columns:
        db.execute("ALTER TABLE participants ADD COLUMN manual_code TEXT")

    rows_without_code = db.execute(
        "SELECT id FROM participants WHERE manual_code IS NULL OR manual_code = ''"
    ).fetchall()
    for row in rows_without_code:
        db.execute(
            "UPDATE participants SET manual_code = ? WHERE id = ?",
            (generate_unique_manual_code(), row["id"]),
        )

    db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_participants_manual_code ON participants(manual_code)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_participants_event_id ON participants(event_id)"
    )
    db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_participants_event_email ON participants(event_id, email)"
    )


def ensure_default_event(db: sqlite3.Connection) -> int:
    event = db.execute("SELECT id FROM events ORDER BY id LIMIT 1").fetchone()
    if event is not None:
        return event["id"]

    cursor = db.execute(
        """
        INSERT INTO events
            (name, event_date, venue, capacity, accessible_capacity, is_open, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            current_app.config["EVENT_NAME"],
            "",
            "",
            100,
            0,
            1,
            now_iso(),
        ),
    )
    return cursor.lastrowid


def rebuild_participants_table(
    db: sqlite3.Connection, columns: set[str], default_event_id: int
) -> None:
    db.execute("DROP TABLE IF EXISTS participants_new")
    db.execute(
        """
        CREATE TABLE participants_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            email TEXT NOT NULL,
            phone TEXT,
            organization TEXT,
            accessible_required INTEGER NOT NULL DEFAULT 0,
            token TEXT NOT NULL UNIQUE,
            manual_code TEXT UNIQUE,
            created_at TEXT NOT NULL,
            checked_in_at TEXT,
            FOREIGN KEY (event_id) REFERENCES events(id)
        )
        """
    )

    event_expr = "event_id" if "event_id" in columns else str(default_event_id)
    accessible_expr = (
        "accessible_required" if "accessible_required" in columns else "0"
    )
    manual_expr = "manual_code" if "manual_code" in columns else "NULL"

    db.execute(
        f"""
        INSERT INTO participants_new
            (id, event_id, first_name, last_name, email, phone, organization,
             accessible_required, token, manual_code, created_at, checked_in_at)
        SELECT
            id, {event_expr}, first_name, last_name, email, phone, organization,
            {accessible_expr}, token, {manual_expr}, created_at, checked_in_at
        FROM participants
        """
    )
    db.execute("DROP TABLE participants")
    db.execute("ALTER TABLE participants_new RENAME TO participants")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_event(event_id: int) -> sqlite3.Row | None:
    return get_db().execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()


def get_events_with_stats(public_only: bool = False) -> list[sqlite3.Row]:
    where_sql = "WHERE e.is_open = 1" if public_only else ""
    return get_db().execute(
        f"""
        SELECT
            e.*,
            COUNT(p.id) AS registered_count,
            SUM(CASE WHEN p.accessible_required = 1 THEN 1 ELSE 0 END) AS accessible_count,
            e.capacity - COUNT(p.id) AS remaining_seats,
            e.accessible_capacity
                - SUM(CASE WHEN p.accessible_required = 1 THEN 1 ELSE 0 END)
                AS remaining_accessible_seats
        FROM events e
        LEFT JOIN participants p ON p.event_id = e.id
        {where_sql}
        GROUP BY e.id
        ORDER BY e.event_date = '', e.event_date, e.name
        """
    ).fetchall()


def get_event_with_stats(event_id: int) -> sqlite3.Row | None:
    return get_db().execute(
        """
        SELECT
            e.*,
            COUNT(p.id) AS registered_count,
            SUM(CASE WHEN p.accessible_required = 1 THEN 1 ELSE 0 END) AS accessible_count,
            e.capacity - COUNT(p.id) AS remaining_seats,
            e.accessible_capacity
                - SUM(CASE WHEN p.accessible_required = 1 THEN 1 ELSE 0 END)
                AS remaining_accessible_seats
        FROM events e
        LEFT JOIN participants p ON p.event_id = e.id
        WHERE e.id = ?
        GROUP BY e.id
        """,
        (event_id,),
    ).fetchone()


def get_participant_by_token(token: str) -> sqlite3.Row | None:
    if not TOKEN_RE.match(token):
        return None
    return get_db().execute(
        """
        SELECT p.*, e.name AS event_name, e.event_date, e.venue
        FROM participants p
        JOIN events e ON e.id = p.event_id
        WHERE p.token = ?
        """,
        (token,),
    ).fetchone()


def get_participant_by_manual_code(code: str) -> sqlite3.Row | None:
    clean_code = normalize_manual_code(code)
    if not MANUAL_CODE_RE.match(clean_code):
        return None
    return get_db().execute(
        """
        SELECT p.*, e.name AS event_name, e.event_date, e.venue
        FROM participants p
        JOIN events e ON e.id = p.event_id
        WHERE p.manual_code = ?
        """,
        (clean_code,),
    ).fetchone()


def generate_unique_manual_code() -> str:
    db = get_db()
    while True:
        code = uuid.uuid4().hex[:8].upper()
        exists = db.execute(
            "SELECT 1 FROM participants WHERE manual_code = ?", (code,)
        ).fetchone()
        if exists is None:
            return code


def normalize_manual_code(code: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", code).upper()


def format_manual_code(code: str) -> str:
    clean_code = normalize_manual_code(code)
    if len(clean_code) == 8:
        return f"{clean_code[:4]}-{clean_code[4:]}"
    return clean_code


def normalize_code(raw_code: str) -> str:
    code = raw_code.strip()
    if not code:
        return ""

    parsed = urlparse(code)
    if parsed.path:
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 2 and parts[-2] == "checkin":
            code = parts[-1]
        elif parts:
            code = parts[-1]

    return code.strip()


def checkin_code(code: str) -> tuple[str, sqlite3.Row | None]:
    normalized = code.strip()
    if TOKEN_RE.match(normalized.lower()):
        return checkin_participant(normalized.lower())

    manual_code = normalize_manual_code(normalized)
    if MANUAL_CODE_RE.match(manual_code):
        participant = get_participant_by_manual_code(manual_code)
        if participant is None:
            return "invalid", None
        return checkin_participant(participant["token"])

    return "invalid", None


def checkin_participant(token: str) -> tuple[str, sqlite3.Row | None]:
    participant = get_participant_by_token(token)
    if participant is None:
        return "invalid", None

    if participant["checked_in_at"]:
        return "already_checked", participant

    get_db().execute(
        "UPDATE participants SET checked_in_at = ? WHERE token = ?",
        (now_iso(), token),
    )
    get_db().commit()
    participant = get_participant_by_token(token)
    return "checked", participant


def create_ticket_image(participant: sqlite3.Row) -> io.BytesIO:
    width, height = 900, 520
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)

    title_font = load_font(44, bold=True)
    name_font = load_font(34, bold=True)
    text_font = load_font(24)
    code_font = load_font(38, bold=True)

    draw.rectangle((0, 0, width, 84), fill="#176b87")
    draw.text((40, 24), participant["event_name"], fill="white", font=title_font)

    draw.text((40, 128), "Biglietto personale", fill="#657184", font=text_font)
    draw.text(
        (40, 172),
        f"{participant['first_name']} {participant['last_name']}",
        fill="#1d2430",
        font=name_font,
    )
    draw.text((40, 224), participant["email"], fill="#394454", font=text_font)
    if participant["organization"]:
        draw.text((40, 264), participant["organization"], fill="#394454", font=text_font)
    if participant["venue"]:
        draw.text((40, 304), participant["venue"], fill="#394454", font=text_font)
    if participant["accessible_required"]:
        draw.text((40, 334), "Posto accessibile richiesto", fill="#137a45", font=text_font)

    manual_code = format_manual_code(participant["manual_code"])
    draw.text((40, 360), "Codice manuale ingresso", fill="#657184", font=text_font)
    draw.rounded_rectangle((40, 390, 330, 460), radius=12, outline="#c7ceda", width=2)
    draw.text((68, 405), manual_code, fill="#1d2430", font=code_font)

    qr = qrcode.make(url_for("checkin", token=participant["token"], _external=True))
    qr = qr.convert("RGB").resize((300, 300))
    image.paste(qr, (600, 150))
    draw.text((615, 460), "QR ingresso", fill="#657184", font=text_font)

    output = io.BytesIO()
    image.save(output, format="PNG")
    output.seek(0)
    return output


def send_confirmation_email(participant: sqlite3.Row | None) -> bool:
    if participant is None or not current_app.config["SMTP_HOST"]:
        return False

    ticket = create_ticket_image(participant)
    details = [
        f"Ciao {participant['first_name']},",
        "",
        f"la tua iscrizione a {participant['event_name']} e confermata.",
    ]
    if participant["venue"]:
        details.append(f"Luogo: {participant['venue']}")
    if participant["event_date"]:
        details.append(f"Data: {participant['event_date']}")
    if participant["accessible_required"]:
        details.append("Posto accessibile richiesto: si")
    details.extend(
        [
            f"Codice manuale: {format_manual_code(participant['manual_code'])}",
            "",
            "In allegato trovi il biglietto con il QR da mostrare all'ingresso.",
        ]
    )

    msg = EmailMessage()
    msg["Subject"] = f"Conferma iscrizione - {participant['event_name']}"
    msg["From"] = current_app.config["SMTP_FROM"]
    msg["To"] = participant["email"]
    msg.set_content("\n".join(details))
    msg.add_attachment(
        ticket.getvalue(),
        maintype="image",
        subtype="png",
        filename=f"biglietto-{participant['last_name']}-{participant['manual_code']}.png",
    )

    try:
        with smtplib.SMTP(
            current_app.config["SMTP_HOST"], current_app.config["SMTP_PORT"]
        ) as smtp:
            if current_app.config["SMTP_USE_TLS"]:
                smtp.starttls()
            if current_app.config["SMTP_USERNAME"]:
                smtp.login(
                    current_app.config["SMTP_USERNAME"],
                    current_app.config["SMTP_PASSWORD"],
                )
            smtp.send_message(msg)
        return True
    except (OSError, smtplib.SMTPException):
        current_app.logger.exception("Invio email di conferma non riuscito")
        return False


def load_font(size: int, bold: bool = False):
    candidates = [
        "arialbd.ttf" if bold else "arial.ttf",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
    ]
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


def admin_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapped_view


app = create_app()


if __name__ == "__main__":
    app.run(debug=True)
