"""Erzeugt referenz/ElsterTable_bits.json: Referenz + BETRIEBS_STATUS (0x0176) und BETRIEBS_STATUS_WPLA (0x4eef) als Bitfeld.
Das Original in referenzen/elster_reference/ wird nur gelesen.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "gui"))
from reference_model import ReferenceModel  # noqa: E402

BITS = [
    {"bit": 0, "name": "VERDICHTER", "confirmed": True, "comment": "auch Schaltprogramm-Symbol im alten Code (x[6] & 0x01)"},
    {"bit": 6, "name": "SPEICHERLADEPUMPE", "confirmed": True},
    {"bit": 12, "name": "ZUSATZHEIZUNG", "confirmed": True, "comment": "Notlauf: Verdichter aus, nur Zusatzheizung, Wert 0x1040"},
    {"bit": 14, "name": "WARMWASSER", "confirmed": True},
]

# BETRIEBS_STATUS_WPLA (0x4eef, Owner 0x480). Quelle kr0ner/OneESP32ToRuleThemAll yaml/wpl_a_base.yaml.
WPLA_CONFIRMED = {0, 4, 6}  # gemessen 0x0051 Heizen 2026-10-20
WPLA_NAMES = [
    "HEIZKREISPUMPE_1", "HEIZKREISPUMPE_2", "AUFHEIZPROGRAMM", "NHZ_STUFEN_IN_BETRIEB",
    "WP_IM_HEIZBETRIEB", "WP_IM_WW_BETRIEB", "VERDICHTER_IN_BETRIEB", "SOMMERBETRIEB_AKTIV",
    "KUEHLBETRIEB_AKTIV", "ABTAUBETRIEB", "SILENT_MODE_1", "SILENT_MODE_2",
]
BITS_WPLA = []
for _bit, _name in enumerate(WPLA_NAMES):
    _entry = {"bit": _bit, "name": _name, "confirmed": _bit in WPLA_CONFIRMED}
    if _bit not in WPLA_CONFIRMED:
        _entry["comment"] = "Quelle kr0ner, am Display noch nicht geprueft"
    BITS_WPLA.append(_entry)

# Namen aus Upstream-Branch feat/v2.5-community-findings (Stand 2026-10-29), nur wo der Wert auf unserem Bus plausibel ist.
# (Index, Name, Typ, Owner, unser Wert / Bereich). Siehe plan/v25-branch-indexabgleich-2026-10-29.md
V25_NEW = [
    (0x0022, "WW_HYSTERESE", "et_dec_val", 0x180, "5.0, 1.0..15.0"),
    (0x0658, "SENSORABGLEICH_VORLAUF_WP", "et_dec_val", 0x480, "0.0, -5.0..5.0"),
    (0x0659, "SENSORABGLEICH_VORLAUF_NHZ", "et_dec_val", 0x480, "0.0, -5.0..5.0"),
    (0x065A, "SENSORABGLEICH_RUECKLAUF_WP", "et_dec_val", 0x480, "0.0, -5.0..5.0"),
    (0x0665, "AUSLEGUNGSTEMPERATUR", "et_dec_val", 0x180, "-12.0, -20.0..5.0"),
    (0x0666, "WAERMEBEDARF", "et_dec_val", 0x180, "8.0, 2.0..10.0"),
    (0x066F, "WW_LEISTUNG_SOMMER", "et_default", 0x514, "5, 2..8"),
    (0x0670, "WW_LEISTUNG_WINTER", "et_default", 0x514, "8, 2..8"),
    (0x067E, "SOLLSPREIZUNG", "et_dec_val", 0x180, "8.0, 6.0..10.0"),
    (0x06B1, "SPANNUNG_INVERTER", "et_dec_val", 0x514, "234.2 V"),
    (0x06B2, "STROM_INVERTER", "et_dec_val", 0x514, "5.7 A"),
    (0x070D, "KOMBISPEICHER", "et_default", 0x180, "0, 0..1"),
    (0x0715, "HEIZEN_TEMPERATURVORGABE_1V", "et_dec_val", 0x480, "10.0, 10.0..30.0"),
    (0x0716, "HEIZEN_TEMPERATURVORGABE_10V", "et_dec_val", 0x480, "50.0, 31.0..65.0"),
    (0x0717, "KUEHLEN_TEMPERATURVORGABE_1V", "et_dec_val", 0x480, "11.0, 9.0..12.0"),
    (0x0718, "KUEHLEN_TEMPERATURVORGABE_10V", "et_dec_val", 0x480, "15.0, 13.0..20.0"),
    (0x071F, "LEISTUNG_PWM_X1_17", "et_default", 0x480, "50, 0..100"),
    (0x0730, "FEHLERAUSGANG", "et_default", 0x480, "0, 0..1"),
    (0x07A6, "ANZEIGE_HOCHDRUCK", "et_cent_val", 0x514, "12.64 bar"),
    (0x4E9F, "SILENT_LEISTUNG", "et_default", 0x500, "70, 35..100 (0x480: 0 = aus)"),
    (0x4EA0, "SILENT_LUEFTER", "et_default", 0x480, "0, 50..100"),
    (0x4EA1, "WAERMEPUMPE_AUS", "et_default", 0x480, "0, 0..1"),
    (0x4EAF, "PUFFERTRIEB", "et_default", 0x180, "1, 0..1"),
    (0x4EB2, "WW_ANFORDERUNG", "et_default", 0x200, "0, 0..2"),
    (0x4EBA, "KONSTANTE_LEISTUNG", "et_dec_val", 0x480, "4.0, 2.0..10.0"),
    (0x4EBF, "REDUZIERUNG_LEISTUNG", "et_default", 0x480, "0, 0..1"),
    (0x4EFA, "EINGESCHALTETE_STUFEN", "et_default", 0x480, "1"),
    (0x4F24, "WARMWASSERBETRIEB", "et_default", 0x180, "1, 0..1"),
    (0x4F25, "WARMWASSERHYSTERESE", "et_dec_val", 0x180, "5.0, 1.0..15.0"),
    (0x4F28, "ANTILEGIONELLENBEHANDLUNG", "et_default", 0x180, "1, 0..1"),
    (0x4F2E, "STILLSTANDSZEIT", "et_default", 0x180, "10, 1..120"),
    (0x4F2F, "MINIMALE_ABTAUZEIT", "et_default", 0x480, "1, 1..20"),
    (0x4F4F, "AUSSENTEMPERATUR_WP", "et_dec_val", 0x514, "8.2 degC"),
    (0x4F84, "EVU_SPERRE_CONFIG", "et_default", 0x480, "3, 0..3"),
    (0x4F8A, "PUMPENLEISTUNG_STANDBY_4F8A", "et_default", 0x180, "30 %, 20..100"),
    (0x4F8C, "PUMPENLEISTUNG_HEIZEN", "et_default", 0x180, "50 %, 30..80"),
    (0x4F92, "PUMPENLEISTUNG_KUEHLEN", "et_default", 0x180, "80 %, 80..100"),
    (0x4FB9, "STEIGUNG_KUEHLKURVE", "et_dec_val", 0x601, "10.0, 1.0..30.0"),
    (0x4FBE, "STARTTEMPERATUR", "et_dec_val", 0x601, "15.0, 11.0..30.0"),
    (0xABE1, "EVU_SPERRE", "et_default", 0x514, "0/1"),
    (0xFDB1, "VERDICHTER_STILLSTAND", "et_little_endian", 0x180, "10 (0x0a00)"),
]
# Bereits bei uns benannt: eigener Name bleibt, Upstream-Name nur als Kommentar.
V25_ALIAS = {
    0x4E8A: "REGLERDYNAMIK_HEIZEN", 0x4EA8: "MAXTEMP", 0x4EB0: "SOLLTEMPERATUR", 0x4EB4: "ISTTEMPERATUR",
    0x4EB8: "KOMFORTTEMPERATUR", 0x4EB9: "ECOTEMPERATUR", 0x4FA1: "SOLL_UEBERHITZUNG_SAUGGAS_VERDICHTER",
    0x4FA2: "IST_UEBERHITZUNG_SAUGGAS_VERDICHTER", 0xC1E7: "TEMPERATUR_INV_VERDICHTER",
    0xC2C3: "OEFFNUNGSGRAD_BYPASSVENT", 0x4EFB: "LAUFZEIT_VD_HEIZEN", 0x4EFD: "LAUFZEIT_VD_WW",
    0x4F06: "LAUFZEIT_VD_ABTAUEN", 0x4F4E: "RESTSTILLSTANDSZEIT",
}


def apply_v25(ref: ReferenceModel) -> None:
    for idx, name, typ, owner, val in V25_NEW:
        if ref.signal(idx) is not None:
            continue  # eigener Name hat Vorrang
        sig = ref.upsert_signal(idx, name, typ, owner)
        sig["comment"] = f"Name v2.5-Branch (Upstream), Bus-Wert {val}"
    for idx, up in V25_ALIAS.items():
        sig = ref.signal(idx)
        if sig is None:
            continue
        note = f"Upstream v2.5: {up}"
        if note not in sig.get("comment", ""):
            sig["comment"] = f"{sig['comment']} | {note}" if sig.get("comment") else note


def main() -> str:
    ref = ReferenceModel()
    ref.upsert_signal(0x0176, "BETRIEBS_STATUS", "et_default", 0x180,
                      "Bitfeld, Wert big-endian (data[5]<<8 | data[6]); Bits siehe 'bits'")
    ref.set_bits(0x0176, BITS, owner=0x180)
    # Quelle: kr0ner/OneESP32ToRuleThemAll yaml/wpl_a_base.yaml (Index 0x4eef, Owner 0x480)
    ref.upsert_signal(0x4EEF, "BETRIEBS_STATUS_WPLA", "et_default", 0x480,
                      "Bitfeld WPL-A, Wert big-endian; Quelle kr0ner/OneESP32ToRuleThemAll yaml/wpl_a_base.yaml")
    ref.set_bits(0x4EEF, BITS_WPLA, owner=0x480)
    apply_v25(ref)
    out = os.path.join(ROOT, "referenz", "ElsterTable_bits.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    ref.save_as(out)
    return out


if __name__ == "__main__":
    print(main())
