// unix_time.h - Unix-Zeit in ms und NTP-Status fuer mk1-debug (Header-only).
// Namespace mkdbg, C++11, nur gettimeofday aus <sys/time.h>.
#ifndef MKDBG_UNIX_TIME_H
#define MKDBG_UNIX_TIME_H

#include <stdint.h>
#include <sys/time.h>

namespace mkdbg {

// Unix-Zeit in ms. 0, wenn die Systemzeit nicht synchron ist (tv_sec < 1700000000).
inline uint64_t unix_ms() {
  struct timeval tv;
  if (gettimeofday(&tv, nullptr) != 0) return 0;
  if (tv.tv_sec < 1700000000) return 0;
  return (uint64_t)tv.tv_sec * 1000ULL + (uint64_t)(tv.tv_usec / 1000);
}

// true, wenn die Systemzeit per NTP gesetzt wurde.
inline bool ntp_synced() { return unix_ms() != 0; }

}  // namespace mkdbg

#endif  // MKDBG_UNIX_TIME_H
