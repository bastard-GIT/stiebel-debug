// test_ring_buffer.cpp - Quelltext, wird auf diesem PC NICHT ausgefuehrt
// (kein Host-C++-Compiler). Spaeter z.B.:
//   g++ -std=c++11 -I../firmware test_ring_buffer.cpp -o test_ring_buffer
#include <assert.h>
#include <stddef.h>
#include <stdint.h>

#include "../firmware/ring_buffer.h"

using mkdbg::Frame;
using mkdbg::RingBuffer;

static Frame make_frame(uint16_t id, uint8_t v) {
  Frame f;
  f.t = (uint32_t)id * 10u;
  f.u = (uint64_t)id * 1000000000ull;  // > 32 Bit, prueft uint64_t
  f.id = id;
  f.dlc = 8;
  for (int i = 0; i < 8; ++i) {
    f.data[i] = v;
  }
  f.tx = (v & 1) != 0;
  return f;
}

static void test_empty() {
  RingBuffer<4> rb;
  assert(rb.capacity() == 4);
  assert(rb.size() == 0);
  assert(rb.overflow() == 0);
  Frame out;
  assert(!rb.pop(out));
}

static void test_fifo_and_overflow() {
  RingBuffer<4> rb;
  for (uint16_t i = 1; i <= 4; ++i) {
    assert(rb.push(make_frame(i, (uint8_t)i)));
  }
  assert(rb.size() == 4);
  assert(!rb.push(make_frame(99, 9)));  // voll: neuer Frame verworfen
  assert(rb.overflow() == 1);
  assert(rb.size() == 4);

  Frame out;
  for (uint16_t i = 1; i <= 4; ++i) {
    assert(rb.pop(out));
    assert(out.id == i);
    assert(out.t == (uint32_t)i * 10u);
    assert(out.u == (uint64_t)i * 1000000000ull);
    assert(out.data[0] == (uint8_t)i);
    assert(out.dlc == 8);
    assert(out.tx == ((i & 1) != 0));
  }
  assert(!rb.pop(out));
  assert(rb.size() == 0);
  assert(rb.overflow() == 1);
}

static void test_wraparound() {
  // Puffer laeuft ueber viele Indexwechsel; FIFO-Reihenfolge muss stimmen.
  RingBuffer<4> rb;
  uint16_t in = 0;
  uint16_t expect = 0;
  uint32_t overflows = 0;
  for (int step = 0; step < 1000; ++step) {
    for (int k = 0; k < 3; ++k) {
      if (rb.push(make_frame(in, (uint8_t)in))) {
        ++in;
      } else {
        ++overflows;
      }
    }
    for (int k = 0; k < 2; ++k) {
      Frame f;
      if (rb.pop(f)) {
        assert(f.id == expect);
        ++expect;
      }
    }
    assert(rb.size() == (size_t)(in - expect));
    assert(rb.size() <= rb.capacity());
  }
  assert(rb.overflow() == overflows);
}

static void test_external_storage() {
  Frame ext[8];
  RingBuffer<8> rb(ext);
  assert(rb.capacity() == 8);
  for (uint16_t i = 1; i <= 8; ++i) {
    assert(rb.push(make_frame(i, (uint8_t)i)));
  }
  // Frame i (0-basiert) liegt an Index i im externen Speicher.
  assert(ext[0].id == 1);
  assert(ext[7].id == 8);

  Frame out;
  assert(rb.pop(out));
  assert(out.id == 1);
  assert(rb.size() == 7);
}

static void test_clear() {
  RingBuffer<2> rb;
  assert(rb.push(make_frame(1, 1)));
  assert(rb.push(make_frame(2, 2)));
  assert(!rb.push(make_frame(3, 3)));
  assert(rb.overflow() == 1);

  rb.clear();
  assert(rb.size() == 0);
  assert(rb.overflow() == 1);  // Zaehler bleibt erhalten

  Frame out;
  assert(!rb.pop(out));
  assert(rb.push(make_frame(4, 4)));
  assert(rb.pop(out));
  assert(out.id == 4);
}

int main() {
  test_empty();
  test_fifo_and_overflow();
  test_wraparound();
  test_external_storage();
  test_clear();
  return 0;
}
