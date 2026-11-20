"""สแกน Wi-Fi จากระบบปฏิบัติการ (Windows / Linux / macOS)

ทุกแพลตฟอร์มคืนค่าเป็น list ของ dict รูปแบบเดียวกัน:
    {bssid, ssid, rssi (dBm), channel, freq (MHz), band, security}

หมายเหตุ: Windows (netsh) และ nmcli รายงานแค่ "เปอร์เซ็นต์สัญญาณ" จึงแปลงเป็น dBm
โดยประมาณด้วย  dBm = percent/2 - 100  (ค่าที่ใช้กันทั่วไป) ส่วน macOS และ `iw` ให้ dBm จริง
"""
from __future__ import annotations

import json
import platform
import re
import shutil
import subprocess
from collections import defaultdict
from statistics import mean


class ScanError(RuntimeError):
    """สแกนไม่สำเร็จ (ไม่มีอะแดปเตอร์, ไม่มีสิทธิ์, ไม่มีคำสั่ง ฯลฯ)"""


# ---------------------------------------------------------------- helpers
def pct_to_dbm(percent: float) -> float:
    return round(max(0.0, min(100.0, float(percent))) / 2 - 100, 1)


def channel_to_freq(channel: int, band_hint: str | None = None) -> int | None:
    if channel is None:
        return None
    if band_hint == "6":
        return 5950 + 5 * channel
    if 1 <= channel <= 13:
        return 2407 + 5 * channel
    if channel == 14:
        return 2484
    if 32 <= channel <= 177:
        return 5000 + 5 * channel
    return None


def freq_to_channel(freq: int) -> int | None:
    if 2412 <= freq <= 2472:
        return (freq - 2407) // 5
    if freq == 2484:
        return 14
    if 5160 <= freq <= 5885:
        return (freq - 5000) // 5
    if 5955 <= freq <= 7115:
        return (freq - 5950) // 5
    return None


def freq_to_band(freq: int | None) -> str:
    if not freq:
        return "?"
    if freq < 3000:
        return "2.4"
    if freq < 5900:
        return "5"
    return "6"


def norm_bssid(b: str) -> str:
    parts = re.split(r"[:\-]", b.strip().lower())
    return ":".join(p.zfill(2) for p in parts)


def _ap(bssid, ssid, rssi, channel=None, freq=None, security="", band=None) -> dict:
    if freq is None and channel is not None:
        freq = channel_to_freq(channel, band)
    if channel is None and freq:
        channel = freq_to_channel(freq)
    return {
        "bssid": norm_bssid(bssid),
        "ssid": ssid or "",
        "rssi": float(rssi),
        "channel": channel,
        "freq": freq,
        "band": band or freq_to_band(freq),
        "security": security or "",
    }


def _run(cmd: list[str], timeout: float = 30) -> str:
    try:
        res = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
        )
    except FileNotFoundError as e:
        raise ScanError(f"ไม่พบคำสั่ง {cmd[0]}") from e
    except subprocess.TimeoutExpired as e:
        raise ScanError(f"คำสั่ง {cmd[0]} ใช้เวลานานเกินไป") from e
    if res.returncode != 0:
        raise ScanError((res.stderr or res.stdout).strip() or f"{cmd[0]} ล้มเหลว")
    return res.stdout


# ---------------------------------------------------------------- Linux
def split_terse(line: str) -> list[str]:
    """แยกบรรทัดโหมด -t ของ nmcli (ใช้ ':' คั่น และ '\\:' คือ ':' ในค่า)"""
    out, cur, i = [], [], 0
    while i < len(line):
        c = line[i]
        if c == "\\" and i + 1 < len(line):
            cur.append(line[i + 1])
            i += 2
            continue
        if c == ":":
            out.append("".join(cur))
            cur = []
        else:
            cur.append(c)
        i += 1
    out.append("".join(cur))
    return out


def parse_nmcli(text: str) -> list[dict]:
    aps = []
    for line in text.splitlines():
        if not line.strip():
            continue
        f = split_terse(line)
        if len(f) < 6:
            continue
        bssid, ssid, chan, freq, signal, sec = f[:6]
        try:
            freq_mhz = int(re.match(r"\d+", freq).group())
            aps.append(_ap(bssid, ssid, pct_to_dbm(float(signal)),
                           int(chan) if chan.isdigit() else None, freq_mhz, sec))
        except (AttributeError, ValueError):
            continue
    return aps


def parse_iw(text: str) -> list[dict]:
    aps, cur = [], None

    def flush():
        if cur and "rssi" in cur:
            aps.append(_ap(cur["bssid"], cur.get("ssid", ""), cur["rssi"],
                           None, cur.get("freq"), cur.get("sec", "")))

    for line in text.splitlines():
        m = re.match(r"^BSS ([0-9a-fA-F:]{17})", line)
        if m:
            flush()
            cur = {"bssid": m.group(1)}
            continue
        if cur is None:
            continue
        s = line.strip()
        if s.startswith("freq:"):
            cur["freq"] = int(float(s.split(":")[1]))
        elif s.startswith("signal:"):
            cur["rssi"] = float(s.split(":")[1].split()[0])
        elif s.startswith("SSID:"):
            cur["ssid"] = s[5:].strip()
        elif s.startswith("RSN:"):
            cur["sec"] = "WPA2"
    flush()
    return aps


def scan_linux() -> list[dict]:
    if shutil.which("nmcli"):
        out = _run(["nmcli", "-t", "-f", "BSSID,SSID,CHAN,FREQ,SIGNAL,SECURITY",
                    "dev", "wifi", "list", "--rescan", "yes"], timeout=40)
        aps = parse_nmcli(out)
        if aps:
            return aps
    if shutil.which("iw"):
        dev = _run(["iw", "dev"])
        m = re.search(r"Interface\s+(\S+)", dev)
        if not m:
            raise ScanError("ไม่พบอะแดปเตอร์ Wi-Fi")
        return parse_iw(_run(["iw", "dev", m.group(1), "scan"], timeout=40))
    raise ScanError("ต้องมี nmcli (NetworkManager) หรือ iw ติดตั้งอยู่")


# ---------------------------------------------------------------- Windows
def parse_netsh(text: str) -> list[dict]:
    aps = []
    ssid = sec = ""
    cur = None

    def flush():
        if cur and "pct" in cur:
            aps.append(_ap(cur["bssid"], ssid, pct_to_dbm(cur["pct"]),
                           cur.get("channel"), None, sec, cur.get("band")))

    for line in text.splitlines():
        m = re.match(r"^\s*SSID\s+\d+\s*:\s*(.*)$", line)
        if m:
            flush()
            cur, ssid, sec = None, m.group(1).strip(), ""
            continue
        m = re.match(r"^\s*BSSID\s+\d+\s*:\s*(\S+)", line)
        if m:
            flush()
            cur = {"bssid": m.group(1)}
            continue
        m = re.match(r"^\s*Authentication\s*:\s*(.*)$", line)
        if m and cur is None:
            sec = m.group(1).strip()
            continue
        if cur is None:
            continue
        m = re.match(r"^\s*Signal\s*:\s*(\d+)\s*%", line)
        if m:
            cur["pct"] = int(m.group(1))
        m = re.match(r"^\s*Channel\s*:\s*(\d+)", line)
        if m:
            cur["channel"] = int(m.group(1))
        m = re.match(r"^\s*Band\s*:\s*([\d.]+)\s*GHz", line)
        if m:
            b = m.group(1)
            cur["band"] = "2.4" if b.startswith("2") else b
    flush()
    return aps


def scan_windows() -> list[dict]:
    out = _run(["netsh", "wlan", "show", "networks", "mode=bssid"])
    aps = parse_netsh(out)
    if not aps:
        raise ScanError("ไม่พบเครือข่าย (เปิด Wi-Fi และ Location Services แล้วหรือยัง?)")
    return aps


# ---------------------------------------------------------------- macOS
AIRPORT = ("/System/Library/PrivateFrameworks/Apple80211.framework/Versions/"
           "Current/Resources/airport")


def parse_airport(text: str) -> list[dict]:
    rx = re.compile(
        r"^(.*?)\s+([0-9a-fA-F]{1,2}(?::[0-9a-fA-F]{1,2}){5})\s+(-?\d+)\s+(\d+)[,\d+\-]*\s+\S+\s+\S+\s+(.*)$")
    aps = []
    for line in text.splitlines()[1:]:
        m = rx.match(line)
        if m:
            aps.append(_ap(m.group(2), m.group(1).strip(), int(m.group(3)),
                           int(m.group(4)), None, m.group(5).strip()))
    return aps


def parse_system_profiler(text: str) -> list[dict]:
    """macOS 14.4+ ไม่มี airport แล้ว: ใช้ system_profiler (ไม่มี BSSID → ใช้ ssid+ch แทน)"""
    data = json.loads(text)
    aps = []
    for card in data.get("SPAirPortDataType", []):
        for iface in card.get("spairport_airport_interfaces", []):
            nets = list(iface.get("spairport_airport_other_local_wireless_networks", []))
            if "spairport_current_network_information" in iface:
                nets.append(iface["spairport_current_network_information"])
            for n in nets:
                sn = n.get("spairport_signal_noise", "")
                ch = n.get("spairport_network_channel", "")
                ms, mc = re.search(r"(-\d+)\s*dBm", sn), re.match(r"(\d+)", str(ch))
                if not ms:
                    continue
                chan = int(mc.group(1)) if mc else None
                band = "5" if "5GHz" in ch else "6" if "6GHz" in ch else "2.4"
                name = n.get("_name", "")
                bssid = n.get("spairport_network_bssid") or \
                    "00:00:00:00:%02x:%02x" % (sum(map(ord, name)) % 256, (chan or 0) % 256)
                aps.append(_ap(bssid, name, int(ms.group(1)), chan, None,
                               n.get("spairport_security_mode", ""), band))
    return aps


def scan_macos() -> list[dict]:
    try:
        aps = parse_airport(_run([AIRPORT, "-s"]))
        if aps:
            return aps
    except ScanError:
        pass
    aps = parse_system_profiler(_run(["system_profiler", "SPAirPortDataType", "-json"], 60))
    if not aps:
        raise ScanError("ไม่พบเครือข่าย (macOS ต้องอนุญาต Location Services ให้ Terminal/Python)")
    return aps


# ---------------------------------------------------------------- public API
def scan_once() -> list[dict]:
    system = platform.system()
    if system == "Windows":
        return scan_windows()
    if system == "Linux":
        return scan_linux()
    if system == "Darwin":
        return scan_macos()
    raise ScanError(f"ระบบปฏิบัติการ {system} ยังไม่รองรับ")


def average_scans(scans: list[list[dict]]) -> list[dict]:
    """รวมหลายรอบสแกน: เฉลี่ย RSSI ต่อ BSSID (ใน dBm)"""
    groups: dict[str, list[dict]] = defaultdict(list)
    for s in scans:
        for ap in s:
            groups[ap["bssid"]].append(ap)
    out = []
    for items in groups.values():
        best = dict(items[-1])
        best["rssi"] = round(mean(i["rssi"] for i in items), 1)
        out.append(best)
    out.sort(key=lambda a: a["rssi"], reverse=True)
    return out


def scan(samples: int = 1) -> list[dict]:
    samples = max(1, min(int(samples), 10))
    return average_scans([scan_once() for _ in range(samples)])


def scanner_available() -> tuple[bool, str]:
    """ตรวจเร็ว ๆ ว่าเครื่องนี้น่าจะสแกนได้ไหม (ไม่ได้สแกนจริง)"""
    system = platform.system()
    if system == "Windows":
        return (shutil.which("netsh") is not None, "netsh")
    if system == "Linux":
        if shutil.which("nmcli"):
            return True, "nmcli"
        return (shutil.which("iw") is not None, "iw")
    if system == "Darwin":
        return True, "airport/system_profiler"
    return False, system
