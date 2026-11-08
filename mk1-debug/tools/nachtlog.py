# Passiver Langzeit-Mitschnitt (nur lesen, keine CAN-Requests) fuer todo.md 2026-10-31:
#  - alle HA-States unter heatingpump/# bei Aenderung (Energie Summe/Tag, VERDICHTER, PUMPENSTATUS ...)
#  - Roh-Antworten (Typ 2) ausgewaehlter Indizes aus stiebel-debug/rx (Sniffer, alle 60 s neu eingeschaltet,
#    damit er einen Neustart/OTA der Firmware ueberlebt)
# Aufruf: nachtlog.py <stunden> <out_prefix>  ->  <out_prefix>_states.jsonl, <out_prefix>_rx.jsonl
import os, sys, time, json, threading
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "mk1-i2c", "gui"))
from mqtt_secrets import load_mqtt_settings
import paho.mqtt.client as mqtt

WATCH = {0x07a8, 0x01d2, 0xfdad, 0x0a20, 0x0176, 0x4eef, 0x07a3, 0x07a4, 0x07a5, 0x07a9, 0x0822, 0x0833, 0x05d7}
WATCH |= set(range(0x091a, 0x0932))  # Energie-Register

host, port, user, pw = load_mqtt_settings(os.environ.get("STIEBEL_SECRETS") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "mk1-i2c", "secrets.yaml"))
hours = float(sys.argv[1]); prefix = sys.argv[2]
f_st = open(prefix + "_states.jsonl", "a", encoding="utf-8")
f_rx = open(prefix + "_rx.jsonl", "a", encoding="utf-8")
last = {}
cnt = {"st": 0, "rx": 0}
lock = threading.Lock()

def decode(d):
    b = bytes.fromhex(d)
    if len(b) < 5 or (b[0] & 0x0F) != 2:
        return None
    if b[2] == 0xFA:
        return (b[3] << 8 | b[4], b[5] << 8 | b[6]) if len(b) >= 7 else None
    return b[2], b[3] << 8 | b[4]

def sniff_on(c):
    c.publish("stiebel-debug/cmd/sniff", json.dumps({"on": True}))

def on_connect(c, u, fl, rc):
    c.subscribe("heatingpump/#"); c.subscribe("stiebel-debug/rx"); sniff_on(c)

def on_msg(c, u, m):
    now = time.time()
    with lock:
        if m.topic == "stiebel-debug/rx":
            for fr in json.loads(m.payload):
                if fr.get("tx"):
                    continue
                r = decode(fr["d"])
                if r and r[0] in WATCH:
                    f_rx.write(json.dumps({"ts": round(now, 1), "u": fr.get("u"), "id": fr["id"],
                                           "idx": "0x%04x" % r[0], "raw": "0x%04x" % r[1]}) + "\n")
                    cnt["rx"] += 1
        elif m.topic.endswith("/state") or m.topic.endswith("/attr"):
            v = m.payload.decode("utf-8", "replace")
            if last.get(m.topic) != v:
                last[m.topic] = v
                f_st.write(json.dumps({"ts": round(now, 1), "topic": m.topic, "v": v}, ensure_ascii=False) + "\n")
                cnt["st"] += 1

c = mqtt.Client(); c.username_pw_set(user, pw)
c.on_connect = on_connect; c.on_message = on_msg
c.connect(host, port, 30); c.loop_start()
end = time.time() + hours * 3600
try:
    while time.time() < end:
        time.sleep(60)
        sniff_on(c)
        with lock:
            f_st.flush(); f_rx.flush()
            print(time.strftime("%H:%M"), "states", cnt["st"], "rx", cnt["rx"], flush=True)
finally:
    c.publish("stiebel-debug/cmd/sniff", json.dumps({"on": False})).wait_for_publish(5)
    time.sleep(1); c.loop_stop(); f_st.close(); f_rx.close()
