"""Small, dependency-free prototype for logging and reviewing unusual HF signals.

Run:  python signal_observatory.py
Open: http://127.0.0.1:8765
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import sqlite3
import struct
import wave
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "signals.db"
RECORDINGS = ROOT / "recordings"
HTML_PATH = ROOT / "signal_observatory.html"
RECORDINGS.mkdir(exist_ok=True)

# Tiny offline schedule sample. A real build would periodically import EiBi/HFCC
# datasets and retain source/licence/update metadata.
SCHEDULES = [
    {"frequency_khz": 6195, "station": "BBC World Service", "start": "00:00", "end": "24:00", "days": "daily", "mode": "AM", "source": "prototype"},
    {"frequency_khz": 9410, "station": "BBC World Service", "start": "00:00", "end": "24:00", "days": "daily", "mode": "AM", "source": "prototype"},
    {"frequency_khz": 10000, "station": "WWV time signal", "start": "00:00", "end": "24:00", "days": "daily", "mode": "AM", "source": "prototype"},
    {"frequency_khz": 14590, "station": "Example scheduled broadcaster", "start": "12:00", "end": "13:00", "days": "daily", "mode": "AM", "source": "prototype"},
]


def connect():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db


def init_db():
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          frequency_khz REAL NOT NULL,
          bandwidth_hz REAL DEFAULT 0,
          started_at TEXT NOT NULL,
          ended_at TEXT,
          receiver_name TEXT NOT NULL,
          receiver_url TEXT DEFAULT '',
          receiver_location TEXT DEFAULT '',
          mode TEXT DEFAULT 'UNKNOWN',
          snr_db REAL,
          decoded_text TEXT DEFAULT '',
          recording_path TEXT DEFAULT '',
          recording_sha256 TEXT DEFAULT '',
          fingerprint TEXT NOT NULL,
          anomaly_score INTEGER NOT NULL,
          classification TEXT NOT NULL,
          match_station TEXT DEFAULT '',
          match_source TEXT DEFAULT '',
          match_confidence REAL DEFAULT 0,
          anomaly_reasons TEXT NOT NULL,
          status TEXT DEFAULT 'new',
          created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS event_lookup
          ON events(frequency_khz, started_at, fingerprint);
        """)


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def active_at(schedule, iso_time):
    hhmm = datetime.fromisoformat(iso_time.replace("Z", "+00:00")).strftime("%H:%M")
    start, end = schedule["start"], schedule["end"]
    if end == "24:00":
        return hhmm >= start
    return start <= hhmm < end if start <= end else hhmm >= start or hhmm < end


def classify(payload):
    frequency = float(payload["frequency_khz"])
    mode = str(payload.get("mode", "UNKNOWN")).upper()
    started = payload.get("started_at") or utc_now()
    nearby = [s for s in SCHEDULES if abs(s["frequency_khz"] - frequency) <= 5 and active_at(s, started)]
    reasons, score, match, confidence = [], 0, None, 0
    if not nearby:
        score += 40
        reasons.append("No active schedule match within ±5 kHz")
    else:
        match = min(nearby, key=lambda s: abs(s["frequency_khz"] - frequency))
        delta = abs(match["frequency_khz"] - frequency)
        confidence = max(0.35, 0.95 - delta / 10)
        score -= 40
        reasons.append(f"Schedule candidate: {match['station']} ({delta:.1f} kHz offset)")
        if mode not in ("UNKNOWN", match["mode"]):
            score += 20
            confidence -= 0.2
            reasons.append(f"Observed {mode}; schedule expects {match['mode']}")
    if payload.get("drifting"):
        score += 10
        reasons.append("Carrier drift reported")
    if payload.get("unusual_bandwidth"):
        score += 10
        reasons.append("Unusual bandwidth reported")
    classification = "unusual" if score >= 40 else "unresolved" if score >= 10 else "probably_identified" if confidence < 0.8 else "identified"
    return score, classification, match, max(0, confidence), reasons


def save_audio(data_url, event_hint):
    if not data_url:
        return "", ""
    encoded = data_url.split(",", 1)[-1]
    raw = base64.b64decode(encoded, validate=True)
    if len(raw) > 15 * 1024 * 1024:
        raise ValueError("Recording exceeds the 15 MB prototype limit")
    digest = hashlib.sha256(raw).hexdigest()
    suffix = ".wav" if raw[:4] == b"RIFF" else ".bin"
    name = f"{event_hint}-{digest[:12]}{suffix}"
    (RECORDINGS / name).write_bytes(raw)
    return f"recordings/{name}", digest


def insert_event(payload):
    started = payload.get("started_at") or utc_now()
    mode = str(payload.get("mode", "UNKNOWN")).upper()
    freq = float(payload["frequency_khz"])
    fingerprint = payload.get("fingerprint") or hashlib.sha1(f"{round(freq, 1)}:{mode}".encode()).hexdigest()[:16]
    score, classification, match, confidence, reasons = classify({**payload, "started_at": started})
    recording, digest = save_audio(payload.get("audio_data", ""), started.replace(":", "-")[:19])
    with connect() as db:
        recurrence = db.execute("SELECT COUNT(*) FROM events WHERE fingerprint = ?", (fingerprint,)).fetchone()[0]
        if recurrence:
            score += 15
            reasons.append(f"Matches {recurrence} earlier occurrence(s)")
            if score >= 40:
                classification = "unusual"
        cur = db.execute("""
          INSERT INTO events (frequency_khz, bandwidth_hz, started_at, ended_at,
          receiver_name, receiver_url, receiver_location, mode, snr_db, decoded_text,
          recording_path, recording_sha256, fingerprint, anomaly_score, classification,
          match_station, match_source, match_confidence, anomaly_reasons, created_at)
          VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (freq, float(payload.get("bandwidth_hz") or 0), started, payload.get("ended_at"),
              payload.get("receiver_name", "Manual observation"), payload.get("receiver_url", ""),
              payload.get("receiver_location", ""), mode, payload.get("snr_db"),
              payload.get("decoded_text", ""), recording, digest, fingerprint, score,
              classification, match["station"] if match else "", match["source"] if match else "",
              confidence, json.dumps(reasons), utc_now()))
        return cur.lastrowid


def demo_wav(index):
    path = RECORDINGS / f"demo-{index}.wav"
    rate, seconds, tone = 8000, 2, 600 + index * 55
    with wave.open(str(path), "wb") as out:
        out.setparams((1, 2, rate, rate * seconds, "NONE", "not compressed"))
        frames = bytearray()
        for n in range(rate * seconds):
            gate = 1 if (n // 800) % 3 != 2 else 0
            sample = int(8500 * gate * math.sin(2 * math.pi * tone * n / rate))
            frames.extend(struct.pack("<h", sample))
        out.writeframes(frames)
    return f"recordings/{path.name}", hashlib.sha256(path.read_bytes()).hexdigest()


def seed_demo():
    examples = [
        (6195, "AM", "BBC World Service", 4),
        (14590.3, "USB", "Repeated pulses; undecoded", 11),
        (10000, "AM", "Time pips", 18),
        (7321.7, "CW", "CQ CQ DE ?", 9),
    ]
    ids = []
    for i, (freq, mode, text, snr) in enumerate(examples):
        payload = {"frequency_khz": freq, "mode": mode, "decoded_text": text,
                   "snr_db": snr, "receiver_name": "Demo KiwiSDR", "receiver_location": "Virginia, USA",
                   "started_at": f"2026-09-03T{8+i:02d}:1{i}:00Z", "ended_at": f"2026-09-03T{8+i:02d}:1{i}:42Z",
                   "drifting": i == 1}
        event_id = insert_event(payload)
        recording, digest = demo_wav(i)
        with connect() as db:
            db.execute("UPDATE events SET recording_path=?, recording_sha256=? WHERE id=?", (recording, digest, event_id))
        ids.append(event_id)
    return ids


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def json_response(self, data, status=200):
        raw = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/events":
            with connect() as db:
                rows = [dict(row) for row in db.execute("""
                  SELECT e.*, (SELECT COUNT(*) FROM events x WHERE x.fingerprint=e.fingerprint) occurrences
                  FROM events e ORDER BY started_at DESC, id DESC LIMIT 200
                """)]
            for row in rows:
                row["anomaly_reasons"] = json.loads(row["anomaly_reasons"])
            return self.json_response(rows)
        if path == "/api/stats":
            with connect() as db:
                row = db.execute("SELECT COUNT(*) total, SUM(classification='unusual') unusual, COUNT(DISTINCT fingerprint) signatures, COALESCE(SUM((julianday(ended_at)-julianday(started_at))*86400),0) seconds FROM events").fetchone()
            return self.json_response(dict(row))
        if path.startswith("/recordings/"):
            candidate = (ROOT / path.lstrip("/")).resolve()
            if candidate.parent != RECORDINGS.resolve() or not candidate.exists():
                return self.send_error(404)
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(candidate.stat().st_size))
            self.end_headers()
            return self.wfile.write(candidate.read_bytes())
        if path == "/assets/spectralkey-logo.png":
            raw = (ROOT / "assets" / "spectralkey-logo.png").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            return self.wfile.write(raw)
        if path in ("/", "/signal_observatory.html"):
            raw = HTML_PATH.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            return self.wfile.write(raw)
        self.send_error(404)

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length > 16 * 1024 * 1024:
                raise ValueError("Request too large")
            payload = json.loads(self.rfile.read(length) or b"{}")
            if self.path == "/api/events":
                return self.json_response({"id": insert_event(payload)}, 201)
            if self.path == "/api/demo":
                return self.json_response({"ids": seed_demo()}, 201)
            if self.path.startswith("/api/events/") and self.path.endswith("/status"):
                event_id = int(self.path.split("/")[3])
                status = payload.get("status", "reviewed")
                with connect() as db:
                    db.execute("UPDATE events SET status=? WHERE id=?", (status, event_id))
                return self.json_response({"ok": True})
            self.send_error(404)
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            self.json_response({"error": str(exc)}, 400)


if __name__ == "__main__":
    init_db()
    server = ThreadingHTTPServer(("127.0.0.1", 8765), Handler)
    print("SpectralKey running at http://127.0.0.1:8765")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
