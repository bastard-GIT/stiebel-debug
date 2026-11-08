// i2c_frame.h - Frame-Struktur und Aenderungsfilter fuer mk1-i2c (I2C-Slave).
// Header-only, C++11, keine ESP-Abhaengigkeit, keine dynamische Allokation.
// Der fruehere Software-Decoder (passiver Sniffer) entfaellt seit 2026-10-30. Hier bleibt nur,
// was der Slave-Pfad braucht: Frame (fuer MQTT-Ausgabe), Flag F_OVERLEN, ChangeFilter (Modus "changed").
#ifndef I2CS_FRAME_H
#define I2CS_FRAME_H

#include <stdint.h>
#include <stddef.h>
#include <string.h>

namespace i2cs {

static const size_t kMaxFrameBytes = 64;
static const size_t kChangeTableSize = 64;

enum FrameFlags : uint8_t {
  F_OVERLEN = 0x04  // mehr Bytes als in den Frame passen, Rest verworfen
};

struct Frame {
  uint32_t t_ms;                  // Zeit des Empfangs in ms (Firmware-Uhr)
  uint64_t unix_ms;               // setzt der Glue (0 = Uhr nicht synchron)
  uint64_t ack_bits;              // Bit i = 1 wenn Byte i quittiert (Slave: alle Bytes)
  uint8_t len;                    // Anzahl Bytes (0..64)
  uint8_t flags;                  // FrameFlags
  uint8_t bytes[kMaxFrameBytes];  // bytes[0] = Adresse<<1 | R/W (bei LWZ 180: 0x00, rekonstruiert)
};

// Frame-Filter fuer Modus "changed".
// Schluessel: Header = 3 Bytes ab bytes[1], wenn bytes[0] == 0x00 (General Call), sonst ab bytes[0].
// Ist der Header e3 20 10 (Istwerte), gehoert das Typ-Byte (4. Byte nach Adresse) zum Schluessel.
// Frames mit weniger Bytes als der Schluessel: ganzer Frame ist Schluessel.
// Vergleich ueber ganzen Inhalt (len + bytes). Tabelle 64 Eintraege, bei voll: Round-Robin-Zeiger.
// Frames mit Fehlerflags (flags != 0) laufen immer durch.
class ChangeFilter {
 public:
  ChangeFilter() : count_(0), rr_(0) {}

  // true wenn der Frame neu ist oder sich vom letzten Frame mit gleichem Schluessel unterscheidet.
  bool pass(const Frame &f) {
    uint8_t kb[4];
    const uint8_t kn = make_key(f, kb);
    bool changed = true;
    bool found = false;
    for (size_t i = 0; i < count_ && !found; ++i) {
      Entry &e = tab_[i];
      if (e.kn == kn && memcmp(e.kb, kb, kn) == 0) {
        found = true;
        changed = !(e.len == f.len && memcmp(e.bytes, f.bytes, f.len) == 0);
        store(e, f);  // Tabelle wird auch bei Fehlerflag-Frames aktualisiert
      }
    }
    if (!found) {
      Entry *slot;
      if (count_ < kChangeTableSize) {
        slot = &tab_[count_];
        ++count_;
      } else {
        slot = &tab_[rr_];
        rr_ = (rr_ + 1) % kChangeTableSize;
      }
      slot->kn = kn;
      memcpy(slot->kb, kb, kn);
      store(*slot, f);
    }
    if (f.flags != 0) {  // Fehlerflag: laeuft immer durch
      return true;
    }
    return changed;
  }

  void clear() {
    count_ = 0;
    rr_ = 0;
  }

 private:
  struct Entry {
    uint8_t kn;
    uint8_t kb[4];
    uint8_t len;
    uint8_t bytes[kMaxFrameBytes];
  };

  static uint8_t make_key(const Frame &f, uint8_t kb[4]) {
    const size_t start = (f.len >= 1 && f.bytes[0] == 0x00) ? 1 : 0;
    if (f.len < start + 3) {
      memcpy(kb, f.bytes, f.len);
      return f.len;
    }
    memcpy(kb, f.bytes + start, 3);
    if (kb[0] == 0xE3 && kb[1] == 0x20 && kb[2] == 0x10 && f.len >= start + 4) {
      kb[3] = f.bytes[start + 3];
      return 4;
    }
    return 3;
  }

  static void store(Entry &e, const Frame &f) {
    e.len = f.len;
    memcpy(e.bytes, f.bytes, f.len);
  }

  Entry tab_[kChangeTableSize];
  size_t count_;
  size_t rr_;
};

}  // namespace i2cs

#endif  // I2CS_FRAME_H
