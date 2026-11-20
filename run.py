#!/usr/bin/env python3
"""เริ่มโปรแกรม Wi-Fi Site Survey:  python run.py [--port 8765] [--no-browser]"""
import argparse

from wifi_survey.server import serve

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Wi-Fi Site Survey")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--projects-dir", help="โฟลเดอร์เก็บโปรเจกต์ (ค่าเริ่มต้น ~/.wifi-survey/projects)")
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    raise SystemExit(serve(port=a.port, projects_dir=a.projects_dir, open_browser=not a.no_browser))
