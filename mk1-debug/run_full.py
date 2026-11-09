"""Kompletter Automatik-Lauf: Debug-Firmware flashen, Auto-Scan, Produktiv-Firmware zurueckflashen, auswerten.

Aufruf (aus PowerShell oder Git-Bash):
    C:\\temp\\stiebel-Remote\\.venv\\Scripts\\python.exe -I C:\\temp\\stiebel-Remote\\mk1-debug\\run_full.py [--smoke] [--delay 100]

Die Produktiv-Firmware wird IMMER zurueckgeflasht (finally), auch bei Abbruch oder Fehler im Scan.
Vor dem Rueckflash wird geprueft, dass das Mk1-Binary dem Backup entspricht (SHA256).
"""

import argparse
import datetime as dt
import glob
import hashlib
import os
import socket
import subprocess
import sys
import time

# Maintainer-Skript (Monorepo-Layout der Arbeitsumgebung). Pfade/Geraet per Umgebungsvariable.
ROOT = os.environ.get("STIEBEL_ROOT", r"C:\temp\stiebel-Remote")
VENV_PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
ESPHOME = os.path.join(ROOT, ".venv", "Scripts", "esphome.exe")
DEBUG_DIR = os.path.dirname(os.path.abspath(__file__))
MK1_DIR = os.path.join(ROOT, "esp32-s3-technik-mk1")
MK1_BIN = os.path.join(MK1_DIR, ".esphome", "build", "esp32-s3-technik-mk1", "build", "firmware.ota.bin")
BACKUP_BIN = os.path.join(ROOT, "backup", "mk1-produktiv-rxstream-fw-2026-10-21", "firmware.ota.bin")
BACKUP_SHA256 = "66613E3079CEDBC0813A2BCC859F22211CF38B00B516DAFB84B3B57D4BB92072"
DEVICE = os.environ.get("MK1_IP", "")  # IP des Mk1
LOG_DIR = os.environ.get("STIEBEL_LOG_DIR") or os.path.join(DEBUG_DIR, "logs")

_log_file = None


def log(msg: str) -> None:
    line = f"{dt.datetime.now().isoformat(timespec='seconds')} {msg}"
    try:
        print(line, flush=True)
    except (UnicodeEncodeError, OSError):
        # Konsole ohne UTF-8 (cp1252): Logging darf den Lauf nie abbrechen
        print(line.encode("ascii", "replace").decode("ascii"), flush=True)
    if _log_file:
        _log_file.write(line + "\n")
        _log_file.flush()


def esphome_env() -> dict:
    # ESP-IDF lehnt MSYS/Mingw ab (Git-Bash setzt MSYSTEM).
    env = dict(os.environ)
    env.pop("MSYSTEM", None)
    return env


def run(cmd: list, cwd: str, timeout: int) -> int:
    log(f"RUN {' '.join(os.path.basename(c) if i == 0 else c for i, c in enumerate(cmd))} (cwd={cwd})")
    proc = subprocess.run(cmd, cwd=cwd, env=esphome_env(), timeout=timeout,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                          encoding="utf-8", errors="replace")
    tail = proc.stdout.strip().splitlines()[-12:]
    for t in tail:
        log("  | " + t)
    log(f"EXIT {proc.returncode}")
    return proc.returncode


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def wait_port(host: str, port: int, timeout_s: int) -> bool:
    end = time.time() + timeout_s
    while time.time() < end:
        try:
            with socket.create_connection((host, port), timeout=3):
                return True
        except OSError:
            time.sleep(3)
    return False


def flash_debug() -> bool:
    rc = run([ESPHOME, "compile", "mk1-debug.yaml"], DEBUG_DIR, 1800)
    if rc != 0:
        return False
    rc = run([ESPHOME, "upload", "mk1-debug.yaml", "--device", DEVICE], DEBUG_DIR, 600)
    return rc == 0


def restore_production() -> bool:
    if not os.path.exists(MK1_BIN) or sha256(MK1_BIN) != BACKUP_SHA256:
        log(f"FEHLER: {MK1_BIN} fehlt oder SHA256 weicht vom Backup ab. Rueckflash NICHT automatisch.")
        log(f"Backup liegt in {BACKUP_BIN}. Bitte manuell flashen.")
        return False
    for attempt in range(1, 4):
        log(f"Rueckflash Produktiv-Firmware, Versuch {attempt}")
        rc = run([ESPHOME, "upload", "esp32-s3-technik-mk1.yaml", "--device", DEVICE], MK1_DIR, 600)
        if rc == 0:
            ok = wait_port(DEVICE, 6053, 180)
            log(f"Mk1 API-Port 6053 erreichbar: {ok}")
            return True
        time.sleep(20)
    return False


def newest_state(since: float) -> str | None:
    files = [f for f in glob.glob(os.path.join(LOG_DIR, "auto_*_state.json")) if os.path.getmtime(f) >= since]
    return max(files, key=os.path.getmtime) if files else None


def main() -> int:
    global _log_file
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--delay", type=int, default=100)
    ap.add_argument("--resume", default=None)
    ap.add_argument("--skip-flash", action="store_true",
                    help="Debug-Firmware laeuft schon auf dem Geraet: nicht neu bauen/flashen (nur mit --resume sinnvoll)")
    ap.add_argument("--d-members", default=None, help="an auto_runner durchgereicht")
    ap.add_argument("--extra-members", default=None, help="an auto_runner durchgereicht")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    os.makedirs(LOG_DIR, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    _log_file = open(os.path.join(LOG_DIR, f"run_{stamp}_orchestrator.log"), "a", encoding="utf-8")
    t_start = time.time()
    scan_rc = None
    restored = False
    try:
        if sha256(BACKUP_BIN) != BACKUP_SHA256:
            log("FEHLER: Backup-Binary beschaedigt, Abbruch vor dem Flashen.")
            return 5
        if args.skip_flash:
            log("Schritt 1 uebersprungen (--skip-flash): Debug-Firmware laeuft bereits")
            if not wait_port(DEVICE, 6053, 60):
                log("Geraet nicht erreichbar")
                return 6
        else:
            log("Schritt 1: Debug-Firmware bauen und flashen")
            if not flash_debug():
                log("Flash der Debug-Firmware fehlgeschlagen")
                return 6
        log("Schritt 2: Auto-Scan")
        cmd = [VENV_PY, "-I", os.path.join(DEBUG_DIR, "gui", "auto_runner.py"),
               "--out", LOG_DIR, "--secrets", os.path.join(ROOT, "esp32-s3-technik-mk1", "secrets.yaml"),
               "--delay", str(args.delay)]
        if args.smoke:
            cmd.append("--smoke")
        if args.resume:
            cmd += ["--resume", args.resume]
        if args.d_members:
            cmd += ["--d-members", args.d_members]
        if args.extra_members:
            cmd += ["--extra-members", args.extra_members]
        log("RUN auto_runner " + " ".join(cmd[3:]))
        scan_rc = subprocess.call(cmd, cwd=os.path.join(DEBUG_DIR, "gui"))
        log(f"auto_runner Exit-Code {scan_rc}")
    except KeyboardInterrupt:
        log("Abbruch durch Ctrl+C")
    except Exception as exc:  # noqa: BLE001 - Rueckflash muss in jedem Fall laufen
        log(f"Fehler: {exc!r}")
    finally:
        log("Schritt 3: Produktiv-Firmware zurueckflashen")
        restored = restore_production()
        log(f"Rueckflash erfolgreich: {restored}")

    state = newest_state(t_start)
    if state:
        log(f"Schritt 4: Auswertung {state}")
        subprocess.call([VENV_PY, "-I", os.path.join(DEBUG_DIR, "gui", "type_guess.py"), state],
                        cwd=os.path.join(DEBUG_DIR, "gui"))
    else:
        log("Keine State-Datei gefunden, keine Auswertung")
    log(f"Fertig. scan_rc={scan_rc} restored={restored} dauer={(time.time() - t_start) / 3600:.2f} h")
    return 0 if (scan_rc == 0 and restored) else 1


if __name__ == "__main__":
    sys.exit(main())
