// ring_buffer.h - SPSC-Ringpuffer fuer CAN-Frames (mk1-debug).
// Header-only, C++11, nur <stdint.h> und <stddef.h>.
#ifndef MKDBG_RING_BUFFER_H
#define MKDBG_RING_BUFFER_H

#include <stdint.h>
#include <stddef.h>

namespace mkdbg {

struct Frame {
  uint32_t t;
  uint64_t u;  // Unix-Zeit in ms, 0 = nicht synchron
  uint16_t id;
  uint8_t dlc;
  uint8_t data[8];
  bool tx;
};

// Single-Producer/Single-Consumer ohne Mutex.
// Producer ruft push() auf, Consumer ruft pop() auf.
// head_ und tail_ laufen monoton (size_t, Ueberlauf ist unproblematisch,
// da N ein Teiler von 2^Bits ist). Index = Zaehler & (N - 1).
template <size_t N>
class RingBuffer {
  static_assert(N != 0 && (N & (N - 1)) == 0,
                "RingBuffer: N muss Zweierpotenz sein");

 public:
  RingBuffer() : buf_(own_), head_(0), tail_(0), overflow_(0) {}

  // external: optionaler Speicher (z.B. PSRAM) mit mindestens N Frames.
  // nullptr -> eigener Speicher (own_). own_ wird in jedem Fall angelegt.
  explicit RingBuffer(Frame *external)
      : buf_(external != nullptr ? external : own_),
        head_(0),
        tail_(0),
        overflow_(0) {}

  RingBuffer(const RingBuffer &) = delete;
  RingBuffer &operator=(const RingBuffer &) = delete;

  // Producer. false und overflow()++ wenn voll (neuer Frame wird verworfen).
  bool push(const Frame &f) {
    const size_t h = head_;
    const size_t t = tail_;
    if (h - t >= N) {
      overflow_ = overflow_ + 1;
      return false;
    }
    buf_[h & (N - 1)] = f;
    __sync_synchronize();  // Frame schreiben, bevor head_ weitergeschaltet wird
    head_ = h + 1;
    return true;
  }

  // Consumer. false wenn leer.
  bool pop(Frame &out) {
    const size_t t = tail_;
    const size_t h = head_;
    if (h == t) {
      return false;
    }
    __sync_synchronize();  // head_ lesen, bevor der Frame gelesen wird
    out = buf_[t & (N - 1)];
    __sync_synchronize();  // Frame lesen, bevor tail_ weitergeschaltet wird
    tail_ = t + 1;
    return true;
  }

  // Anzahl gespeicherter Frames. Bei gleichzeitigem Zugriff nur Naeherung,
  // daher auf [0, N] begrenzt. tail_ wird vor head_ gelesen (tail <= head).
  size_t size() const {
    const size_t t = tail_;
    const size_t h = head_;
    const size_t s = h - t;
    return s > N ? N : s;
  }

  size_t capacity() const { return N; }

  // Zaehler verworfener Frames seit Erzeugung (wird von clear() nicht geleert).
  uint32_t overflow() const { return overflow_; }

  // Leert den Puffer. Nur vom Consumer-Thread aufrufen oder bei gestoppter Produktion.
  void clear() {
    const size_t h = head_;
    tail_ = h;
  }

 private:
  Frame own_[N];
  Frame *buf_;
  volatile size_t head_;
  volatile size_t tail_;
  volatile uint32_t overflow_;
};

}  // namespace mkdbg

#endif  // MKDBG_RING_BUFFER_H
