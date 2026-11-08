"""Antwortzeit je CAN-Member aus auto_*_rx.jsonl (TX-Request -> erste passende Antwort des Members)."""
import glob
import json
import os
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
LOGS = os.path.join(os.path.dirname(HERE), "logs")
WINDOW_MS = 2000


def _key(d):
    return bytes(d[2:5]) if d[2] in (0xF7, 0xF8, 0xF9, 0xFA) else bytes(d[2:3])


def _times(r):
    """-> (t_ms, u_ms oder None). u nur wenn > 0 (alte Logs haben kein u)."""
    u = int(r.get("u") or 0)
    return int(r["t"]), (u if u > 0 else None)


def _dt(a, b):
    """Differenz in ms von a nach b. u nur, wenn beide ein u haben; sonst beide mit t."""
    if a[1] is not None and b[1] is not None:
        return b[1] - a[1]
    return b[0] - a[0]


def latencies(files=None):
    """-> {member: sortierte Liste ms}. Unbeantwortete Requests zaehlen separat in sent."""
    files = files or sorted(glob.glob(os.path.join(LOGS, "auto_*_rx.jsonl")))
    lat = defaultdict(list)
    sent = defaultdict(int)
    for path in files:
        frames = []
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                    frames.append((_times(r), int(r["id"], 16), bytes.fromhex(r["d"]), bool(r["tx"])))
                except (ValueError, KeyError, TypeError):
                    continue
        for i, (tm, _id, d, tx) in enumerate(frames):
            if not tx or len(d) < 5 or (d[0] & 0x0F) != 1:
                continue
            tgt = ((d[0] & 0xF0) << 3) | d[1]
            key = _key(d)
            sent[tgt] += 1
            for j in range(i + 1, min(i + 600, len(frames))):
                tm2, id2, d2, tx2 = frames[j]
                if _dt(tm, tm2) > WINDOW_MS:
                    break
                if tx2:
                    if len(d2) >= 5 and (d2[0] & 0x0F) == 1 and _key(d2) == key and ((d2[0] & 0xF0) << 3 | d2[1]) == tgt:
                        break
                    continue
                if id2 == tgt and len(d2) >= 4 and (d2[0] & 0x0F) == 2 and _key(d2) == key:
                    lat[tgt].append(_dt(tm, tm2))
                    break
    return {m: sorted(v) for m, v in lat.items()}, dict(sent)


def stats(v, n_sent):
    if not v:
        return None
    return {"n": len(v), "sent": n_sent, "quote": round(100 * len(v) / n_sent) if n_sent else 0,
            "med": v[len(v) // 2], "p90": v[int(len(v) * 0.9)], "p99": v[int(len(v) * 0.99)], "max": v[-1]}
