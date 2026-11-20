"""วัดแบบ active: ข้อมูลลิงก์ของ AP ที่เชื่อมต่ออยู่ (link rate) + ping หา gateway/โฮสต์ที่กำหนด

ต่างจาก scanner.py (passive: ฟัง beacon ของทุก AP) ตรงที่นี่วัด "ประสบการณ์จริง" ของเครื่องที่เชื่อมต่ออยู่:
    link:  {ssid, bssid, rssi, rx_rate, tx_rate (Mbps), channel, band}   (ค่าที่หาไม่ได้เป็น None)
    ping:  {host, sent, received, loss (%), min, avg, max, jitter (ms)}
"""
from __future__ import annotations

import json
import platform
import re
import shutil
import subprocess
from statistics import mean

from .scanner import ScanError, _run, freq_to_band, freq_to_channel, norm_bssid, pct_to_dbm

MAC_RE = re.compile(r"([0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5})")
HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-]{0,252}$|^[0-9a-fA-F:]{2,45}$")  # ห้ามขึ้นต้นด้วย '-'


def _empty_link() -> dict:
    return {"ssid": None, "bssid": None, "rssi": None, "rx_rate": None, "tx_rate": None,
            "channel": None, "band": None}


# ---------------------------------------------------------------- link info
def parse_netsh_interfaces(text: str) -> dict:
    """`netsh wlan show interfaces` — จับคู่ด้วยหน่วย/รูปแบบ เพื่อไม่ผูกกับภาษาของ Windows"""
    link = _empty_link()
    connected = False
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key, val = key.strip(), val.strip()
        low = key.lower()
        if re.fullmatch(r"ssid", low):
            link["ssid"] = val or None
            connected = connected or bool(val)
        elif re.fullmatch(r"bssid", low):
            m = MAC_RE.search(line)
            if m:
                link["bssid"] = norm_bssid(m.group(1))
        elif "(mbps)" in low:
            m = re.match(r"([\d.]+)", val)
            if m:
                # ลำดับของ netsh: Receive rate ก่อน Transmit rate
                slot = "rx_rate" if link["rx_rate"] is None else "tx_rate"
                link[slot] = float(m.group(1))
        elif re.fullmatch(r"channel", low) and val.isdigit():
            link["channel"] = int(val)
        elif val.endswith("%") and val[:-1].strip().isdigit():
            link["rssi"] = pct_to_dbm(float(val[:-1]))
    if not connected:
        raise ScanError("ไม่ได้เชื่อมต่อ Wi-Fi อยู่ (วัดแบบ active ต้องเชื่อมต่อเครือข่ายก่อน)")
    if link["channel"]:
        link["band"] = "2.4" if link["channel"] <= 14 else "5"
    return link


def parse_iw_link(text: str) -> dict:
    if "Not connected" in text:
        raise ScanError("ไม่ได้เชื่อมต่อ Wi-Fi อยู่ (วัดแบบ active ต้องเชื่อมต่อเครือข่ายก่อน)")
    link = _empty_link()
    m = re.search(r"Connected to ([0-9a-fA-F:]{17})", text)
    if m:
        link["bssid"] = norm_bssid(m.group(1))
    m = re.search(r"SSID:\s*(.*)", text)
    if m:
        link["ssid"] = m.group(1).strip()
    m = re.search(r"freq:\s*(\d+)", text)
    if m:
        f = int(m.group(1))
        link["channel"], link["band"] = freq_to_channel(f), freq_to_band(f)
    m = re.search(r"signal:\s*(-?\d+)", text)
    if m:
        link["rssi"] = float(m.group(1))
    m = re.search(r"rx bitrate:\s*([\d.]+)", text)
    if m:
        link["rx_rate"] = float(m.group(1))
    m = re.search(r"tx bitrate:\s*([\d.]+)", text)
    if m:
        link["tx_rate"] = float(m.group(1))
    return link


def parse_nmcli_active(text: str) -> dict:
    """nmcli -t -f ACTIVE,BSSID,SSID,CHAN,FREQ,RATE,SIGNAL dev wifi — RATE คือเรทสูงสุดที่ AP โฆษณา (ไม่ใช่เรทจริง)"""
    from .scanner import split_terse
    for line in text.splitlines():
        f = split_terse(line)
        if len(f) >= 7 and f[0] == "yes":
            link = _empty_link()
            link["bssid"], link["ssid"] = norm_bssid(f[1]), f[2] or None
            link["channel"] = int(f[3]) if f[3].isdigit() else None
            fm, rm = re.match(r"\d+", f[4]), re.match(r"[\d.]+", f[5])
            if fm:
                link["band"] = freq_to_band(int(fm.group()))
            if rm:
                link["rx_rate"] = link["tx_rate"] = float(rm.group())
            if f[6].isdigit():
                link["rssi"] = pct_to_dbm(float(f[6]))
            return link
    raise ScanError("ไม่ได้เชื่อมต่อ Wi-Fi อยู่ (วัดแบบ active ต้องเชื่อมต่อเครือข่ายก่อน)")


def parse_macos_current(text: str) -> dict:
    data = json.loads(text)
    for card in data.get("SPAirPortDataType", []):
        for iface in card.get("spairport_airport_interfaces", []):
            n = iface.get("spairport_current_network_information")
            if not n:
                continue
            link = _empty_link()
            link["ssid"] = n.get("_name")
            ch = str(n.get("spairport_network_channel", ""))
            mc = re.match(r"(\d+)", ch)
            link["channel"] = int(mc.group(1)) if mc else None
            link["band"] = "5" if "5GHz" in ch else "6" if "6GHz" in ch else "2.4"
            ms = re.search(r"(-\d+)\s*dBm", n.get("spairport_signal_noise", ""))
            link["rssi"] = float(ms.group(1)) if ms else None
            rate = n.get("spairport_network_rate")
            if rate is not None:
                link["rx_rate"] = link["tx_rate"] = float(rate)
            return link
    raise ScanError("ไม่ได้เชื่อมต่อ Wi-Fi อยู่ (วัดแบบ active ต้องเชื่อมต่อเครือข่ายก่อน)")


def link_info() -> dict:
    system = platform.system()
    if system == "Windows":
        return parse_netsh_interfaces(_run(["netsh", "wlan", "show", "interfaces"]))
    if system == "Linux":
        if shutil.which("iw"):
            dev = re.search(r"Interface\s+(\S+)", _run(["iw", "dev"]))
            if dev:
                return parse_iw_link(_run(["iw", "dev", dev.group(1), "link"]))
        if shutil.which("nmcli"):
            return parse_nmcli_active(_run(["nmcli", "-t", "-f", "ACTIVE,BSSID,SSID,CHAN,FREQ,RATE,SIGNAL",
                                            "dev", "wifi", "list"]))
        raise ScanError("ต้องมี iw หรือ nmcli ติดตั้งอยู่")
    if system == "Darwin":
        return parse_macos_current(_run(["system_profiler", "SPAirPortDataType", "-json"], 60))
    raise ScanError(f"ระบบปฏิบัติการ {system} ยังไม่รองรับ")


# ---------------------------------------------------------------- gateway
def parse_default_gateway(text: str, system: str) -> str | None:
    if system == "Windows":  # route print -4 0.0.0.0
        best = None
        for line in text.splitlines():
            m = re.match(r"^\s*0\.0\.0\.0\s+0\.0\.0\.0\s+(\d+\.\d+\.\d+\.\d+)\s+\S+\s+(\d+)", line)
            if m and (best is None or int(m.group(2)) < best[1]):
                best = (m.group(1), int(m.group(2)))
        return best[0] if best else None
    if system == "Darwin":  # route -n get default
        m = re.search(r"gateway:\s*(\S+)", text)
        return m.group(1) if m else None
    m = re.search(r"default via (\S+)", text)  # ip route show default
    return m.group(1) if m else None


def default_gateway() -> str | None:
    system = platform.system()
    try:
        if system == "Windows":
            return parse_default_gateway(_run(["route", "print", "-4", "0.0.0.0"]), system)
        if system == "Darwin":
            return parse_default_gateway(_run(["route", "-n", "get", "default"]), system)
        return parse_default_gateway(_run(["ip", "route", "show", "default"]), system)
    except ScanError:
        return None


# ---------------------------------------------------------------- ping
def parse_ping(text: str, sent: int, host: str) -> dict:
    """อ่านผล ping แบบไม่ผูกภาษา: นับบรรทัดตอบกลับจาก 'ttl=' และดึงเวลา 'time=12ms' / 'time<1ms'"""
    times = []
    for line in text.splitlines():
        if re.search(r"ttl\s*=", line, re.I):
            m = re.search(r"time\s*([=<])\s*([\d.]+)\s*ms", line, re.I)
            if m:
                times.append(float(m.group(2)) if m.group(1) == "=" else 0.5)  # "<1ms" ≈ 0.5
            else:
                m = re.search(r"([\d.]+)\s*ms", line)
                times.append(float(m.group(1)) if m else 0.0)
    got = len(times)
    out = {"host": host, "sent": sent, "received": got,
           "loss": round(100.0 * (sent - got) / sent, 1) if sent else 100.0,
           "min": None, "avg": None, "max": None, "jitter": None}
    if times:
        out.update(min=round(min(times), 2), avg=round(mean(times), 2), max=round(max(times), 2),
                   jitter=round(mean(abs(a - b) for a, b in zip(times, times[1:])), 2) if got > 1 else 0.0)
    return out


def ping(host: str, count: int = 8) -> dict:
    if not HOST_RE.match(host or ""):
        raise ScanError("ชื่อโฮสต์/IP สำหรับ ping ไม่ถูกต้อง")
    count = max(2, min(int(count), 30))
    system = platform.system()
    if system == "Windows":
        cmd = ["ping", "-n", str(count), "-w", "1000", host]
    elif system == "Darwin":
        cmd = ["ping", "-c", str(count), "-W", "1000", host]
    else:
        cmd = ["ping", "-c", str(count), "-i", "0.2", "-W", "1", host]
    for attempt in (cmd, [c for c in cmd if c not in ("-i", "0.2")]):  # busybox ไม่รองรับ -i
        try:
            res = subprocess.run(attempt, capture_output=True, text=True, errors="replace",
                                 timeout=count * 2 + 10)
        except FileNotFoundError as e:
            raise ScanError("ไม่พบคำสั่ง ping") from e
        except subprocess.TimeoutExpired as e:
            raise ScanError("ping ใช้เวลานานเกินไป") from e
        out = res.stdout + res.stderr
        if res.returncode in (0, 1) or "ttl" in out.lower():  # 1 = มี loss ซึ่งเป็นผลวัดที่ใช้ได้
            return parse_ping(res.stdout, count, host)
    raise ScanError((out.strip() or "ping ล้มเหลว")[:200])


# ---------------------------------------------------------------- public API
def measure(host: str | None = None, count: int = 8) -> dict:
    link = link_info()
    target = (host or "").strip() or default_gateway()
    if not target:
        raise ScanError("หา gateway ไม่พบ — ระบุโฮสต์/IP ที่จะ ping เอง")
    return {"link": link, "ping": ping(target, count)}
