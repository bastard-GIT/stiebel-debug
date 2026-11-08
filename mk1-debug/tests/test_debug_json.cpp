// test_debug_json.cpp - Tests fuer firmware/debug_json.h.
// Nur Quelltext. Kompilieren ohne -DNDEBUG, damit assert aktiv ist:
//   g++ -std=c++11 -I../firmware test_debug_json.cpp -o test_debug_json
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "../firmware/debug_json.h"

using namespace mkdbg;

static void test_parse_sniff() {
  SniffCmd c;
  assert(parse_sniff("{\"on\":true,\"ids\":[\"0x180\"],\"types\":[2]}", c));
  assert(c.on);
  assert(c.id_count == 1 && c.ids[0] == 0x180);
  assert(c.type_count == 1 && c.types[0] == 2);

  // leere Listen = alles
  assert(parse_sniff("{\"on\":false,\"ids\":[],\"types\":[]}", c));
  assert(!c.on && c.id_count == 0 && c.type_count == 0);

  // Dezimal und Hex in Strings, fehlende Listen
  assert(parse_sniff("{\"on\":true,\"ids\":[384,\"0X680\"]}", c));
  assert(c.id_count == 2 && c.ids[0] == 384 && c.ids[1] == 0x680);
  assert(c.type_count == 0);

  // "on" fehlt
  assert(!parse_sniff("{\"ids\":[]}", c));
  // zu viele IDs (max 16)
  assert(!parse_sniff("{\"on\":true,\"ids\":[1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17]}", c));
  // kein Array
  assert(!parse_sniff("{\"on\":true,\"ids\":5}", c));
  // Wert zu gross fuer uint16
  assert(!parse_sniff("{\"on\":true,\"ids\":[\"0x10000\"]}", c));
  // Nullpointer
  assert(!parse_sniff(nullptr, c));
}

static void test_parse_read() {
  ReadCmd r;
  assert(parse_read("{\"member\":\"0x180\",\"index\":\"0x4ef0\"}", r));
  assert(r.member == 0x180 && r.index == 0x4ef0);

  assert(parse_read("{\"member\":\"0X500\",\"index\":3}", r));
  assert(r.member == 0x500 && r.index == 3);

  assert(!parse_read("{\"member\":\"0x180\"}", r));                       // index fehlt
  assert(!parse_read("{\"member\":\"0x180\",\"index\":\"0x10000\"}", r));  // ueber 0xFFFF
  assert(!parse_read("{\"member\":\"0xZZ\",\"index\":\"0x1\"}", r));       // kein Hex
  assert(!parse_read("{\"member\":\"-1\",\"index\":\"0x1\"}", r));         // Vorzeichen

  // switch: Default 0xFA, "f8"/"0xF9" (case-insensitiv), unbekannt abgelehnt
  assert(parse_read("{\"member\":\"0x180\",\"index\":\"0x3\"}", r) && r.sw == 0xFA);
  assert(parse_read("{\"member\":\"0x180\",\"index\":\"0x3\",\"switch\":\"f8\"}", r) && r.sw == 0xF8);
  assert(parse_read("{\"member\":\"0x180\",\"index\":\"0x3\",\"switch\":\"0XF9\"}", r) && r.sw == 0xF9);
  assert(!parse_read("{\"member\":\"0x180\",\"index\":\"0x3\",\"switch\":\"f7\"}", r));

  // src: Default 0x680, optional 0x000..0x7FF
  assert(parse_read("{\"member\":\"0x100\",\"index\":\"0x3\"}", r) && r.src == 0x680);
  assert(parse_read("{\"member\":\"0x100\",\"index\":\"0x3\",\"src\":\"0x700\"}", r) && r.src == 0x700);
  assert(!parse_read("{\"member\":\"0x100\",\"index\":\"0x3\",\"src\":\"0x800\"}", r));
  // timeout_ms: Default 1000, < 100 auf 100, > 10000 abgelehnt
  assert(parse_read("{\"member\":\"0x100\",\"index\":\"0x3\"}", r) && r.timeout_ms == 1000);
  assert(parse_read("{\"member\":\"0x100\",\"index\":\"0x3\",\"timeout_ms\":3000}", r) && r.timeout_ms == 3000);
  assert(parse_read("{\"member\":\"0x100\",\"index\":\"0x3\",\"timeout_ms\":5}", r) && r.timeout_ms == 100);
  assert(!parse_read("{\"member\":\"0x100\",\"index\":\"0x3\",\"timeout_ms\":20000}", r));
}

static void test_parse_scan() {
  ScanCmd s;
  assert(parse_scan("{\"member\":\"0x500\",\"from\":\"0x4e00\",\"to\":\"0x4fff\",\"delay_ms\":200}", s));
  assert(s.member == 0x500 && s.from == 0x4e00 && s.to == 0x4fff && s.delay_ms == 200);

  // delay_ms unter 100 wird auf 100 geklemmt
  assert(parse_scan("{\"member\":\"0x500\",\"from\":\"0x4e00\",\"to\":\"0x4e10\",\"delay_ms\":50}", s));
  assert(s.delay_ms == 100);

  // delay_ms fehlt: Default 200
  assert(parse_scan("{\"member\":\"0x500\",\"from\":\"0x4e00\",\"to\":\"0x4e10\"}", s));
  assert(s.delay_ms == 200);

  // from == to ist erlaubt
  assert(parse_scan("{\"member\":\"0x500\",\"from\":5,\"to\":5}", s));
  assert(s.from == 5 && s.to == 5);
  assert(s.sw == 0xFA);
  assert(parse_scan("{\"member\":\"0x500\",\"from\":5,\"to\":5,\"switch\":\"0xf8\"}", s) && s.sw == 0xF8);

  // from > to
  assert(!parse_scan("{\"member\":\"0x500\",\"from\":\"0x4fff\",\"to\":\"0x4e00\"}", s));
  // Pflichtfelder fehlen
  assert(!parse_scan("{\"from\":\"0x4e00\",\"to\":\"0x4fff\"}", s));
  assert(!parse_scan("{\"member\":\"0x500\",\"to\":\"0x4fff\"}", s));
  assert(!parse_scan("{\"member\":\"0x500\",\"from\":\"0x4e00\"}", s));
}

static void test_format_rx_batch() {
  Frame frames[2];
  memset(frames, 0, sizeof frames);
  const uint8_t d0[7] = {0xd2, 0x00, 0xfa, 0x01, 0x12, 0x02, 0x00};
  const uint8_t d1[7] = {0x31, 0x00, 0xfa, 0x01, 0x12, 0x00, 0x00};
  frames[0].t = 123456;
  frames[0].id = 0x180;
  frames[0].dlc = 7;
  memcpy(frames[0].data, d0, 7);
  frames[0].tx = false;
  frames[1].t = 5;
  frames[1].id = 0x680;
  frames[1].dlc = 7;
  memcpy(frames[1].data, d1, 7);
  frames[1].tx = true;

  const char *expected =
      "[{\"t\":123456,\"id\":\"0x180\",\"d\":\"d200fa01120200\",\"tx\":false},"
      "{\"t\":5,\"id\":\"0x680\",\"d\":\"3100fa01120000\",\"tx\":true}]";
  char buf[256];
  size_t n = format_rx_batch(frames, 2, buf, sizeof buf);
  assert(n == strlen(expected));
  assert(strcmp(buf, expected) == 0);

  // leerer Batch
  n = format_rx_batch(nullptr, 0, buf, sizeof buf);
  assert(n == 2 && strcmp(buf, "[]") == 0);

  // cap genau so gross wie Text ohne NUL: Fehler, Puffer leer
  char small[256];
  size_t len = strlen(expected);
  small[0] = 'x';
  n = format_rx_batch(frames, 2, small, len);
  assert(n == 0 && small[0] == '\0');

  // cap = Text + NUL: passt
  n = format_rx_batch(frames, 2, small, len + 1);
  assert(n == len && strcmp(small, expected) == 0);

  // gemischt: "u" nur bei Frame mit u != 0, direkt nach "t"
  frames[1].u = 1700000000123ULL;
  const char *expected_mixed =
      "[{\"t\":123456,\"id\":\"0x180\",\"d\":\"d200fa01120200\",\"tx\":false},"
      "{\"t\":5,\"u\":1700000000123,\"id\":\"0x680\",\"d\":\"3100fa01120000\",\"tx\":true}]";
  n = format_rx_batch(frames, 2, buf, sizeof buf);
  assert(n == strlen(expected_mixed) && strcmp(buf, expected_mixed) == 0);

  // alle Frames mit u
  frames[0].u = 1;
  const char *expected_all =
      "[{\"t\":123456,\"u\":1,\"id\":\"0x180\",\"d\":\"d200fa01120200\",\"tx\":false},"
      "{\"t\":5,\"u\":1700000000123,\"id\":\"0x680\",\"d\":\"3100fa01120000\",\"tx\":true}]";
  n = format_rx_batch(frames, 2, buf, sizeof buf);
  assert(n == strlen(expected_all) && strcmp(buf, expected_all) == 0);

  // Puffer zu klein (mit u): Fehler, Puffer leer; Text + NUL passt
  len = strlen(expected_all);
  small[0] = 'x';
  n = format_rx_batch(frames, 2, small, len);
  assert(n == 0 && small[0] == '\0');
  n = format_rx_batch(frames, 2, small, len + 1);
  assert(n == len && strcmp(small, expected_all) == 0);
}

static void test_format_read_result() {
  char buf[160];
  const char *expected =
      "{\"member\":\"0x180\",\"index\":\"0x4ef0\",\"switch\":\"0xfa\",\"raw\":\"0x03e8\",\"status\":\"ok\"}";
  size_t n = format_read_result(0x180, 0x4ef0, 0xFA, 0x03e8, "ok", buf, sizeof buf);
  assert(n == strlen(expected) && strcmp(buf, expected) == 0);

  // zu kleiner Puffer
  char small[160];
  n = format_read_result(0x180, 0x4ef0, 0xFA, 0x03e8, "ok", small, strlen(expected));
  assert(n == 0 && small[0] == '\0');

  // src != 0x680: Feld src am Ende
  const char *expected_src =
      "{\"member\":\"0x100\",\"index\":\"0x4f4d\",\"switch\":\"0xfa\",\"raw\":\"0x8000\",\"status\":\"unsupported\","
      "\"src\":\"0x700\"}";
  n = format_read_result(0x100, 0x4f4d, 0xFA, 0x8000, "unsupported", buf, sizeof buf, 0x700);
  assert(n == strlen(expected_src) && strcmp(buf, expected_src) == 0);
}

static void test_format_scan_progress() {
  char buf[160];
  const char *expected =
      "{\"member\":\"0x180\",\"format\":\"auto\",\"index\":\"0x4e40\",\"switch\":\"0xf8\","
      "\"done\":64,\"total\":512,\"state\":\"running\"}";
  size_t n = format_scan_progress(0x180, "auto", 0x4e40, 0xF8, 64, 512, "running", buf, sizeof buf);
  assert(n == strlen(expected) && strcmp(buf, expected) == 0);

  char small[160];
  n = format_scan_progress(0x180, "auto", 0x4e40, 0xF8, 64, 512, "running", small, strlen(expected));
  assert(n == 0 && small[0] == '\0');
}

static void test_format_status() {
  char buf[256];
  const char *expected =
      "{\"fw\":\"debug\",\"rx\":10,\"tx\":2,\"rx_fill\":0,\"rx_cap\":4096,\"overflow\":0,"
      "\"twai_err\":0,\"bus_off\":false,\"heap\":1000,\"ntp\":false}";
  size_t n = format_status(10, 2, 0, 4096, 0, 0, false, 1000, false, buf, sizeof buf);
  assert(n == strlen(expected) && strcmp(buf, expected) == 0);

  const char *expected2 =
      "{\"fw\":\"debug\",\"rx\":0,\"tx\":0,\"rx_fill\":0,\"rx_cap\":4096,\"overflow\":3,"
      "\"twai_err\":1,\"bus_off\":true,\"heap\":0,\"ntp\":true}";
  n = format_status(0, 0, 0, 4096, 3, 1, true, 0, true, buf, sizeof buf);
  assert(n == strlen(expected2) && strcmp(buf, expected2) == 0);

  char small[256];
  n = format_status(10, 2, 0, 4096, 0, 0, false, 1000, false, small, strlen(expected));
  assert(n == 0 && small[0] == '\0');
}

static void test_parse_write() {
  WriteCmd w;
  assert(parse_write("{\"member\":\"0x201\",\"index\":\"0x0013\",\"value\":\"0x0208\",\"format\":\"auto\"}", w));
  assert(w.member == 0x201 && w.index == 0x13 && w.value == 0x208 && w.format == 0);

  // format fehlt: auto
  assert(parse_write("{\"member\":\"0x201\",\"index\":\"0x0013\",\"value\":\"0x0208\"}", w));
  assert(w.format == 0);
  // format short / long
  assert(parse_write("{\"member\":\"0x201\",\"index\":\"0x0013\",\"value\":\"0x0208\",\"format\":\"short\"}", w));
  assert(w.format == 1);
  assert(parse_write("{\"member\":\"0x201\",\"index\":\"0x0013\",\"value\":\"0x0208\",\"format\":\"long\"}", w));
  assert(w.format == 2);

  // Dezimal und Zahlen ohne Anfuehrungszeichen, Grenzwert 0xFFFF
  assert(parse_write("{\"member\":384,\"index\":19,\"value\":520}", w));
  assert(w.member == 384 && w.index == 19 && w.value == 520);
  assert(parse_write("{\"member\":\"0x201\",\"index\":\"0xFFFF\",\"value\":\"0xffff\"}", w));
  assert(w.index == 0xFFFF && w.value == 0xFFFF);
  assert(parse_write("{\"member\":\"0x201\",\"index\":\"0x0000\",\"value\":\"0x0000\"}", w));
  assert(w.index == 0 && w.value == 0);

  // Pflichtfelder fehlen
  assert(!parse_write("{\"index\":\"0x0013\",\"value\":\"0x0208\"}", w));
  assert(!parse_write("{\"member\":\"0x201\",\"value\":\"0x0208\"}", w));
  assert(!parse_write("{\"member\":\"0x201\",\"index\":\"0x0013\"}", w));
  // Wert zu gross
  assert(!parse_write("{\"member\":\"0x201\",\"index\":\"0x10000\",\"value\":\"0x0208\"}", w));
  assert(!parse_write("{\"member\":\"0x201\",\"index\":\"0x0013\",\"value\":\"0x10000\"}", w));
  assert(!parse_write("{\"member\":\"0x201\",\"index\":\"0x0013\",\"value\":65536}", w));
  // kein Hex, Vorzeichen
  assert(!parse_write("{\"member\":\"0x201\",\"index\":\"0x0013\",\"value\":\"0xZZ\"}", w));
  assert(!parse_write("{\"member\":\"0x201\",\"index\":\"0x0013\",\"value\":\"-1\"}", w));
  // unbekanntes format
  assert(!parse_write("{\"member\":\"0x201\",\"index\":\"0x0013\",\"value\":\"0x0208\",\"format\":\"fast\"}", w));
  // Nullpointer
  assert(!parse_write(nullptr, w));
}

static void test_format_write_result() {
  char buf[160];
  const char *expected =
      "{\"member\":\"0x201\",\"index\":\"0x0013\",\"value\":\"0x0208\",\"status\":\"verified\","
      "\"ack\":4,\"readback\":\"0x0208\"}";
  size_t n = format_write_result(0x201, 0x13, 0x0208, "verified", 4, 0x0208, buf, sizeof buf);
  assert(n == strlen(expected) && strcmp(buf, expected) == 0);

  // ack fehlt (ack_type < 0)
  const char *no_ack =
      "{\"member\":\"0x201\",\"index\":\"0x0013\",\"value\":\"0x0208\",\"status\":\"verified\","
      "\"readback\":\"0x0208\"}";
  n = format_write_result(0x201, 0x13, 0x0208, "verified", -1, 0x0208, buf, sizeof buf);
  assert(n == strlen(no_ack) && strcmp(buf, no_ack) == 0);

  // readback fehlt (readback < 0)
  const char *no_rb =
      "{\"member\":\"0x201\",\"index\":\"0x0013\",\"value\":\"0x0208\",\"status\":\"verified\","
      "\"ack\":4}";
  n = format_write_result(0x201, 0x13, 0x0208, "verified", 4, -1, buf, sizeof buf);
  assert(n == strlen(no_rb) && strcmp(buf, no_rb) == 0);

  // beides fehlt
  const char *none =
      "{\"member\":\"0x201\",\"index\":\"0x0013\",\"value\":\"0x0208\",\"status\":\"timeout\"}";
  n = format_write_result(0x201, 0x13, 0x0208, "timeout", -1, -1, buf, sizeof buf);
  assert(n == strlen(none) && strcmp(buf, none) == 0);

  // Status mit Unterstrich
  n = format_write_result(0x201, 0x13, 0x0208, "no_ack", -1, -1, buf, sizeof buf);
  assert(n > 0 && strstr(buf, "\"status\":\"no_ack\"") != nullptr);

  // Status unsicher: nullptr, leer, Grossbuchstaben, Leerzeichen, Anfuehrungszeichen
  assert(format_write_result(0x201, 0x13, 0x0208, nullptr, 4, 0x0208, buf, sizeof buf) == 0);
  assert(format_write_result(0x201, 0x13, 0x0208, "", 4, 0x0208, buf, sizeof buf) == 0 && buf[0] == '\0');
  assert(format_write_result(0x201, 0x13, 0x0208, "Verified", 4, 0x0208, buf, sizeof buf) == 0 && buf[0] == '\0');
  assert(format_write_result(0x201, 0x13, 0x0208, "a b", 4, 0x0208, buf, sizeof buf) == 0 && buf[0] == '\0');
  assert(format_write_result(0x201, 0x13, 0x0208, "x\"y", 4, 0x0208, buf, sizeof buf) == 0 && buf[0] == '\0');
  assert(format_write_result(0x201, 0x13, 0x0208, "ok1", 4, 0x0208, buf, sizeof buf) == 0 && buf[0] == '\0');

  // out == nullptr / cap == 0
  assert(format_write_result(0x201, 0x13, 0x0208, "verified", 4, 0x0208, nullptr, sizeof buf) == 0);
  assert(format_write_result(0x201, 0x13, 0x0208, "verified", 4, 0x0208, buf, 0) == 0);

  // zu kleiner Puffer: Fehler, Puffer leer; Text + NUL passt
  char small[160];
  small[0] = 'x';
  n = format_write_result(0x201, 0x13, 0x0208, "verified", 4, 0x0208, small, strlen(expected));
  assert(n == 0 && small[0] == '\0');
  n = format_write_result(0x201, 0x13, 0x0208, "verified", 4, 0x0208, small, strlen(expected) + 1);
  assert(n == strlen(expected) && strcmp(small, expected) == 0);
}

int main() {
  test_parse_sniff();
  test_parse_read();
  test_parse_scan();
  test_parse_write();
  test_format_rx_batch();
  test_format_read_result();
  test_format_scan_progress();
  test_format_status();
  test_format_write_result();
  printf("test_debug_json: alle Tests OK\n");
  return 0;
}
