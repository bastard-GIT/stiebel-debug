"""Referenz-Arbeitskopie (ElsterTable.json) fuer die Auswerte-GUI (kein Qt).

Das Original wird nie geschrieben: save_as verweigert den Pfad der geladenen Datei.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from elster import BUILTIN_BITS, DEFAULT_JSON


class ReferenceError(ValueError):
    pass


class ReferenceModel:
    def __init__(self, path: str | None = None) -> None:
        self.path = Path(path) if path else DEFAULT_JSON
        with open(self.path, encoding="utf-8") as fh:
            self.data: dict = json.load(fh)
        if "signals" not in self.data:
            raise ReferenceError("Keine Referenz-JSON: Schluessel 'signals' fehlt")
        self.dirty = False

    # ---- Lesen ----
    @property
    def types(self) -> list[str]:
        return [t["name"] for t in self.data.get("types", [])]

    @property
    def members(self) -> list[dict]:
        return self.data.get("can_members", [])

    def member_name(self, can_id: int) -> str:
        for m in self.members:
            if int(str(m["can_id"]), 16) == can_id:
                return m["name"]
        return ""

    def _real(self):
        return (s for s in self.data["signals"] if not s.get("sentinel"))

    def signal(self, index: int) -> dict | None:
        for s in self._real():
            if s["index_int"] == index:
                return s
        return None

    def by_name(self, name: str) -> dict | None:
        for s in self._real():
            if s["name"] == name:
                return s
        return None

    def indices(self) -> set[int]:
        return {s["index_int"] for s in self._real()}

    def bits(self, index: int) -> list[dict]:
        sig = self.signal(index)
        if sig is not None and "bits" in sig:
            return list(sig["bits"])
        return list(BUILTIN_BITS.get(index, []))

    # ---- Schreiben (nur Arbeitskopie) ----
    def upsert_signal(self, index: int, name: str, type_name: str,
                      owner: int | None, comment: str = "") -> dict:
        name = name.strip()
        if not name:
            raise ReferenceError("Name fehlt")
        if type_name not in self.types:
            raise ReferenceError(f"Typ unbekannt: {type_name}")
        other = self.by_name(name)
        if other is not None and other["index_int"] != index:
            raise ReferenceError(f"Name '{name}' gehoert schon zu Index 0x{other['index_int']:04x}")
        owner_hex = f"0x{owner:03x}" if owner is not None else None
        owner_name = self.member_name(owner) if owner is not None else None
        note = f"GUI-Auswertung {date.today().isoformat()}"
        full_comment = f"{comment} | {note}" if comment else note
        sig = self.signal(index)
        if sig is None:
            sig = {"name": name, "index": f"0x{index:04x}", "index_int": index, "type": type_name,
                   "owner": owner_hex, "owner_name": owner_name, "poll": "pl_off",
                   "comment": full_comment}
            self.data["signals"].append(sig)
        else:
            sig.update({"name": name, "type": type_name, "owner": owner_hex,
                        "owner_name": owner_name, "comment": full_comment})
        self.dirty = True
        return sig

    def set_bits(self, index: int, bits: list[dict], owner: int | None = None,
                 type_name: str = "et_default") -> None:
        clean: list[dict] = []
        seen_bits: set[int] = set()
        seen_names: set[str] = set()
        for entry in bits:
            bit = entry.get("bit")
            if isinstance(bit, bool) or not isinstance(bit, int) or not 0 <= bit <= 15:
                raise ReferenceError(f"bit ungueltig: {bit!r} (erlaubt 0..15)")
            name = entry.get("name")
            if not isinstance(name, str) or not name.strip():
                raise ReferenceError(f"Name fehlt fuer bit {bit}")
            name = name.strip()
            if bit in seen_bits:
                raise ReferenceError(f"bit doppelt: {bit}")
            if name in seen_names:
                raise ReferenceError(f"Name doppelt: {name}")
            seen_bits.add(bit)
            seen_names.add(name)
            item = {"bit": bit, "name": name}
            for key in ("confirmed", "comment"):
                if key in entry:
                    item[key] = entry[key]
            clean.append(item)

        if self.signal(index) is None:
            self.upsert_signal(index, name='UNK_0x%04X' % index, type_name=type_name, owner=owner)
        sig = self.signal(index)
        if clean:
            sig["bits"] = sorted(clean, key=lambda b: b["bit"])
        else:
            sig.pop("bits", None)
        self.dirty = True

    def save_as(self, path: str) -> None:
        target = Path(path)
        if target.exists() and target.resolve() == self.path.resolve():
            raise ReferenceError("Das geladene Original wird nicht ueberschrieben: anderen Dateinamen waehlen")
        with open(target, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(self.data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        self.dirty = False
