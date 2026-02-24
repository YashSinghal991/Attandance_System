from __future__ import annotations

import io
import os
import secrets
import sqlite3
from datetime import datetime
from pathlib import Path

import qrcode
from flask import Flask, g, jsonify, redirect, render_template, request, send_file, url_for

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "attendance.db"

app = Flask(__name__)


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_: object) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db() -> None:
    db = sqlite3.connect(DB_PATH)
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token TEXT UNIQUE NOT NULL,
            class_name TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            student_id TEXT NOT NULL,
            student_name TEXT NOT NULL,
            marked_at TEXT NOT NULL,
            UNIQUE(session_id, student_id),
            FOREIGN KEY(session_id) REFERENCES sessions(id)
        )
        """
    )
    db.commit()
    db.close()


@app.route("/")
def home() -> str:
    db = get_db()
    sessions = db.execute(
        "SELECT id, token, class_name, created_at FROM sessions ORDER BY id DESC"
    ).fetchall()
    return render_template("home.html", sessions=sessions)


@app.route("/create-session", methods=["POST"])
def create_session():
    class_name = request.form.get("class_name", "").strip()
    if not class_name:
        return redirect(url_for("home"))

    token = secrets.token_urlsafe(10)
    created_at = datetime.utcnow().isoformat(timespec="seconds")

    db = get_db()
    db.execute(
        "INSERT INTO sessions(token, class_name, created_at) VALUES (?, ?, ?)",
        (token, class_name, created_at),
    )
    db.commit()
    return redirect(url_for("session_detail", token=token))


@app.route("/session/<token>")
def session_detail(token: str):
    db = get_db()
    session = db.execute(
        "SELECT id, token, class_name, created_at FROM sessions WHERE token = ?", (token,)
    ).fetchone()
    if not session:
        return "Session not found", 404

    attendance = db.execute(
        """
        SELECT student_id, student_name, marked_at
        FROM attendance
        WHERE session_id = ?
        ORDER BY marked_at DESC
        """,
        (session["id"],),
    ).fetchall()

    attendance_link = request.host_url.rstrip("/") + url_for("scan_page", token=token)
    return render_template(
        "session.html", session=session, attendance=attendance, attendance_link=attendance_link
    )


@app.route("/qr/<token>.png")
def qr_code(token: str):
    data = request.host_url.rstrip("/") + url_for("scan_page", token=token)
    img = qrcode.make(data)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png")


@app.route("/scan/<token>")
def scan_page(token: str):
    db = get_db()
    session = db.execute("SELECT id, class_name FROM sessions WHERE token = ?", (token,)).fetchone()
    if not session:
        return "Session not found", 404
    return render_template("scan.html", token=token, class_name=session["class_name"])


@app.route("/api/mark-attendance", methods=["POST"])
def mark_attendance():
    payload = request.get_json(silent=True) or {}
    token = (payload.get("token") or "").strip()
    student_id = (payload.get("student_id") or "").strip()
    student_name = (payload.get("student_name") or "").strip()

    if not token or not student_id or not student_name:
        return jsonify({"ok": False, "message": "token, student_id and student_name are required"}), 400

    db = get_db()
    session = db.execute("SELECT id FROM sessions WHERE token = ?", (token,)).fetchone()
    if not session:
        return jsonify({"ok": False, "message": "Invalid session token"}), 404

    try:
        db.execute(
            "INSERT INTO attendance(session_id, student_id, student_name, marked_at) VALUES (?, ?, ?, ?)",
            (
                session["id"],
                student_id,
                student_name,
                datetime.utcnow().isoformat(timespec="seconds"),
            ),
        )
        db.commit()
    except sqlite3.IntegrityError:
        return jsonify({"ok": False, "message": "Attendance already marked for this student"}), 409

    return jsonify({"ok": True, "message": "Attendance marked successfully"})


if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)
