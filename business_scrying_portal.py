#!/usr/bin/env python3
# Copyright (c) 2026 theL1zardWizard. All rights reserved. See the LICENSE file.
"""Business Scrying Portal, Conjured by: theL1zardWizard

A local dashboard that shows what each company type needs for 1-10 stars, and rates YOUR company
against that sample. Run it and it opens http://127.0.0.1:8780 in your browser.

  * The server listens on 127.0.0.1 only. Your API key is never sent to the browser.
  * The only site it contacts is api.torn.com, and only to read your own company (profile + detailed).
  * It never loads or scrapes a torn.com page and has no way to trade, send or change anything.
Python 3.9+, standard library only.
"""
import argparse
import json
import math
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import portal_core as core

APP_DIR = Path(__file__).resolve().parent
WEB = APP_DIR / "web"
COOLDOWN = 30                      # Torn caches API answers for 30 s; never ask more often than that
HISTORY_MAX = 500
ORIGIN = os.environ.get("TORN_API_ORIGIN_FOR_TESTS", "https://api.torn.com")
PACE = float(os.environ.get("PORTAL_PACE", "1.0"))   # seconds between search calls: about 55 a minute, under Torn's 100
GAP_ALL, GAP_ONE = (0, 0) if os.environ.get("PORTAL_NO_GAP") else (300, 60)   # minimum seconds between refresh runs
KEY_RE = re.compile(r"[A-Za-z0-9]{16}")
COMMENT = "BusinessScryingPortal"
DEAD_CODES = (2, 13, 18)           # incorrect key, key disabled for inactivity, key paused: never retry these automatically (Torn: repeated invalid-key calls can get an IP banned)

TORN_ERRORS = {
    2: "Torn says the API key is incorrect. Check it and save it again.",
    5: "Torn says too many requests were made. Wait a minute and try again.",
    8: "Torn has temporarily blocked this IP address for too many requests. Wait a while.",
    9: "The Torn API is switched off right now. Try again later.",
    10: "The key owner is in federal jail, so Torn will not serve this key.",
    13: "This key was disabled for inactivity. Create a new one in Torn Preferences > API.",
    16: "This key's access level is too low. Create a Limited Access key (Torn Preferences > API).",
    17: "Torn had a backend error. Try again in a moment.",
    18: "This key is paused. Unpause it in Torn Preferences > API.",
}


class AppError(Exception):
    def __init__(self, msg, code=None, transient=False):
        super().__init__(msg)
        self.code = code
        self.transient = transient


def slim_company(j):
    """Keep only what the page needs. Employee names and ids are dropped on purpose."""
    c = dict(j.get("company") or {})
    emp = c.get("employees")
    if isinstance(emp, dict):
        slim = {}
        for i, e in enumerate(emp.values()):
            if not isinstance(e, dict):
                continue
            slim[str(i)] = {
                "position": e.get("position"),
                "days_in_company": e.get("days_in_company"),
                "last_action": {"timestamp": (e.get("last_action") or {}).get("timestamp")},
                "status": {"state": (e.get("status") or {}).get("state")},
            }
        c["employees"] = slim
    d = j.get("company_detailed") or {}
    keep = ("popularity", "efficiency", "environment", "trains_available", "advertising_budget")
    return {"company": c, "company_detailed": {k: d[k] for k in keep if k in d}}


def demo_company():
    now = int(time.time())
    emp = {str(i): {"position": p, "days_in_company": dd, "last_action": {"timestamp": now - h * 3600},
                    "status": {"state": st}}
           for i, (p, dd, h, st) in enumerate([
               ("Sexpert", 210, 1, "Okay"), ("Sexpert", 95, 5, "Okay"), ("Sales Assistant", 60, 2, "Okay"),
               ("Sales Assistant", 33, 30, "Okay"), ("Sales Assistant", 12, 1, "Hospital"), ("Store Manager", 140, 0, "Okay"),
               ("Sales Assistant", 4, 3, "Okay"), ("Sexpert", 80, 9, "Okay")])}
    return {"company": {"ID": 1, "company_type": 10, "rating": 7, "name": "Demo Novelties (fake data)",
                        "employees_hired": 8, "employees_capacity": 10, "daily_income": 1050000, "daily_customers": 11400,
                        "weekly_income": 8100000, "weekly_customers": 88000, "days_old": 260, "employees": emp},
            "company_detailed": {"popularity": 36, "efficiency": 96, "environment": 90, "trains_available": 6,
                                 "advertising_budget": 2000000}}


class App:
    def __init__(self, config_path: Path, demo: bool):
        self.config_path = config_path
        self.demo = demo
        self.token = secrets.token_urlsafe(24)
        self.lock = threading.Lock()
        self.key = ""
        self.info = None                   # validated key info (access level, user id, company id)
        self.key_error = None
        self.info_try = 0.0
        self.key_dead = False              # set when Torn rejects the saved key; cleared only by saving a different key
        self.info_ok = 0.0
        self.last_fetch = 0.0
        self.last_try = 0.0
        self.cache = None
        self.user = config_path.parent / "userdata"
        self.hist = self.user / "history"
        self.history_path = self.user / "my_history.json"
        self.progress = {"running": False}
        self.last_end = 0.0
        self.last_scope = "type"
        self.cancel = False
        self._load()

    # ---------------------------------------------------------------- key
    def _load(self):
        try:
            self.key = str(json.loads(self.config_path.read_text("utf-8")).get("api_key", "")).strip()
        except (OSError, ValueError):
            self.key = ""
        env = os.environ.get("TORN_API_KEY", "").strip()
        self.key_from_env = bool(env)
        if env:
            self.key = env

    def _get(self, path, params, key=None):
        """One GET to api.torn.com. Returns parsed JSON or raises AppError with a plain-words message."""
        q = dict(params)
        q["key"] = key or self.key
        q["comment"] = COMMENT
        req = urllib.request.Request(ORIGIN + path + "?" + urllib.parse.urlencode(q), headers={"User-Agent": "BusinessScryingPortal/2.0"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                j = json.loads(r.read(8_000_000).decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise AppError("Torn answered with HTTP %d." % e.code, transient=e.code >= 500)
        except (urllib.error.URLError, OSError, ValueError):
            raise AppError("Could not reach api.torn.com. Check your internet connection.", transient=True)
        if isinstance(j, dict) and "error" in j:
            err = j["error"] or {}
            code = err.get("code") if isinstance(err, dict) else None
            if code in DEAD_CODES and q["key"] == self.key:
                self.key_dead = True
                self.key_error = TORN_ERRORS.get(code)
                self.info = None
            raise AppError(TORN_ERRORS.get(code) or "Torn error %s: %s" % (code, (err.get("error") if isinstance(err, dict) else err)), code=code)
        return j

    def check_key(self, key):
        """Only a Limited Access key is accepted: Public and Minimal cannot read your company, Full is more power than this needs."""
        j = self._get("/v2/key/info", {}, key)
        info = j.get("info") or {}
        acc = info.get("access") or {}
        level = acc.get("level")
        if level != 3:
            kind = acc.get("type") or ("level %s" % level)
            if level == 4:
                raise AppError("That is a Full Access key. This app only accepts a Limited Access key, so it can never do more than read. Create one in Torn: Preferences > API.")
            raise AppError("That is a %s key. This app needs your own Limited Access key (Torn: Preferences > API)." % kind)
        user = info.get("user") or {}
        return {"level": 3, "type": acc.get("type") or "Limited Access", "user_id": user.get("id"), "company_id": user.get("company_id")}

    def _ensure_info(self, force=False):
        if self.demo:
            return
        if not self.key:
            raise AppError("Enter your own Limited Access API key on the Data tab first.")
        if self.key_dead:
            raise AppError((self.key_error or "Torn rejected the saved key.") + " No further requests are sent until you save a new key.")
        now = time.time()
        if self.info and not force and now - self.info_ok < 600:
            return
        if now - self.info_try < 3:
            raise AppError(self.key_error or "Please wait a few seconds.")
        self.info_try = now
        try:
            self.info = self.check_key(self.key)
            self.info_ok = now
            self.key_error = None
        except AppError as e:
            self.info = None
            self.key_error = str(e)
            raise

    def state(self):
        if self.key and self.info is None and not self.key_dead and not self.demo and time.time() - self.info_try > 30 and not self.progress.get("running"):
            try:
                self._ensure_info()
            except AppError:
                pass
        gap = GAP_ALL if self.last_scope == "all" else GAP_ONE
        return {"demo": self.demo, "has_key": bool(self.key), "key_from_env": self.key_from_env,
                "key_ok": bool(self.info) or self.demo, "key_error": self.key_error, "info": self.info,
                "cooldown": COOLDOWN, "next_allowed": self.last_fetch + COOLDOWN,
                "refresh_next": self.last_end + gap if self.last_end else 0, "refresh": self.progress}

    def save_key(self, key: str):
        key = (key or "").strip()
        if key and not KEY_RE.fullmatch(key):
            raise AppError("A Torn API key is 16 letters and numbers. Check for stray spaces.")
        if key and not self.demo:
            self.info = self.check_key(key)          # refuses non-Limited keys before anything is stored
            self.info_ok = time.time()
            self.key_error = None
            self.key_dead = False
        with self.lock:
            self.config_path.write_text(json.dumps({"api_key": key}, indent=2), "utf-8")
            try:
                os.chmod(self.config_path, 0o600)
            except OSError:
                pass
            self.key = key
            if not key:
                self.info = None
                self.key_dead = False
                self.key_error = None
            self.cache = None
            self.last_fetch = 0.0
            self.last_try = 0.0
        return self.state()

    # ---------------------------------------------------------------- my company
    def history(self):
        h = core.read_json(self.history_path, [])
        return h if isinstance(h, list) else []

    def _record(self, data):
        c = data.get("company") or {}
        d = data.get("company_detailed") or {}
        e = {"ts": int(time.time()), "id": c.get("ID"), "star": c.get("rating"), "wi": c.get("weekly_income"),
             "wc": c.get("weekly_customers"), "di": c.get("daily_income"), "dc": c.get("daily_customers"),
             "hired": c.get("employees_hired"), "pop": d.get("popularity"), "eff": d.get("efficiency"),
             "env": d.get("environment"), "ad": d.get("advertising_budget")}
        h = self.history()
        if h and h[-1].get("id") == e["id"] and h[-1].get("star") == e["star"] and e["ts"] - h[-1].get("ts", 0) < 600:
            h[-1] = e
        else:
            h.append(e)
        try:
            core.write_json(self.history_path, h[-HISTORY_MAX:])
        except OSError:
            pass

    def mine(self):
        if self.demo:
            return {"data": demo_company(), "cached": False, "age": 0, "history": self.history()}
        with self.lock:
            self._ensure_info()
            now = time.time()
            if self.cache is not None and now - self.last_fetch < COOLDOWN:
                return {"data": self.cache, "cached": True, "age": int(now - self.last_fetch), "history": self.history()}
            if now - self.last_try < 3:
                raise AppError("Please wait a few seconds between pulls.")
            self.last_try = now
            j = self._get("/company/", {"selections": "profile,detailed"})
            data = slim_company(j if isinstance(j, dict) else {})
            if not data["company"].get("weekly_income") and data["company"].get("weekly_income") != 0:
                raise AppError("Torn returned no company for this key. The key's owner must direct a company.")
            self.cache, self.last_fetch = data, now
            self._record(data)
            return {"data": data, "cached": False, "age": 0, "history": self.history()}

    # ---------------------------------------------------------------- refresh the sample
    def start_refresh(self, scope, tid):
        if self.demo:
            raise AppError("Demo mode has no network, so there is nothing to refresh.")
        self._ensure_info()
        with self.lock:
            if self.progress.get("running"):
                raise AppError("A refresh is already running.")
            gap = GAP_ALL if self.last_scope == "all" else GAP_ONE
            wait = self.last_end + gap - time.time()
            if self.last_end and wait > 0:
                raise AppError("Please wait %d more seconds before the next refresh. It keeps the load on Torn's API low." % math.ceil(wait))
            scope = "type" if scope == "type" else "all"
            if scope == "type" and not isinstance(tid, int):
                raise AppError("Choose a company type to refresh.")
            self.cancel = False
            self.last_scope = scope
            self.progress = {"running": True, "done": False, "error": None, "cancelled": False, "scope": scope, "type_name": "starting",
                             "types_done": 0, "types_total": 0, "calls": 0, "est_calls": 0, "rows": 0, "started": time.time(), "log": []}
        threading.Thread(target=self._refresh_worker, args=(scope, tid), daemon=True).start()
        return self.state()

    def cancel_refresh(self):
        self.cancel = True
        return self.state()

    def _search(self, tid, flt, off):
        p = self.progress
        for attempt in range(4):
            try:
                j = self._get("/v2/company/search", {"filters": "type:Equal:%d,%s" % (tid, flt), "limit": 100, "offset": off})
                p["calls"] += 1
                return j.get("search") or []
            except AppError as e:
                if e.code == 5 and attempt < 3:
                    p["type_name"] = "waiting out Torn's rate limit"
                    self._sleep(65)
                elif e.transient and attempt < 3:
                    self._sleep(5 * (attempt + 1))
                else:
                    raise
        return []

    def _sleep(self, secs):
        end = time.time() + secs
        while time.time() < end and not self.cancel:
            time.sleep(min(0.25, max(0.0, end - time.time())))

    def _collect_type(self, tid):
        rows = []
        for flt, max_pages in (("rating:>=:1", 200), ("rating:=:0", 1)):
            off, pages = 0, 0
            while not self.cancel:
                batch = self._search(tid, flt, off)
                for c in batch:
                    if (c.get("type") or {}).get("id") != tid:
                        continue
                    inc, cus, emp = c.get("income") or {}, c.get("customers") or {}, c.get("employees") or {}
                    rows.append([c["id"], int(c.get("rating") or 0), int(c.get("days_old") or 0), int(emp.get("hired") or 0),
                                 int(emp.get("capacity") or 0), int(inc.get("daily") or 0), int(inc.get("weekly") or 0),
                                 int(cus.get("daily") or 0), int(cus.get("weekly") or 0), 1 if c.get("applications_allowed") else 0, tid])
                self.progress["rows"] += len(batch)
                pages += 1
                off += 100
                self._sleep(PACE)
                if len(batch) < 100 or pages >= max_pages:
                    break
        return rows

    def _refresh_worker(self, scope, only):
        p = self.progress
        started = time.time()
        ts = int(started)
        runs = core.read_json(self.hist / "runs.json", []) or []
        run_id = time.strftime("%Y%m%d-%H%M%S", time.gmtime(started))
        taken = {r["run"] for r in runs}
        n = 1
        while run_id in taken or (self.hist / "snap" / run_id).exists():
            n += 1
            run_id = time.strftime("%Y%m%d-%H%M%S", time.gmtime(started)) + "-%d" % n
        done_types, total_rows = [], 0
        try:
            j = self._get("/v2/torn/companies", {})
            types = [(c["id"], str(c.get("name", c["id"]))) for c in (j.get("companies") or []) if isinstance(c, dict) and "id" in c]
            if isinstance(j.get("companies"), dict):
                types = [(int(k), str(v.get("name", k))) for k, v in j["companies"].items()]
            if scope == "type":
                types = [t for t in types if t[0] == only]
                if not types:
                    raise AppError("Torn does not list that company type.")
            idx = core.read_json(self.user / "data" / "index.json") or core.read_json(WEB / "data" / "index.json") or {"types": []}
            rated = {t[0]: t[2] for t in idx["types"]}
            p["types_total"] = len(types)
            p["est_calls"] = sum(math.ceil((rated.get(t[0]) or 300) / 100) + 1 for t in types) + 1
            for tid, name in types:
                if self.cancel:
                    break
                p["type_name"] = name
                rows = self._collect_type(tid)
                if self.cancel:
                    break
                if not rows:
                    p["log"].append("%s: Torn returned no companies, kept the old numbers" % name)
                    continue
                res = core.analyze_type(tid, name, rows, ts)
                prev = None
                for r in reversed(runs):
                    if tid in r.get("types", []):
                        prev = core.load_snapshot(self.hist, r["run"], tid)
                        break
                core.save_snapshot(self.hist, run_id, ts, tid, name, rows)
                core.log_lines(self.hist, run_id, ts, tid, res)
                moved = core.update_moves(self.hist, tid, prev, {"ts": ts, "rows": rows}) if prev else 0
                core.merge_outputs(self.user / "data", WEB / "data", {tid: res}, ts)
                done_types.append(tid)
                total_rows += len(rows)
                p["types_done"] = len(done_types)
                if moved:
                    p["log"].append("%s: %d companies changed star since the last pull" % (name, moved))
        except AppError as e:
            p["error"] = str(e)
        except Exception as e:                      # never let the worker die silently
            p["error"] = "Unexpected error: %s" % e
        finally:
            if done_types:
                runs.append({"run": run_id, "ts": ts, "types": done_types, "rows": total_rows, "calls": p.get("calls", 0),
                             "seconds": int(time.time() - started), "complete": not (self.cancel or p.get("error")), "scope": scope})
                core.write_json(self.hist / "runs.json", runs[-200:])
            p["cancelled"] = bool(self.cancel)
            p["running"] = False
            p["done"] = True
            p["run_id"] = run_id if done_types else None
            self.last_end = time.time()

    # ---------------------------------------------------------------- history for the page
    def history_view(self, tid):
        runs = core.read_json(self.hist / "runs.json", []) or []
        moves = (core.read_json(self.hist / "moves.json", {}) or {}).get(str(tid), {}) if tid else {}
        return {"runs": runs[-60:], "moves": moves, "lines": core.read_lines(self.hist, tid)[-400:] if tid else [], "mine": self.history()}


def make_handler(app: App, port: int):
    allowed_hosts = {"127.0.0.1:%d" % port, "localhost:%d" % port}
    allowed_origins = {"http://" + h for h in allowed_hosts}
    data_re = re.compile(r"^/data/[A-Za-z0-9_.-]+\.json$")
    page = (WEB / "index.html").read_text("utf-8")

    class Handler(BaseHTTPRequestHandler):
        server_version = "PortalUI"

        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(data)

        def _json(self, code, obj):
            self._send(code, json.dumps(obj))

        def _guard(self, need_token):
            if self.headers.get("Host", "") not in allowed_hosts:      # blocks DNS rebinding
                self._json(403, {"error": "bad host"})
                return False
            origin = self.headers.get("Origin")
            if origin and origin not in allowed_origins:
                self._json(403, {"error": "bad origin"})
                return False
            if need_token and not secrets.compare_digest(self.headers.get("X-Token", ""), app.token):
                self._json(403, {"error": "bad token"})
                return False
            return True

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                if self._guard(False):
                    self._send(200, page.replace("__TOKEN__", app.token), "text/html")
            elif data_re.match(path):
                if self._guard(False):
                    f = app.user / path.lstrip("/")
                    if not f.is_file():
                        f = WEB / path.lstrip("/")
                    if f.is_file():
                        self._send(200, f.read_bytes())
                    else:
                        self._json(404, {"error": "not found"})
            elif path == "/api/state":
                if self._guard(True):
                    self._json(200, app.state())
            elif path == "/api/history":
                if self._guard(True):
                    qs = urllib.parse.parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
                    try:
                        tid = int(qs.get("type", ["0"])[0])
                    except ValueError:
                        tid = 0
                    self._json(200, dict(app.history_view(tid), history=app.history()))
            elif path == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self):
            if not self._guard(True):
                return
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(min(n, 20_000)) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError
            except ValueError:
                return self._json(400, {"error": "bad request"})
            try:
                if self.path == "/api/mine":
                    self._json(200, app.mine())
                elif self.path == "/api/key":
                    self._json(200, app.save_key(body.get("api_key", "")))
                elif self.path == "/api/refresh":
                    tid = body.get("type")
                    self._json(200, app.start_refresh(body.get("scope"), tid if isinstance(tid, int) else None))
                elif self.path == "/api/refresh/cancel":
                    self._json(200, app.cancel_refresh())
                else:
                    self._json(404, {"error": "not found"})
            except AppError as e:
                self._json(400, {"error": str(e)})

    return Handler


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Business Scrying Portal (local web UI)")
    ap.add_argument("--port", type=int, default=8780)
    ap.add_argument("--config", default=str(APP_DIR / "config.json"))
    ap.add_argument("--demo", action="store_true", help="fake company, no network: just to look around")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args(argv)

    app = App(Path(args.config), args.demo)
    server = None
    for port in range(args.port, args.port + 10):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app, port))
            break
        except OSError:
            continue
    if server is None:
        print("error: could not open a local port (%d-%d all busy)" % (args.port, args.port + 9))
        return 1
    url = "http://127.0.0.1:%d/" % port
    print("Business Scrying Portal, Conjured by: theL1zardWizard\n  dashboard: %s   (close this window or press Ctrl-C to stop)%s"
          % (url, "   [DEMO DATA]" if args.demo else ""))
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
