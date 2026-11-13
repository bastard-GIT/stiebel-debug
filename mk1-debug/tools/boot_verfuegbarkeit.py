"""Verfuegbarkeit der Elster-Indizes im Boot-Fenster: welche Indizes (Sender, Index) mit Typ 0 an 0x100
im Zeitfenster antworten, mit erstem und letztem Wert, Zuordnung zu den Bloecken und Namen aus ElsterTable.h.
Aufruf: python -I boot_verfuegbarkeit.py <rx.jsonl> <ElsterTable.h> <ausgabe_praefix> [--start U] [--ende U]
Ausgabe: <praefix>.csv (utf-8-sig, Trenner ;, CRLF) und <praefix>.md. Bestehende Dateien werden nicht ueberschrieben.
Liest nur Eingaben, nur Standardbibliothek.
"""
import argparse
import csv
import json
import os
import re
import sys
from collections import defaultdict

WP_EIN_U = 1794556046112        # Unix-ms WP-ein, Bezugspunkt fuer die Sekunden im Bericht
START_VOR = 1794556062700       # Vorgabe Fensterbeginn (Unix-ms, inklusive)
ENDE_VOR = 1794556080000        # Vorgabe Fensterende (Unix-ms, inklusive)
EMPF_ZIEL = 0x100               # Empfaenger der Frames (Steuerung)
TYP_ZIEL = 0                    # nur Typ-0-Frames

# Block-Starts (dezimal). Die Bereiche sind disjunkt, die Zuordnung ist daher eindeutig.
BLOECKE = [40000, 40900, 41000, 44000, 46000, 48000]
BLOCK_ENDE = {40000: 40899, 40900: 40999, 41000: 41999, 44000: 45999, 46000: 47999, 48000: 48999}
SONSTIGE = "sonstige"

RE_ZEILE = re.compile(r'^\s*\{\s*"([^"]+)"\s*,\s*0x([0-9a-fA-F]+)\s*,')


def zahl(v):
    """Feld als int. Akzeptiert int oder String ('0x100', '256'). Sonst None."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    s = str(v).strip()
    for basis in (0, 10):
        try:
            return int(s, basis)
        except ValueError:
            pass
    return None


def block_of(idx):
    """Block zu einem Index (dezimal). Ausserhalb aller Bereiche: SONSTIGE."""
    for start in BLOECKE:
        if start <= idx <= BLOCK_ENDE[start]:
            return start
    return SONSTIGE


def lade_namen(pfad):
    """Index -> Name aus ElsterTable.h. Bei doppeltem Index gewinnt der erste Name."""
    namen = {}
    with open(pfad, encoding="utf-8", errors="replace") as f:
        for zeile in f:
            m = RE_ZEILE.match(zeile)
            if m:
                namen.setdefault(int(m.group(2), 16), m.group(1))
    return namen


def lade(pfad, start, ende):
    """Liest rx.jsonl. Liefert (Treffer, Frames im Fenster gesamt, unvollstaendig, Fehlzeilen)."""
    treffer = []                # (u, zeilennr, sender, idx, val)
    im_fenster = 0
    unvollst = 0
    fehler = 0
    with open(pfad, encoding="utf-8") as f:
        for nr, zeile in enumerate(f):
            zeile = zeile.strip()
            if not zeile:
                continue
            try:
                fr = json.loads(zeile)
            except ValueError:
                fehler += 1
                continue
            if not isinstance(fr, dict):
                fehler += 1
                continue
            u = zahl(fr.get("u"))
            if u is None or not start <= u <= ende:
                continue
            im_fenster += 1
            if zahl(fr.get("typ")) != TYP_ZIEL or zahl(fr.get("empf")) != EMPF_ZIEL:
                continue
            sender = zahl(fr.get("id"))
            idx = zahl(fr.get("idx"))
            val = zahl(fr.get("val"))
            if sender is None or idx is None or val is None:
                unvollst += 1
                continue
            treffer.append((u, nr, sender, idx, val))
    return treffer, im_fenster, unvollst, fehler


def aggregiere(treffer):
    """Je Paar (Sender, Index): erster und letzter Wert in Zeitreihenfolge nach u, Anzahl Frames."""
    gruppen = defaultdict(list)     # (sender, idx) -> [(u, zeilennr, val)]
    for u, nr, sender, idx, val in treffer:
        gruppen[(sender, idx)].append((u, nr, val))
    zeilen = []
    for (sender, idx), liste in gruppen.items():
        liste.sort()
        zeilen.append({"block": block_of(idx), "idx": idx, "sender": sender,
                       "erster": liste[0][2], "letzter": liste[-1][2], "n": len(liste)})
    zeilen.sort(key=lambda z: (z["idx"], z["sender"]))
    return zeilen


def md_zeile(cells):
    return "| " + " | ".join(str(c).replace("|", "/") for c in cells) + " |"


def main():
    ap = argparse.ArgumentParser(description="Verfuegbarkeit Elster-Indizes im Boot-Fenster (Typ 0 an 0x100).")
    ap.add_argument("rx", help="rx.jsonl (eine JSON-Zeile je Frame)")
    ap.add_argument("tabelle", help="ElsterTable.h (nur Lesen, Namen)")
    ap.add_argument("praefix", help="Ausgabe-Praefix ohne Endung; erzeugt .csv und .md")
    ap.add_argument("--start", type=int, default=START_VOR, help="Fensterbeginn Unix-ms, inklusive")
    ap.add_argument("--ende", type=int, default=ENDE_VOR, help="Fensterende Unix-ms, inklusive")
    a = ap.parse_args()
    if a.start > a.ende:
        ap.error("--start liegt nach --ende")

    csv_pfad = a.praefix + ".csv"
    md_pfad = a.praefix + ".md"
    for p in (csv_pfad, md_pfad):
        if os.path.exists(p):
            sys.exit("Ausgabe existiert schon, wird nicht ueberschrieben: " + p)

    namen = lade_namen(a.tabelle)
    treffer, im_fenster, unvollst, fehler = lade(a.rx, a.start, a.ende)
    zeilen = aggregiere(treffer)
    for z in zeilen:
        z["name"] = namen.get(z["idx"], "")

    # ---- CSV: alle Zeilen (Bloecke und Sonstige), sortiert nach Index, dann Sender ----
    with open(csv_pfad, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";", lineterminator="\r\n")
        w.writerow(["block", "index_hex", "index_dez", "sender", "erster_wert", "letzter_wert",
                    "anzahl_frames", "name"])
        for z in zeilen:
            w.writerow([str(z["block"]), "0x%04x" % z["idx"], z["idx"], "0x%03x" % z["sender"],
                        "0x%04x" % z["erster"], "0x%04x" % z["letzter"], z["n"], z["name"]])

    # ---- Zaehlung je Block (je Paar (Sender, Index)) ----
    def zaehl(block):
        paare = [z for z in zeilen if z["block"] == block]
        idxe = sorted({z["idx"] for z in paare})
        cnt = {"0x0001": 0, "0x0000": 0, "0x8000": 0, "anderer": 0}
        for z in paare:
            key = {1: "0x0001", 0: "0x0000", 0x8000: "0x8000"}.get(z["letzter"], "anderer")
            cnt[key] += 1
        rng = "-" if not idxe else "%s..%s (%d..%d)" % ("0x%04x" % idxe[0], "0x%04x" % idxe[-1], idxe[0], idxe[-1])
        frames = sum(z["n"] for z in paare)
        return paare, len(paare), len(idxe), cnt, rng, frames

    bloecke = BLOECKE + [SONSTIGE]
    zaehlung = {b: zaehl(b) for b in bloecke}
    sonst_frames = zaehlung[SONSTIGE][5]

    # ---- MD ----
    L = []
    L.append("# Verfuegbarkeit Elster-Indizes im Boot-Fenster (Typ 0 an 0x100)")
    L.append("")
    L.append("## Quelle")
    L.append("")
    L.append("- Mitschnitt: `" + os.path.basename(a.rx) + "` (je Zeile ein Frame)")
    L.append("- Namen: `" + os.path.basename(a.tabelle) + "` (nur Lesen)")
    L.append("- Skript: `repos/stiebel-debug/mk1-debug/tools/boot_verfuegbarkeit.py` (Aufruf `python -I`)")
    L.append("")
    L.append("## Zeitfenster")
    L.append("")
    L.append("- u von %d bis %d (beide inklusive)" % (a.start, a.ende))
    L.append("- relativ zu WP-ein (u=%d): +%.3f s bis +%.3f s" % (
        WP_EIN_U, (a.start - WP_EIN_U) / 1000.0, (a.ende - WP_EIN_U) / 1000.0))
    L.append("- Frames im Fenster gesamt: %d; davon Filter-Treffer (typ 0, empf 0x100, idx und val vorhanden): %d"
             % (im_fenster, len(treffer)))
    L.append("- verworfen: unvollstaendig (id/idx/val fehlt bzw. nicht lesbar) %d; Fehlzeilen (kein JSON) %d"
             % (unvollst, fehler))
    L.append("")
    L.append("## Zaehlweise")
    L.append("")
    L.append("- Eine Zaehlung je Paar (Sender, Index). Derselbe Index kann je Sender eigene Werte haben und zaehlt daher je Sender.")
    L.append("- Spalte \"Indizes eindeutig\" zaehlt eindeutige Indexwerte ueber alle Sender.")
    L.append("- Letzter Wert = Wert des zeitlich letzten Frames im Fenster (Sortierung nach u) fuer das Paar (Sender, Index).")
    L.append("- Block-Zuordnung ueber die Bereiche 40000..40899, 40900..40999, 41000..41999, 44000..45999, 46000..47999, 48000..48999 (dezimal). Ausserhalb: sonstige.")
    L.append("")
    L.append("## Zaehlung je Block")
    L.append("")
    L.append(md_zeile(["Block", "Paare (Sender, Index)", "Indizes eindeutig", "letzter Wert 0x0001",
                       "letzter Wert 0x0000", "letzter Wert 0x8000", "letzter Wert anderer",
                       "beobachteter Index min..max", "Frames"]))
    L.append(md_zeile(["---"] * 9))
    for b in bloecke:
        _, np, ni, cnt, rng, frames = zaehlung[b]
        L.append(md_zeile([b if b != SONSTIGE else "sonstige", np, ni, cnt["0x0001"], cnt["0x0000"],
                           cnt["0x8000"], cnt["anderer"], rng, frames]))
    L.append("")
    L.append("## Indizes mit letztem Wert 0x0001")
    L.append("")
    L.append("Kandidaten fuer den Abgleich mit Menuepunkten am Display.")
    L.append("")
    l1 = [z for z in zeilen if z["letzter"] == 1]
    L.append(md_zeile(["Block", "Index hex", "Index dez", "Sender", "Name"]))
    L.append(md_zeile(["---"] * 5))
    for z in l1:
        L.append(md_zeile([z["block"], "0x%04x" % z["idx"], z["idx"], "0x%03x" % z["sender"], z["name"]]))
    if not l1:
        L.append("(keine)")
    L.append("")

    def tabelle(rows):
        L.append(md_zeile(["Index hex", "Index dez", "Sender", "Erster Wert", "Letzter Wert", "Frames", "Name"]))
        L.append(md_zeile(["---"] * 7))
        for z in rows:
            L.append(md_zeile(["0x%04x" % z["idx"], z["idx"], "0x%03x" % z["sender"], "0x%04x" % z["erster"],
                               "0x%04x" % z["letzter"], z["n"], z["name"]]))
        if not rows:
            L.append("(keine)")
        L.append("")

    L.append("## Bloecke")
    L.append("")
    for b in BLOECKE:
        L.append("### Block %d" % b)
        L.append("")
        tabelle(zaehlung[b][0])
    L.append("## Sonstige Werte im Burst")
    L.append("")
    L.append("Frames ausserhalb der sechs Bloecke: %d Frames auf %d Paare." % (sonst_frames, zaehlung[SONSTIGE][1]))
    L.append("")
    tabelle(zaehlung[SONSTIGE][0])

    with open(md_pfad, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")

    # ---- Konsole ----
    print("Frames im Fenster gesamt:", im_fenster, "Filter-Treffer:", len(treffer),
          "unvollstaendig:", unvollst, "Fehlzeilen:", fehler)
    for b in bloecke:
        _, np, ni, cnt, rng, frames = zaehlung[b]
        print("Block", b, "Paare", np, "Indizes", ni, "0x0001", cnt["0x0001"], "0x0000", cnt["0x0000"],
              "0x8000", cnt["0x8000"], "anderer", cnt["anderer"], "Bereich", rng, "Frames", frames)
    print("CSV-Zeilen (ohne Kopf):", len(zeilen))
    print(csv_pfad)
    print(md_pfad)


if __name__ == "__main__":
    main()
