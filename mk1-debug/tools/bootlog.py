# Passiver Vollmitschnitt (nur lesen, keine CAN-Requests), z.B. beim Hochfahren der Waermepumpe.
#
# Zeichnet ohne Filter auf:
#   <prefix>_rx.jsonl      jeder Frame aus stiebel-debug/rx (roh + dekodierte Elster-Felder)
#   <prefix>_mqtt.jsonl    alle anderen Nachrichten unter heatingpump/# und stiebel-debug/# (status, availability ...)
#   <prefix>_events.jsonl  Start/Ende, Marker, ESP-Neustart (millis springt zurueck), Ueberlauf
#
# Sniffer-Kommando wird RETAINED gesetzt: startet der ESP neu (z.B. weil er an der Waermepumpe haengt),
# bekommt er es direkt beim MQTT-Connect. Zusaetzlich alle 5 s erneut. Am Ende: retained geloescht, Sniffer aus.
#
# Aufruf:
#   bootlog.py run <minuten> <out_prefix>     Mitschnitt; Ende nach <minuten>, bei Ctrl+C oder Datei <out_prefix>.stop
#   bootlog.py mark "<text>"                   Marker in laufenden Mitschnitt schreiben (z.B. "Strom ein")
import os, sys, time, json, threading
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "mk1-i2c", "gui"))
from mqtt_secrets import load_mqtt_settings
import paho.mqtt.client as mqtt

T_SNIFF = "stiebel-debug/cmd/sniff"
T_MARK = "stiebel-debug/mark"
SNIFF_ON = json.dumps({"on": True, "ids": [], "types": []})

host, port, user, pw = load_mqtt_settings(os.environ.get("STIEBEL_SECRETS") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "mk1-i2c", "secrets.yaml"))


def mark(text):
    c = mqtt.Client(); c.username_pw_set(user, pw)
    c.connect(host, port, 30); c.loop_start()
    c.publish(T_MARK, json.dumps({"text": text, "ts": round(time.time(), 3)})).wait_for_publish(5)
    c.loop_stop(); c.disconnect()
    print("Marker gesendet:", text)


def elster(d):
    b = bytes.fromhex(d)
    if len(b) < 2:
        return {}
    r = {"typ": b[0] & 0x0F, "empf": "0x%03x" % (((b[0] & 0xF0) << 3) | b[1])}
    if len(b) >= 7 and b[2] == 0xFA:
        r["idx"] = "0x%04x" % (b[3] << 8 | b[4]); r["val"] = "0x%04x" % (b[5] << 8 | b[6])
    elif len(b) >= 5:
        r["idx"] = "0x%04x" % b[2]; r["val"] = "0x%04x" % (b[3] << 8 | b[4])
    return r


def run(minutes, prefix):
    os.makedirs(os.path.dirname(os.path.abspath(prefix)), exist_ok=True)
    f_rx = open(prefix + "_rx.jsonl", "a", encoding="utf-8")
    f_mq = open(prefix + "_mqtt.jsonl", "a", encoding="utf-8")
    f_ev = open(prefix + "_events.jsonl", "a", encoding="utf-8")
    stop_file = prefix + ".stop"
    lock = threading.Lock()
    st = {"rx": 0, "tx": 0, "mq": 0, "last_t": None, "last_ovf": None, "boots": 0}

    def event(kind, **kw):
        f_ev.write(json.dumps(dict({"ts": round(time.time(), 3), "ev": kind}, **kw), ensure_ascii=False) + "\n")
        f_ev.flush()
        print(time.strftime("%H:%M:%S"), kind, kw if kw else "", flush=True)

    def on_connect(c, u, fl, rc):
        c.subscribe("stiebel-debug/#"); c.subscribe("heatingpump/#")
        c.publish(T_SNIFF, SNIFF_ON, retain=True)
        event("mqtt_connect", rc=rc)

    def on_disconnect(c, u, rc):
        event("mqtt_disconnect", rc=rc)

    def on_msg(c, u, m):
        now = round(time.time(), 3)
        with lock:
            if m.topic == "stiebel-debug/rx":
                for fr in json.loads(m.payload):
                    t = fr.get("t")
                    if st["last_t"] is not None and t is not None and t + 1000 < st["last_t"]:
                        st["boots"] += 1
                        event("esp_neustart", t_alt=st["last_t"], t_neu=t)
                    if t is not None:
                        st["last_t"] = t
                    rec = {"ts": now, "t": t, "u": fr.get("u"), "id": fr["id"], "d": fr["d"], "tx": fr.get("tx", False)}
                    rec.update(elster(fr["d"]))
                    f_rx.write(json.dumps(rec) + "\n")
                    st["tx" if rec["tx"] else "rx"] += 1
                return
            if m.topic == T_MARK:
                try:
                    event("marker", **json.loads(m.payload))
                except ValueError:
                    event("marker", text=m.payload.decode("utf-8", "replace"))
                return
            if m.topic == T_SNIFF:
                return
            v = m.payload.decode("utf-8", "replace")
            f_mq.write(json.dumps({"ts": now, "topic": m.topic, "retained": bool(m.retain), "v": v},
                                  ensure_ascii=False) + "\n")
            st["mq"] += 1
            if m.topic == "stiebel-debug/status":
                try:
                    sj = json.loads(v)
                    ovf = sj.get("overflow")
                    if st["last_ovf"] is not None and ovf is not None and ovf > st["last_ovf"]:
                        event("ueberlauf", overflow=ovf)
                    st["last_ovf"] = ovf
                    # TWAI-Verluste (ab stiebel-debug mit rx_missed/rx_overrun im Status)
                    for key in ("rx_missed", "rx_overrun"):
                        val = sj.get(key)
                        old = st.setdefault("twai", {}).get(key)
                        if old is not None and val is not None and val > old:
                            event("twai_verlust", feld=key, wert=val, neu=val - old)
                        if val is not None:
                            st["twai"][key] = val
                except ValueError:
                    pass
            elif m.topic.endswith("availability") or m.topic.endswith("/status"):
                if st.setdefault("avail", {}).get(m.topic) != v:
                    st["avail"][m.topic] = v
                    event("verfuegbarkeit", topic=m.topic, v=v)

    c = mqtt.Client(); c.username_pw_set(user, pw)
    c.on_connect = on_connect; c.on_disconnect = on_disconnect; c.on_message = on_msg
    c.connect(host, port, 30); c.loop_start()
    event("start", minuten=minutes, prefix=os.path.basename(prefix))
    end = time.time() + minutes * 60
    n = 0
    try:
        while time.time() < end and not os.path.exists(stop_file):
            time.sleep(1); n += 1
            if n % 5 == 0:
                c.publish(T_SNIFF, SNIFF_ON)
                with lock:
                    f_rx.flush(); f_mq.flush()
            if n % 30 == 0:
                with lock:
                    print(time.strftime("%H:%M:%S"), "rx", st["rx"], "tx", st["tx"], "mqtt", st["mq"],
                          "esp-neustarts", st["boots"], flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        c.publish(T_SNIFF, b"", retain=True).wait_for_publish(5)   # retained loeschen
        c.publish(T_SNIFF, json.dumps({"on": False})).wait_for_publish(5)
        time.sleep(1)
        with lock:
            event("ende", rx=st["rx"], tx=st["tx"], mqtt=st["mq"], esp_neustarts=st["boots"])
        c.loop_stop(); c.disconnect()
        f_rx.close(); f_mq.close(); f_ev.close()
        if os.path.exists(stop_file):
            os.remove(stop_file)


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "run":
        run(float(sys.argv[2]), sys.argv[3])
    elif len(sys.argv) >= 3 and sys.argv[1] == "mark":
        mark(" ".join(sys.argv[2:]))
    else:
        print(__doc__ or "bootlog.py run <minuten> <out_prefix> | bootlog.py mark <text>"); sys.exit(1)
