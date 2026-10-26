/* Standalone shared-modem routing test with a simulated serial device.
 * MSVC: cl /I src src/dialin/test_router.c src/mm_dialin.c src/mm_serial.c ws2_32.lib
 */
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <time.h>
#include <assert.h>
#ifndef _WIN32
#include <signal.h>
#endif
#include "mm_manager.h"
#include "mm_serial.h"
#include "mm_dialin.h"

static const unsigned char *incoming;
static size_t length, offset;
static int timeout_ms;
#ifdef _WIN32
int manager_running = 1;
#else
volatile sig_atomic_t manager_running = 1;
#endif

int mm_manager_is_running(void) { return offset < length; }
int platform_serial_read_timeout(int fd, int ms) { (void)fd; timeout_ms = ms; return 0; }
int platform_serial_get_modem_status(int fd) { (void)fd; return 0x80; }
int platform_open_serial(const char *path) { (void)path; return 1; }
int platform_init_serial(int fd, int speed) { (void)fd; (void)speed; return 0; }
int platform_close_serial(int fd) { (void)fd; return 0; }
int platform_drain_serial(int fd) { (void)fd; return 0; }
int platform_flush_serial(int fd) { (void)fd; return 0; }
int platform_serial_set_dtr(int fd, int set) { (void)fd; (void)set; return 0; }
ssize_t platform_read_serial(int fd, void *buffer, size_t count) {
    (void)fd;
    if (count > length - offset) count = length - offset;
    memcpy(buffer, incoming + offset, count);
    offset += count;
    return (ssize_t)count;
}
ssize_t platform_write_serial(int fd, const void *buffer, size_t count) {
    (void)fd; (void)buffer; return (ssize_t)count;
}

static void feed(const unsigned char *data, size_t size) {
    incoming = data; length = size; offset = 0;
}

int main(void) {
    mm_serial_context_t serial = {0};
    mm_proto_t proto = {0};
    proto.serial_context = &serial;
    /* Known valid management ACK, zero payload, total size six. */
    const unsigned char management[] = {2, 8, 5, 0xd6, 0x03, 3};
    /* Calculate the CRC independently below to allow arbitrary management data. */
    unsigned char packet[sizeof(management)];
    memcpy(packet, management, sizeof(packet));
    uint16_t crc = 0;
    for (int i = 0; i < 3; ++i) {
        crc ^= packet[i];
        for (int bit = 0; bit < 8; ++bit)
            crc = (uint16_t)((crc >> 1) ^ ((crc & 1) ? 0xa001 : 0));
    }
    packet[3] = (unsigned char)crc;
    packet[4] = (unsigned char)(crc >> 8);
    feed(packet, sizeof(packet));
    assert(mm_dialin_route(&proto, 0) == 0);
    assert(serial.replay_length == sizeof(packet));
    assert(timeout_ms == 1000);
    unsigned char replay[6];
    assert(read_serial(&serial, replay, sizeof(replay), 0) == (ssize_t)sizeof(replay));
    assert(memcmp(replay, packet, sizeof(packet)) == 0);

    const unsigned char noa[] = {
        2,0x25,0x82,5,0x12,0x34,0x56,0x78,0x90,7,
        0x4e,0x4f,0x41,0x31,0x58,0x30,0x34,4,0x56,0x31,0x2e,0x33,
        1,2,7,0x20,0x26,0x10,0x24,0x15,0x42,0x17,1,0,0,1,0x23,1,0,9,0xb0,3
    };
    feed(noa, sizeof(noa));
    assert(mm_dialin_route(&proto, 0) == 1); /* Closed service: handled and released. */
    assert(serial.replay_length == 0 && timeout_ms == 1000);
    packet[3] ^= 1;
    feed(packet, sizeof(packet));
    assert(mm_dialin_route(&proto, 0) == 0); /* Let management generate its normal CRC NACK. */
    assert(serial.replay_length == sizeof(packet));
    assert(timeout_ms == 1000);
    puts("PASS: management replay/CRC handoff, TTY routing/service failure, timeout restoration");
    return 0;
}
