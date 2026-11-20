import base64, io, json, threading, urllib.request, urllib.error
import pytest
from PIL import Image
from wifi_survey import report, server

PROJECT = {
    "name": "Office 3F ทดสอบ",
    "points": [
        {"label": "จุด 1", "aps": [{"bssid": "aa:bb:cc:dd:ee:01", "ssid": "Corp", "rssi": -50, "channel": 36, "band": "5"},
                                  {"bssid": "aa:bb:cc:dd:ee:02", "ssid": "Corp", "rssi": -80, "channel": 1, "band": "2.4"}]},
        {"label": "P2", "aps": [{"bssid": "aa:bb:cc:dd:ee:01", "ssid": "Corp", "rssi": -75, "channel": 36, "band": "5"}]},
        {"label": "P3", "aps": []},
    ],
    "aps": [{"name": "AP-1", "band": "5", "channel": 36, "txPower": 18, "pos_m": "3.0, 4.0"}],
}


def png():
    b = io.BytesIO(); Image.new("RGB", (800, 500), "#ccd").save(b, "PNG"); return b.getvalue()


def test_stats():
    s = report.stats(PROJECT, -67)
    assert s["n"] == 3 and s["n_ok"] == 1 and s["min"] == -75 and s["max"] == -50
    assert report.channel_table(PROJECT) == [("2.4", 1, 1, -80), ("5", 36, 1, -50)]


@pytest.mark.parametrize("lang", ["en", "th"])
def test_pdf(lang):
    pdf = report.build_pdf(PROJECT, png(), lang=lang, coverage=71.5, surveyor="Somchai")
    assert pdf.startswith(b"%PDF") and len(pdf) > 3000


def test_pdf_without_image():
    assert report.build_pdf({"name": "x"}, None).startswith(b"%PDF")


def test_decode_data_url():
    url = "data:image/png;base64," + base64.b64encode(png()).decode()
    assert report.decode_data_url(url)[:4] == b"\x89PNG"
    with pytest.raises(ValueError):
        report.decode_data_url("data:text/html;base64,AAAA")


@pytest.fixture
def srv(tmp_path):
    app = server.App(tmp_path)
    httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(app))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def call(url, data=None, method=None, headers=None):
    req = urllib.request.Request(url, data=None if data is None else json.dumps(data).encode(), method=method,
                                 headers=headers or {})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_static_and_info(srv):
    code, body = call(srv + "/")
    assert code == 200 and b"Wi-Fi Site Survey" in body
    assert call(srv + "/app.js")[0] == 200
    code, body = call(srv + "/api/info")
    assert code == 200 and "scanner_ok" in json.loads(body)


def test_path_traversal_blocked(srv):
    assert call(srv + "/..%2f..%2fserver.py")[0] == 404
    assert call(srv + "/api/projects/..%2f..%2fx")[0] == 400


def test_project_roundtrip(srv):
    name = urllib.request.quote("ออฟฟิศ 1")
    assert call(srv + "/api/projects/" + name, {"name": "ออฟฟิศ 1", "points": []})[0] == 200
    assert json.loads(call(srv + "/api/projects")[1])["projects"] == ["ออฟฟิศ 1"]
    assert json.loads(call(srv + "/api/projects/" + name)[1])["name"] == "ออฟฟิศ 1"
    assert call(srv + "/api/projects/" + name, method="DELETE")[0] == 200
    assert call(srv + "/api/projects/" + name)[0] == 404


def test_report_endpoint(srv):
    img = "data:image/png;base64," + base64.b64encode(png()).decode()
    code, body = call(srv + "/api/report", {"project": PROJECT, "image": img, "target": -67})
    assert code == 200 and body.startswith(b"%PDF")
    assert call(srv + "/api/report", {"project": PROJECT, "image": "data:text/html;base64,AA"})[0] == 400


def test_bad_host_rejected(srv):
    assert call(srv + "/api/info", headers={"Host": "evil.example.com"})[0] == 403
