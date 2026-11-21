# Alarm server

Manage the clock's alarms from a web page instead of on the clock. The clock downloads the alarms from this server.

## Run it

Needs Python 3.8 or newer. There is nothing to install.

```
python server.py
```

Open `http://<this-computer-ip>:8080/` to edit the alarms. Changes are saved to `alarms.json`.

Options:

| Setting | Default | Meaning |
|---|---|---|
| `--port` / `ALARM_SERVER_PORT` | 8080 | Port to listen on |
| `ALARM_DATA_FILE` | `./alarms.json` | Where the alarms are stored |
| `ALARM_API_TOKEN` | (none) | If set, saving needs this token. The page asks for it once. The clock only reads, so it needs no token |

Run it on an always-on machine with a fixed LAN address, such as a Pi, NAS or home server.

## Point the clock at it

In `01_definitions_libraries.h`:

```c
#define REMOTE_ALARMS
#define REMOTE_ALARMS_URL      "http://192.168.1.100:8080/api/alarms"
#define REMOTE_ALARMS_INTERVAL 60      // seconds between syncs
```

Then flash the clock.

## How the clock uses it

- It syncs at startup, every `REMOTE_ALARMS_INTERVAL` seconds, and when you short-press the rotary encoder or pick menu item 0 ("Sync alarms").
- The clock's own web interface keeps its Alarms tab, but the tab now shows the sync status and has a **Sync alarms now** button.
- The server sends an ETag. When nothing has changed it answers `304`, so most syncs are tiny.
- Changed alarms are written to EEPROM. If the server or WiFi is down, the clock keeps using the last alarms it downloaded.
- The clock skips a periodic sync when an alarm is due within a few seconds, or while one is ringing. Alarms trigger on an exact second, and a slow request could otherwise make the clock miss one.

## API

- `GET /api/alarms` returns `{"version": n, "updated": "...", "maxAlarms": 12, "alarms": [ ... ]}`
- `PUT /api/alarms` takes the body `{"alarms": [ ... ]}`, with at most 12 alarms. Missing alarms become inactive.
- `GET /api/status` returns the config version and when the clock last fetched it

Each alarm uses the same fields as `struct alarmType` in the firmware: `active, hour, min, sec, month, day, weekDay, weekDays, weekendDays, recurringType, recurringInterval, message, duration, buzzer, bright, fx, actionType, actionNum`. The optional `name` is only a label for the web page.
