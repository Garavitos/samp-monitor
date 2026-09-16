#!/usr/bin/env python3
"""
monitor_once.py
----------------
Single-shot version of the SA-MP server status check, meant to be run
repeatedly by a scheduler (e.g. a GitHub Actions cron workflow) rather
than looping forever itself. Each run:
 
  1. Sends one query to the server using the public SA-MP/open.mp
     query protocol (same one the in-game server browser uses).
  2. Appends one row to samp_log.csv (creates it with a header if it
     doesn't exist yet).
  3. Compares against the previous row already in the CSV to flag a
     likely restart (sudden player-count drop, or server that was
     offline last run and is back now / was online and is offline now).
 
This has zero dependencies beyond the Python standard library.
"""
 
import csv
import os
import random
import socket
import struct
import sys
import time
from datetime import datetime, timezone
 
HOST = os.environ.get("SAMP_HOST", "samp.lsmiestelis.lt")
PORT = int(os.environ.get("SAMP_PORT", "7777"))
LOG_PATH = os.environ.get("SAMP_LOG", "samp_log.csv")
DROP_RATIO = float(os.environ.get("SAMP_DROP_RATIO", "0.5"))
 
CSV_FIELDS = ["timestamp_utc", "online", "players", "maxplayers", "ping_ms",
              "hostname", "gamemode", "weather", "worldtime", "note"]
 
 
def _header(ip_bytes, port, opcode):
    return b"SAMP" + ip_bytes + struct.pack("<H", port) + opcode.encode("ascii")
 
 
def _send_recv(sock, ip_str, ip_bytes, port, opcode, extra=b"", timeout=3.0):
    packet = _header(ip_bytes, port, opcode) + extra
    sock.settimeout(timeout)
    sock.sendto(packet, (ip_str, port))
    data, _addr = sock.recvfrom(4096)
    return data[11:]
 
 
def get_info(sock, ip_str, ip_bytes, port):
    payload = _send_recv(sock, ip_str, ip_bytes, port, "i")
    offset = 1  # skip password byte
    players, maxplayers = struct.unpack_from("<HH", payload, offset)
    offset += 4
    hostname_len = struct.unpack_from("<I", payload, offset)[0]
    offset += 4
    hostname = payload[offset:offset + hostname_len].decode("utf-8", "replace")
    offset += hostname_len
    gamemode_len = struct.unpack_from("<I", payload, offset)[0]
    offset += 4
    gamemode = payload[offset:offset + gamemode_len].decode("utf-8", "replace")
    offset += gamemode_len
    return {"players": players, "maxplayers": maxplayers,
            "hostname": hostname, "gamemode": gamemode}
 
 
def get_rules(sock, ip_str, ip_bytes, port):
    payload = _send_recv(sock, ip_str, ip_bytes, port, "r")
    offset = 0
    count = struct.unpack_from("<H", payload, offset)[0]
    offset += 2
    rules = {}
    for _ in range(count):
        name_len = payload[offset]; offset += 1
        name = payload[offset:offset + name_len].decode("utf-8", "replace"); offset += name_len
        val_len = payload[offset]; offset += 1
        val = payload[offset:offset + val_len].decode("utf-8", "replace"); offset += val_len
        rules[name] = val
    return rules
 
 
def get_ping_ms(sock, ip_str, ip_bytes, port):
    token = struct.pack("<I", random.randint(0, 2 ** 32 - 1))
    t0 = time.time()
    payload = _send_recv(sock, ip_str, ip_bytes, port, "p", extra=token)
    t1 = time.time()
    if payload[:4] != token:
        return None
    return round((t1 - t0) * 1000, 1)
 
 
def check_once():
    try:
        ip_str = socket.gethostbyname(HOST)
        ip_bytes = socket.inet_aton(ip_str)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        info = get_info(sock, ip_str, ip_bytes, PORT)
        ping_ms = get_ping_ms(sock, ip_str, ip_bytes, PORT)
        try:
            rules = get_rules(sock, ip_str, ip_bytes, PORT)
        except Exception:
            rules = {}
        sock.close()
        return {
            "online": True, "players": info["players"], "maxplayers": info["maxplayers"],
            "ping_ms": ping_ms, "hostname": info["hostname"], "gamemode": info["gamemode"],
            "weather": rules.get("weather", ""), "worldtime": rules.get("worldtime", ""),
        }
    except Exception:
        return {"online": False, "players": "", "maxplayers": "", "ping_ms": "",
                "hostname": "", "gamemode": "", "weather": "", "worldtime": ""}
 
 
def read_last_row(path):
    if not os.path.isfile(path):
        return None
    with open(path, "r", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return rows[-1] if rows else None
 
 
def main():
    result = check_once()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
 
    last = read_last_row(LOG_PATH)
    note = ""
    if last is not None:
        last_online = last.get("online") == "True"
        last_players = last.get("players", "")
        if result["online"]:
            if last_online is False:
                note = "RECOVERED (was offline last check) - likely restart just finished"
            elif last_players not in ("", None):
                try:
                    lp = int(last_players)
                    if lp > 3 and result["players"] <= lp * DROP_RATIO:
                        note = f"DROP {lp}->{result['players']} - likely restart (RR)"
                except ValueError:
                    pass
        else:
            if last_online is True:
                note = "WENT OFFLINE (no query response) - server likely restarting"
 
    row = {
        "timestamp_utc": now,
        "online": result["online"],
        "players": result["players"],
        "maxplayers": result["maxplayers"],
        "ping_ms": result["ping_ms"],
        "hostname": result["hostname"],
        "gamemode": result["gamemode"],
        "weather": result["weather"],
        "worldtime": result["worldtime"],
        "note": note,
    }
 
    file_exists = os.path.isfile(LOG_PATH)
    with open(LOG_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)
 
    status = "ONLINE" if result["online"] else "OFFLINE"
    print(f"{now} UTC  {status}  players={result['players']}  ping={result['ping_ms']}ms  {note}")
 
 
if __name__ == "__main__":
    main()
