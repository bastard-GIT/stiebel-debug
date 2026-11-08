// test_elster_frame.cpp - Tests fuer firmware/elster_frame.h (nur Quelltext, wird auf dem PC nicht ausgefuehrt).
// Build (spaeter, mit Compiler): g++ -std=c++11 -Wall -Wextra tests/test_elster_frame.cpp -o test_elster_frame
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "../firmware/elster_frame.h"

using namespace mkdbg;

static void test_members() {
  assert(MEMBER_COUNT == 21);

  const Member *kessel = find_member(0x180);
  assert(kessel != nullptr);
  assert(strcmp(kessel->name, "KESSEL") == 0);
  assert(kessel->read_id[0] == 0x31 && kessel->read_id[1] == 0x00);
  assert(kessel->write_id[0] == 0x30 && kessel->write_id[1] == 0x00);

  const Member *speicher = find_member(0x201);
  assert(speicher != nullptr);
  assert(strcmp(speicher->name, "SPEICHER") == 0);
  assert(speicher->read_id[0] == 0x41 && speicher->read_id[1] == 0x01);

  assert(find_member(0x999) == nullptr);
}

static void test_read_request() {
  uint8_t out[7];
  const Member *kessel = find_member(0x180);
  assert(kessel != nullptr);

  // PROGRAMMSCHALTER 0x0112 von KESSEL -> 31 00 fa 01 12 00 00
  size_t n = build_read_request(*kessel, 0x0112, out);
  assert(n == 7);
  const uint8_t exp1[7] = {0x31, 0x00, 0xfa, 0x01, 0x12, 0x00, 0x00};
  assert(memcmp(out, exp1, 7) == 0);

  // Kurzer Index 0x0003 von SPEICHER -> 41 01 03 00 00 00 00
  const Member *speicher = find_member(0x201);
  assert(speicher != nullptr);
  n = build_read_request(*speicher, 0x0003, out);
  assert(n == 7);
  const uint8_t exp2[7] = {0x41, 0x01, 0x03, 0x00, 0x00, 0x00, 0x00};
  assert(memcmp(out, exp2, 7) == 0);

  // Grenze: 0x00FF ist noch kurz, 0x0100 ist lang
  n = build_read_request(*kessel, 0x00FF, out);
  assert(n == 7 && out[2] == 0xFF && out[3] == 0x00 && out[4] == 0x00);
  n = build_read_request(*kessel, 0x0100, out);
  assert(n == 7 && out[2] == 0xFA && out[3] == 0x01 && out[4] == 0x00);
}

static void test_parse_frame() {
  // Antwort KESSEL -> ESP: d2 00 fa 01 12 02 00
  const uint8_t resp[7] = {0xd2, 0x00, 0xfa, 0x01, 0x12, 0x02, 0x00};
  Parsed p;
  bool ok = parse_frame(resp, 7, p);
  assert(ok);
  assert(p.msg_type == 2);
  assert(p.receiver == 0x680);
  assert(p.index == 0x0112);
  assert(p.value == 0x0200);

  // Kurzer Index: Antwort auf Index 0x05 mit Wert 0x1234
  const uint8_t resp_short[7] = {0xd2, 0x00, 0x05, 0x12, 0x34, 0x00, 0x00};
  ok = parse_frame(resp_short, 7, p);
  assert(ok);
  assert(p.msg_type == 2);
  assert(p.index == 0x05);
  assert(p.value == 0x1234);

  // Zu kurz -> false
  ok = parse_frame(resp, 6, p);
  assert(!ok);
  ok = parse_frame(resp, 0, p);
  assert(!ok);
}

static void test_is_response_for() {
  const uint8_t resp[7] = {0xd2, 0x00, 0xfa, 0x01, 0x12, 0x02, 0x00};
  Parsed p;
  bool ok = parse_frame(resp, 7, p);
  assert(ok);

  // Passende Antwort: Absender 0x180, Member 0x180, Index 0x0112, Switch 0xFA
  assert(is_response_for(p, 0x180, 0x180, 0x0112, 0xFA));
  // Falscher Absender
  assert(!is_response_for(p, 0x201, 0x180, 0x0112, 0xFA));
  // Falscher Index
  assert(!is_response_for(p, 0x180, 0x180, 0x0113, 0xFA));
  // Falscher Switch: Antwort ist 0xFA, Request erwartet 0xF8
  assert(!is_response_for(p, 0x180, 0x180, 0x0112, 0xF8));

  // Keine Antwort (msg_type 1 = read) -> false
  uint8_t req[7] = {0xd1, 0x00, 0xfa, 0x01, 0x12, 0x00, 0x00};
  Parsed q;
  ok = parse_frame(req, 7, q);
  assert(ok);
  assert(q.msg_type == 1);
  assert(!is_response_for(q, 0x180, 0x180, 0x0112, 0xFA));
}

static void test_switch() {
  // Langlayout mit Switch 0xF8 (Minimum): Index 0x0003, Wert 0x0100
  const uint8_t mn[7] = {0xd2, 0x00, 0xf8, 0x00, 0x03, 0x01, 0x00};
  Parsed p;
  assert(parse_frame(mn, 7, p));
  assert(p.sw == 0xF8 && p.index == 0x0003 && p.value == 0x0100);
  assert(is_response_for(p, 0x180, 0x180, 0x0003, 0xF8));
  assert(!is_response_for(p, 0x180, 0x180, 0x0003, 0xFA));

  // Kurzlayout: sw = 0
  const uint8_t sh[7] = {0xd2, 0x00, 0x05, 0x12, 0x34, 0x00, 0x00};
  assert(parse_frame(sh, 7, p));
  assert(p.sw == 0);
  assert(is_response_for(p, 0x180, 0x180, 0x0005, 0xFA));  // 0xFA akzeptiert Kurzlayout

  // Request mit sw 0xF9 fuer Index 0x0010: Langformat [.., F9, 00, 10, 0, 0]
  uint8_t out[7];
  assert(build_read_request_id(0x180, 0x0010, 0, 0xF9, out) == 7);
  const uint8_t exp_f9[7] = {0x31, 0x00, 0xf9, 0x00, 0x10, 0x00, 0x00};
  assert(memcmp(out, exp_f9, 7) == 0);

  // Index 0xFA ist auch bei sw 0xFA Langformat: [.., FA, 00, FA, 0, 0]
  assert(build_read_request_id(0x180, 0x00fa, 0, 0xFA, out) == 7);
  const uint8_t exp_fa[7] = {0x31, 0x00, 0xfa, 0x00, 0xfa, 0x00, 0x00};
  assert(memcmp(out, exp_fa, 7) == 0);

  // Index 0x0003, sw 0xFA, auto -> Kurzformat
  assert(build_read_request_id(0x201, 0x0003, 0, 0xFA, out) == 7);
  const uint8_t exp_short[7] = {0x41, 0x01, 0x03, 0x00, 0x00, 0x00, 0x00};
  assert(memcmp(out, exp_short, 7) == 0);

  // fmt long erzwingt Langformat auch fuer Index 0x0003
  assert(build_read_request_id(0x201, 0x0003, 2, 0xFA, out) == 7);
  const uint8_t exp_long[7] = {0x41, 0x01, 0xfa, 0x00, 0x03, 0x00, 0x00};
  assert(memcmp(out, exp_long, 7) == 0);
}

static void test_write_request() {
  uint8_t out[7];

  // KESSEL 0x180, Index 0x0112, Wert 0x0300, lang -> 30 00 fa 01 12 03 00
  assert(build_write_request_id(0x180, 0x0112, 0x0300, 2, out) == 7);
  const uint8_t exp_l[7] = {0x30, 0x00, 0xfa, 0x01, 0x12, 0x03, 0x00};
  assert(memcmp(out, exp_l, 7) == 0);

  // 0x201, Index 0x0013, Wert 0x0208, kurz -> 40 01 13 02 08 00 00
  assert(build_write_request_id(0x201, 0x0013, 0x0208, 1, out) == 7);
  const uint8_t exp_s[7] = {0x40, 0x01, 0x13, 0x02, 0x08, 0x00, 0x00};
  assert(memcmp(out, exp_s, 7) == 0);

  // WPUMPE 0x514: d0 0xA0, d1 0x14
  assert(build_write_request_id(0x514, 0x0005, 0x0001, 1, out) == 7);
  assert(out[0] == 0xA0 && out[1] == 0x14);

  // Unbekannte ID 0x4ab: d0 0x90, d1 0x2B
  assert(build_write_request_id(0x4ab, 0x0005, 0x0001, 0, out) == 7);
  assert(out[0] == 0x90 && out[1] == 0x2B);

  // Index 0x00F7 erzwingt Langformat (auch bei fmt auto)
  assert(build_write_request_id(0x180, 0x00F7, 0x0001, 0, out) == 7);
  const uint8_t exp_f7[7] = {0x30, 0x00, 0xfa, 0x00, 0xf7, 0x00, 0x01};
  assert(memcmp(out, exp_f7, 7) == 0);

  // fmt 2 erzwingt Langformat bei kleinem Index
  assert(build_write_request_id(0x201, 0x0003, 0x0002, 2, out) == 7);
  const uint8_t exp_f2[7] = {0x40, 0x01, 0xfa, 0x00, 0x03, 0x00, 0x02};
  assert(memcmp(out, exp_f2, 7) == 0);

  // fmt 1 mit Index > 0xFF bleibt Langformat
  assert(build_write_request_id(0x180, 0x0100, 0x0001, 1, out) == 7);
  const uint8_t exp_big[7] = {0x30, 0x00, 0xfa, 0x01, 0x00, 0x00, 0x01};
  assert(memcmp(out, exp_big, 7) == 0);
}

int main() {
  test_members();
  test_read_request();
  test_parse_frame();
  test_is_response_for();
  test_switch();
  test_write_request();
  printf("test_elster_frame: ok\n");
  return 0;
}
