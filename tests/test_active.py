import pytest
from wifi_survey import active as a
from wifi_survey.scanner import ScanError

NETSH_IF = """
    Name                   : Wi-Fi
    State                  : connected
    SSID                   : MyWifi
    BSSID                  : aa:bb:cc:dd:ee:01
    Network type           : Infrastructure
    Radio type             : 802.11ac
    Channel                : 44
    Receive rate (Mbps)    : 585
    Transmit rate (Mbps)   : 433.3
    Signal                 : 88%
"""

IW_LINK = """Connected to aa:bb:cc:dd:ee:02 (on wlan0)
\tSSID: Lab
\tfreq: 5180
\tsignal: -52 dBm
\trx bitrate: 866.7 MBit/s VHT-MCS 9
\ttx bitrate: 650.0 MBit/s VHT-MCS 7
"""

NMCLI = "no:AA\\:BB\\:CC\\:DD\\:EE\\:01:Other:1:2412 MHz:130 Mbit/s:40\nyes:AA\\:BB\\:CC\\:DD\\:EE\\:02:Lab:36:5180 MHz:540 Mbit/s:80\n"

PING_WIN = """Pinging 192.168.1.1 with 32 bytes of data:
Reply from 192.168.1.1: bytes=32 time=3ms TTL=64
Reply from 192.168.1.1: bytes=32 time<1ms TTL=64
Request timed out.
Reply from 192.168.1.1: bytes=32 time=5ms TTL=64
"""

PING_UNIX = """64 bytes from 10.0.0.1: icmp_seq=1 ttl=64 time=2.10 ms
64 bytes from 10.0.0.1: icmp_seq=2 ttl=64 time=4.10 ms
"""


def test_netsh_interfaces():
    l = a.parse_netsh_interfaces(NETSH_IF)
    assert l["ssid"] == "MyWifi" and l["bssid"] == "aa:bb:cc:dd:ee:01" and l["channel"] == 44
    assert l["rx_rate"] == 585 and l["tx_rate"] == 433.3 and l["rssi"] == -56.0 and l["band"] == "5"


def test_netsh_disconnected():
    with pytest.raises(ScanError):
        a.parse_netsh_interfaces("    State : disconnected\n    SSID :\n")


def test_iw_link():
    l = a.parse_iw_link(IW_LINK)
    assert l["rssi"] == -52 and l["rx_rate"] == 866.7 and l["tx_rate"] == 650.0
    assert l["channel"] == 36 and l["band"] == "5" and l["ssid"] == "Lab"
    with pytest.raises(ScanError):
        a.parse_iw_link("Not connected.")


def test_nmcli_active():
    l = a.parse_nmcli_active(NMCLI)
    assert l["ssid"] == "Lab" and l["bssid"] == "aa:bb:cc:dd:ee:02" and l["rssi"] == -60.0
    with pytest.raises(ScanError):
        a.parse_nmcli_active("no:AA\\:BB\\:CC\\:DD\\:EE\\:01:Other:1:2412 MHz:130 Mbit/s:40")


def test_ping_parse():
    p = a.parse_ping(PING_WIN, 4, "192.168.1.1")
    assert p["received"] == 3 and p["loss"] == 25.0 and p["min"] == 0.5 and p["max"] == 5.0
    assert p["avg"] == 2.83 and p["jitter"] == 3.5 and p["host"] == "192.168.1.1"
    u = a.parse_ping(PING_UNIX, 2, "10.0.0.1")
    assert u["loss"] == 0.0 and u["avg"] == 3.1 and u["jitter"] == 2.0
    z = a.parse_ping("Request timed out.", 4, "x")
    assert z["loss"] == 100.0 and z["avg"] is None


def test_gateway():
    win = "  Network Destination        Netmask          Gateway       Interface  Metric\n" \
          "          0.0.0.0          0.0.0.0      192.168.1.1   192.168.1.50     55\n" \
          "          0.0.0.0          0.0.0.0       10.0.0.1       10.0.0.9      25\n"
    assert a.parse_default_gateway(win, "Windows") == "10.0.0.1"
    assert a.parse_default_gateway("default via 10.1.1.1 dev wlan0 proto dhcp", "Linux") == "10.1.1.1"
    assert a.parse_default_gateway("   gateway: 172.16.0.1\n", "Darwin") == "172.16.0.1"
    assert a.parse_default_gateway("", "Linux") is None


@pytest.mark.parametrize("bad", ["", "-c5", "a b", "x;rm", "$(id)"])
def test_ping_rejects_bad_host(bad):
    with pytest.raises(ScanError):
        a.ping(bad)
