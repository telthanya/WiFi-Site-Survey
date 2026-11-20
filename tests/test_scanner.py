import json
from wifi_survey import scanner as s

NMCLI = r"""AA\:BB\:CC\:DD\:EE\:01:Home Wifi:6:2437 MHz:80:WPA2
AA\:BB\:CC\:DD\:EE\:02:Home\: 5G:36:5180 MHz:50:WPA2 WPA3
AA\:BB\:CC\:DD\:EE\:03::149:5745 MHz:30:
junk line
"""

NETSH = """
SSID 1 : MyWifi
    Network type            : Infrastructure
    Authentication          : WPA2-Personal
    Encryption              : CCMP
    BSSID 1                 : aa:bb:cc:dd:ee:01
         Signal             : 90%
         Radio type         : 802.11n
         Band               : 2.4 GHz
         Channel            : 6
    BSSID 2                 : aa:bb:cc:dd:ee:02
         Signal             : 60%
         Radio type         : 802.11ac
         Band               : 5 GHz
         Channel            : 44
SSID 2 : Guest
    Authentication          : Open
    BSSID 1                 : aa:bb:cc:dd:ff:01
         Signal             : 40%
         Channel            : 1
"""

AIRPORT = """                            SSID BSSID             RSSI CHANNEL HT CC SECURITY (auth/unicast/group)
                    My Home Wifi aa:bb:cc:dd:ee:01 -55  6       Y  TH WPA2(PSK/AES/AES)
                           Guest a:b:c:d:e:f       -80  36,+1   Y  -- NONE
"""

IW = """BSS aa:bb:cc:dd:ee:01(on wlan0)
\tfreq: 2437.0
\tsignal: -48.00 dBm
\tSSID: Lab
\tRSN:\t * Version: 1
BSS aa:bb:cc:dd:ee:02(on wlan0) -- associated
\tfreq: 5180
\tsignal: -70.50 dBm
\tSSID: Lab5
"""


def test_conversions():
    assert s.pct_to_dbm(100) == -50 and s.pct_to_dbm(0) == -100 and s.pct_to_dbm(60) == -70
    assert s.freq_to_channel(2437) == 6 and s.freq_to_channel(5180) == 36 and s.freq_to_channel(5955) == 1
    assert s.channel_to_freq(36) == 5180 and s.channel_to_freq(6) == 2437
    assert s.freq_to_band(2437) == "2.4" and s.freq_to_band(5745) == "5" and s.freq_to_band(6100) == "6"


def test_nmcli():
    aps = s.parse_nmcli(NMCLI)
    assert len(aps) == 3
    assert aps[0] == {"bssid": "aa:bb:cc:dd:ee:01", "ssid": "Home Wifi", "rssi": -60.0, "channel": 6,
                      "freq": 2437, "band": "2.4", "security": "WPA2"}
    assert aps[1]["ssid"] == "Home: 5G" and aps[1]["band"] == "5"
    assert aps[2]["ssid"] == "" and aps[2]["channel"] == 149


def test_netsh():
    aps = s.parse_netsh(NETSH)
    assert [a["bssid"][-5:] for a in aps] == ["ee:01", "ee:02", "ff:01"]
    assert aps[0]["ssid"] == "MyWifi" and aps[0]["rssi"] == -55 and aps[0]["band"] == "2.4" and aps[0]["channel"] == 6
    assert aps[1]["band"] == "5" and aps[1]["freq"] == 5220
    assert aps[2]["ssid"] == "Guest" and aps[2]["freq"] == 2412


def test_airport():
    aps = s.parse_airport(AIRPORT)
    assert len(aps) == 2
    assert aps[0]["ssid"] == "My Home Wifi" and aps[0]["rssi"] == -55 and aps[0]["channel"] == 6
    assert aps[1]["bssid"] == "0a:0b:0c:0d:0e:0f" and aps[1]["channel"] == 36 and aps[1]["band"] == "5"


def test_system_profiler():
    data = {"SPAirPortDataType": [{"spairport_airport_interfaces": [{
        "spairport_airport_other_local_wireless_networks": [
            {"_name": "Cafe", "spairport_network_channel": "11 (2GHz, 20MHz)",
             "spairport_signal_noise": "-61 dBm / -92 dBm"},
            {"_name": "NoSig"}]}]}]}
    aps = s.parse_system_profiler(json.dumps(data))
    assert len(aps) == 1 and aps[0]["rssi"] == -61 and aps[0]["channel"] == 11 and aps[0]["band"] == "2.4"


def test_iw():
    aps = s.parse_iw(IW)
    assert len(aps) == 2 and aps[0]["rssi"] == -48 and aps[0]["channel"] == 6 and aps[1]["rssi"] == -70.5


def test_average():
    a = {"bssid": "aa:aa:aa:aa:aa:aa", "ssid": "x", "rssi": -50.0, "channel": 1, "freq": 2412, "band": "2.4", "security": ""}
    b = dict(a, rssi=-60.0)
    c = dict(a, bssid="bb:bb:bb:bb:bb:bb", rssi=-40.0)
    out = s.average_scans([[a, c], [b]])
    assert out[0]["bssid"].startswith("bb") and out[1]["rssi"] == -55.0
