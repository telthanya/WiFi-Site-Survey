"""เซิร์ฟเวอร์ในเครื่อง (127.0.0.1) ให้หน้าเว็บเรียกสแกน/บันทึกโปรเจกต์/สร้าง PDF"""
from __future__ import annotations

import json
import mimetypes
import os
import re
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import __version__, active, report, scanner

STATIC = Path(__file__).parent / "static"
MAX_BODY = 60 * 1024 * 1024  # แปลนอาคาร + heatmap เป็นรูป base64
NAME_RE = re.compile(r"^[\w\- .()\u0E00-\u0E7F]{1,80}$")
LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}


def default_projects_dir() -> Path:
    return Path(os.environ.get("WIFI_SURVEY_HOME", Path.home() / ".wifi-survey")) / "projects"


class App:
    def __init__(self, projects_dir: Path):
        self.projects_dir = Path(projects_dir)
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        self.scan_lock = threading.Lock()  # สแกนทีละงานพอ (อะแดปเตอร์เดียว)

    def project_path(self, name: str) -> Path:
        if not NAME_RE.match(name) or name.strip(". ") != name.strip():
            raise ValueError("ชื่อโปรเจกต์ไม่ถูกต้อง")
        return self.projects_dir / f"{name}.json"


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"WifiSurvey/{__version__}"

        def log_message(self, fmt, *args):  # เงียบ ๆ
            pass

        # ---- utils
        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
            return host in LOCAL_HOSTS  # กัน DNS rebinding

        def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj):
            self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def _err(self, code: int, msg: str):
            self._json(code, {"error": msg})

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                raise ValueError("ข้อมูลใหญ่เกินไป")
            return json.loads(self.rfile.read(n) or b"{}")

        def _guard(self) -> bool:
            if not self._host_ok():
                self._err(403, "forbidden host")
                return False
            return True

        # ---- routes
        def do_GET(self):
            if not self._guard():
                return
            url = urlparse(self.path)
            path = unquote(url.path)
            if path == "/api/info":
                ok, how = scanner.scanner_available()
                return self._json(200, {"version": __version__, "scanner_ok": ok, "scanner": how,
                                        "thai_font": report.find_thai_font() is not None})
            if path == "/api/projects":
                names = sorted(p.stem for p in app.projects_dir.glob("*.json"))
                return self._json(200, {"projects": names})
            m = re.match(r"^/api/projects/(.+)$", path)
            if m:
                try:
                    f = app.project_path(m.group(1))
                except ValueError as e:
                    return self._err(400, str(e))
                if not f.exists():
                    return self._err(404, "ไม่พบโปรเจกต์")
                return self._send(200, f.read_bytes(), "application/json; charset=utf-8")
            if path == "/":
                path = "/index.html"
            f = (STATIC / path.lstrip("/")).resolve()
            if STATIC.resolve() not in f.parents or not f.is_file():
                return self._err(404, "not found")
            ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            self._send(200, f.read_bytes(), ctype)

        def do_POST(self):
            if not self._guard():
                return
            url = urlparse(self.path)
            path = unquote(url.path)
            try:
                if path == "/api/scan":
                    q = parse_qs(url.query)
                    samples = int(q.get("samples", ["1"])[0])
                    with app.scan_lock:
                        try:
                            return self._json(200, {"aps": scanner.scan(samples)})
                        except scanner.ScanError as e:
                            return self._err(500, str(e))
                if path == "/api/active":
                    q = parse_qs(url.query)
                    with app.scan_lock:
                        try:
                            return self._json(200, active.measure(q.get("host", [""])[0],
                                                                  int(q.get("count", ["8"])[0])))
                        except scanner.ScanError as e:
                            return self._err(500, str(e))
                m = re.match(r"^/api/projects/(.+)$", path)
                if m:
                    data = self._body()
                    f = app.project_path(m.group(1))
                    tmp = f.with_suffix(".tmp")
                    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                    tmp.replace(f)
                    return self._json(200, {"ok": True})
                if path == "/api/report":
                    d = self._body()
                    pdf = report.build_pdf(
                        d.get("project") or {}, report.decode_data_url(d.get("image")),
                        lang=d.get("lang", "en"), target=float(d.get("target", -67)),
                        coverage=d.get("coverage"), surveyor=d.get("surveyor", ""))
                    return self._send(200, pdf, "application/pdf",
                                      {"Content-Disposition": 'attachment; filename="wifi-survey-report.pdf"'})
            except (ValueError, KeyError, json.JSONDecodeError) as e:
                return self._err(400, str(e))
            except Exception as e:  # pragma: no cover
                return self._err(500, f"{type(e).__name__}: {e}")
            self._err(404, "not found")

        def do_DELETE(self):
            if not self._guard():
                return
            m = re.match(r"^/api/projects/(.+)$", unquote(urlparse(self.path).path))
            if not m:
                return self._err(404, "not found")
            try:
                f = app.project_path(m.group(1))
            except ValueError as e:
                return self._err(400, str(e))
            f.unlink(missing_ok=True)
            self._json(200, {"ok": True})

    return Handler


def serve(host="127.0.0.1", port=8765, projects_dir=None, open_browser=True):
    app = App(projects_dir or default_projects_dir())
    httpd = ThreadingHTTPServer((host, port), make_handler(app))
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    print(f"Wi-Fi Site Survey พร้อมใช้งานที่ {url}  (กด Ctrl+C เพื่อปิด)")
    print(f"โปรเจกต์เก็บที่: {app.projects_dir}")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nปิดโปรแกรม")
    finally:
        httpd.server_close()
    return 0
