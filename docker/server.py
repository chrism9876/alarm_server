#!/usr/bin/env python3
"""
ebc_alarmclock - alarm server

A small standalone web server (Python 3 standard library only) that
  * serves a web UI for managing the clock's alarms   ->  GET  /
  * serves the alarm config to the ESP32              ->  GET  /api/alarms
  * saves alarm changes made in the web UI            ->  PUT  /api/alarms
  * reports when the clock last fetched its config    ->  GET  /api/status

Alarms are stored in alarms.json next to this file (or ALARM_DATA_FILE).

Run:
    python server.py                    # listens on 0.0.0.0:8080
    python server.py --port 9000

Environment variables (all optional):
    ALARM_SERVER_PORT   port to listen on (default 8080)
    ALARM_DATA_FILE     path of the json file (default ./alarms.json)
    ALARM_API_TOKEN     if set, saving alarms requires header  X-Api-Token: <token>
                        (the web UI asks for it once and remembers it)
"""

import argparse
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DATA_FILE = Path(os.environ.get("ALARM_DATA_FILE", BASE_DIR / "conf/alarms.json"))
API_TOKEN = os.environ.get("ALARM_API_TOKEN", "")

# Must match the firmware (02_variables_presetup.h)
MAX_ALARMS = 12          # maxAlarms
MSG_MAX_LEN = 31         # alarmMsgLength (33) - 2, same limit the old on-device UI used

# field name -> (type, min, max, default). Mirrors struct alarmType in the firmware.
FIELDS = {
    "active":            (bool, None, None, False),
    "hour":              (int, 0, 23, 0),
    "min":               (int, 0, 59, 0),
    "sec":               (int, 0, 59, 0),
    "month":             (int, 0, 12, 0),      # 0 = any month
    "day":               (int, 0, 31, 0),      # 0 = any day of the month
    "weekDay":           (int, 0, 7, 0),       # 0 = any, 1 = Sunday ... 7 = Saturday
    "weekDays":          (bool, None, None, False),
    "weekendDays":       (bool, None, None, False),
    "recurringType":     (int, 0, 5, 0),       # 0 off, 1 sec, 2 min, 3 hour, 4 day, 5 month
    "recurringInterval": (int, 0, 32766, 0),
    "message":           (str, 0, MSG_MAX_LEN, ""),
    "duration":          (int, 0, 32766, 10),  # seconds
    "buzzer":            (int, 0, 19, 4),
    "bright":            (bool, None, None, False),
    "fx":                (int, 0, 1, 1),       # 1 = blink
    "actionType":        (int, 0, 6, 0),
    "actionNum":         (int, 0, 99, 0),
}

_lock = threading.Lock()
_last_fetch = {"time": None, "client": None}


class ValidationError(ValueError):
    pass


def default_alarm():
    return {k: spec[3] for k, spec in FIELDS.items()}


def clean_alarm(raw, index):
    if not isinstance(raw, dict):
        raise ValidationError(f"alarm {index + 1}: must be an object")
    out = default_alarm()
    for key, (typ, lo, hi, _default) in FIELDS.items():
        if key not in raw:
            continue
        val = raw[key]
        if typ is bool:
            if not isinstance(val, bool):
                raise ValidationError(f"alarm {index + 1}: '{key}' must be true/false")
        elif typ is int:
            if isinstance(val, bool) or not isinstance(val, (int, float)) or int(val) != val:
                raise ValidationError(f"alarm {index + 1}: '{key}' must be a whole number")
            val = int(val)
            if not lo <= val <= hi:
                raise ValidationError(f"alarm {index + 1}: '{key}' must be between {lo} and {hi}")
        elif typ is str:
            if not isinstance(val, str):
                raise ValidationError(f"alarm {index + 1}: '{key}' must be text")
            # the LED matrix font is ASCII only
            if any(ord(c) < 32 or ord(c) > 126 for c in val):
                raise ValidationError(f"alarm {index + 1}: message may only use plain ASCII characters")
            if len(val) > hi:
                raise ValidationError(f"alarm {index + 1}: message is longer than {hi} characters")
        out[key] = val
    if "name" in raw and isinstance(raw["name"], str):
        out["name"] = raw["name"][:40]          # label for the web UI only, ignored by the clock
    return out


def load_config():
    if DATA_FILE.exists():
        try:
            data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
            alarms = [clean_alarm(a, i) for i, a in enumerate(data.get("alarms", [])[:MAX_ALARMS])]
            version = int(data.get("version", 1))
            updated = data.get("updated")
        except (ValueError, OSError) as exc:
            print(f"!! could not read {DATA_FILE}: {exc} - starting with empty alarms", file=sys.stderr)
            alarms, version, updated = [], 1, None
    else:
        alarms, version, updated = [], 1, None
    while len(alarms) < MAX_ALARMS:
        alarms.append(default_alarm())
    return {"version": version, "updated": updated, "maxAlarms": MAX_ALARMS, "alarms": alarms}


def save_config(config):
    tmp = DATA_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(config, indent=2), encoding="utf-8")
    os.replace(tmp, DATA_FILE)


CONFIG = load_config()


class Handler(BaseHTTPRequestHandler):
    server_version = "ebcAlarmServer/1.0"

    # -- helpers ---------------------------------------------------------
    def _send_json(self, status, payload, extra_headers=None):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path, content_type):
        try:
            body = path.read_bytes()
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s - %s\n" % (datetime.now().strftime("%H:%M:%S"), self.address_string(), fmt % args))

    # -- routes ----------------------------------------------------------
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._send_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
        elif path == "/api/alarms":
            self._get_alarms()
        elif path == "/api/status":
            with _lock:
                self._send_json(HTTPStatus.OK, {
                    "version": CONFIG["version"],
                    "updated": CONFIG["updated"],
                    "lastClockFetch": _last_fetch["time"],
                    "lastClockAddress": _last_fetch["client"],
                    "serverTime": time.time(),
                    "tokenRequired": bool(API_TOKEN),
                })
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def _get_alarms(self):
        with _lock:
            etag = f'"v{CONFIG["version"]}"'
            # the clock identifies itself with this header, so the UI can show when it last synced
            if self.headers.get("X-Clock"):
                _last_fetch["time"] = time.time()
                _last_fetch["client"] = self.client_address[0]
            if self.headers.get("If-None-Match") == etag:
                self.send_response(HTTPStatus.NOT_MODIFIED)
                self.send_header("ETag", etag)
                self.end_headers()
                return
            self._send_json(HTTPStatus.OK, CONFIG, {"ETag": etag})

    def do_PUT(self):
        if self.path.split("?", 1)[0] != "/api/alarms":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if API_TOKEN and self.headers.get("X-Api-Token") != API_TOKEN:
            self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "missing or wrong API token"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length > 64 * 1024:
                raise ValidationError("request too large")
            data = json.loads(self.rfile.read(length) or b"{}")
            raw_alarms = data.get("alarms") if isinstance(data, dict) else None
            if not isinstance(raw_alarms, list):
                raise ValidationError("body must be {\"alarms\": [...]}")
            if len(raw_alarms) > MAX_ALARMS:
                raise ValidationError(f"the clock supports at most {MAX_ALARMS} alarms")
            alarms = [clean_alarm(a, i) for i, a in enumerate(raw_alarms)]
        except (ValueError, ValidationError) as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        while len(alarms) < MAX_ALARMS:
            alarms.append(default_alarm())

        with _lock:
            CONFIG["alarms"] = alarms
            CONFIG["version"] += 1
            CONFIG["updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            save_config(CONFIG)
            self._send_json(HTTPStatus.OK, CONFIG, {"ETag": f'"v{CONFIG["version"]}"'})


def main():
    parser = argparse.ArgumentParser(description="ebc_alarmclock alarm server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=int(os.environ.get("ALARM_SERVER_PORT", 8080)))
    args = parser.parse_args()

    if not DATA_FILE.exists():
        save_config(CONFIG)
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Alarm server on http://{args.host}:{args.port}/  (data: {DATA_FILE})")
    print(f"Point the clock at:  http://<this-computer-ip>:{args.port}/api/alarms")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
