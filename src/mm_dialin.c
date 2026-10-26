/* Shared-modem NOA classification and bounded localhost transport bridge. */
#ifdef _WIN32
#include <winsock2.h>
#include <windows.h>
#else
#include <sys/socket.h>
#include <sys/select.h>
#include <arpa/inet.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <time.h>
#define SOCKET int
#define INVALID_SOCKET (-1)
#endif
#include <stdio.h>
#include <time.h>
#include <stdint.h>
#include <string.h>
#include "mm_manager.h"
#include "mm_serial.h"
#include "mm_dialin.h"
#include "mm_tcp.h"

static double now_seconds(void) {
#ifdef _WIN32
    LARGE_INTEGER frequency, counter;
    QueryPerformanceFrequency(&frequency);
    QueryPerformanceCounter(&counter);
    return (double)counter.QuadPart / (double)frequency.QuadPart;
#else
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec / 1e9;
#endif
}

static uint16_t frame_crc(const unsigned char *data, size_t length) {
    uint16_t crc = 0;
    for (size_t i = 0; i < length; ++i) {
        crc ^= data[i];
        for (int bit = 0; bit < 8; ++bit)
            crc = (uint16_t)((crc >> 1) ^ ((crc & 1) ? 0xa001 : 0));
    }
    return crc;
}

static int valid_frame(const unsigned char *p, size_t n) {
    return n >= 6 && p[n - 1] == 3 &&
        frame_crc(p, n - 3) == (uint16_t)(p[n - 3] | (p[n - 2] << 8));
}

static int carrier(mm_proto_t *proto) {
    return !proto->monitor_carrier ||
        (serial_get_modem_status(proto->serial_context) & MS_RLSD_ON);
}

static int terminal_timeout(mm_proto_t *proto, int milliseconds) {
    return proto->tcp_context ? 0 : platform_serial_read_timeout(proto->serial_context->fd, milliseconds);
}

static ssize_t terminal_read(mm_proto_t *proto, unsigned char *byte) {
    return proto->tcp_context ? mm_tcp_read_timeout(proto->tcp_context, byte, 0, 100) :
        read_serial(proto->serial_context, byte, 1, 0);
}

static int terminal_write(mm_proto_t *proto, const unsigned char *bytes, size_t length) {
    if (proto->tcp_context) return mm_tcp_write(proto->tcp_context, bytes, length);
    size_t offset = 0;
    while (offset < length) {
        ssize_t sent = write_serial(proto->serial_context, bytes + offset, length - offset);
        if (sent <= 0) return -1;
        offset += (size_t)sent;
    }
    return 0;
}

static int bridge_send(SOCKET bridge, const unsigned char *bytes, size_t length, int flags) {
    size_t offset = 0;
    while (offset < length) {
        int sent = (int)send(bridge, (const char *)bytes + offset, (int)(length - offset), flags);
        if (sent <= 0) return -1;
        offset += (size_t)sent;
    }
    return 0;
}

static int connect_bridge(SOCKET bridge, struct sockaddr_in *address) {
#ifdef _WIN32
    u_long nonblocking = 1;
    if (ioctlsocket(bridge, FIONBIO, &nonblocking) != 0) return -1;
#else
    int flags = fcntl(bridge, F_GETFL, 0);
    if (flags < 0 || bridge >= FD_SETSIZE ||
        fcntl(bridge, F_SETFL, flags | O_NONBLOCK) != 0) return -1;
#endif
    int result = connect(bridge, (struct sockaddr *)address, sizeof(*address));
    if (result != 0) {
#ifdef _WIN32
        if (WSAGetLastError() != WSAEWOULDBLOCK) return -1;
#else
        if (errno != EINPROGRESS) return -1;
#endif
        fd_set writes, errors;
        FD_ZERO(&writes);
        FD_ZERO(&errors);
        FD_SET(bridge, &writes);
        FD_SET(bridge, &errors);
        struct timeval timeout = {2, 0};
        result = select((int)bridge + 1, NULL, &writes, &errors, &timeout);
        if (result > 0) {
            int error = 0;
#ifdef _WIN32
            int length = sizeof(error);
#else
            socklen_t length = sizeof(error);
#endif
            result = getsockopt(bridge, SOL_SOCKET, SO_ERROR, (char *)&error, &length);
            if (error) result = -1;
        } else result = -1;
    }
#ifdef _WIN32
    nonblocking = 0;
    if (ioctlsocket(bridge, FIONBIO, &nonblocking) != 0) return -1;
#else
    if (fcntl(bridge, F_SETFL, flags) != 0) return -1;
#endif
    return result;
}

int mm_dialin_route(mm_proto_t *proto, unsigned short port) {
    mm_serial_context_t *serial = proto->serial_context;
    unsigned char data[1024];
    size_t used = 0;
    int kind = -1;
    double deadline = now_seconds() + 10;
    if (serial) serial->replay_length = serial->replay_offset = 0;
    if (terminal_timeout(proto, 100) != 0) return -1;
    while (mm_manager_is_running() && now_seconds() < deadline && used < sizeof(data) && carrier(proto)) {
        ssize_t n = terminal_read(proto, data + used);
        if (n < 0) break;
        used += (size_t)n;
        for (size_t i = 0; i + 3 <= used; ++i) {
            if (data[i] != 2) continue;
            size_t noa = (size_t)data[i + 1] + 5;
            size_t management = (size_t)data[i + 2] + 1;
            if (data[i + 1] == 37 && (data[i + 2] & 0xbf) == 0x82 &&
                used - i >= noa && valid_frame(data + i, noa) &&
                data[i + 3] == 5 && data[i + 9] == 7 &&
                data[i + 17] == 4 && data[i + 22] == 1 && data[i + 24] == 7) {
                kind = 1;
                break;
            }
            /* Preserve the management parser's CRC/framing NACK behavior.
             * A TTY identification uses length 37 in this flags position:
             * do not misroute its incomplete/corrupt opening as management. */
            if (management >= 6 && used - i >= management && !(data[i + 1] & 0xc0) &&
                (valid_frame(data + i, management) || data[i + 1] != 37)) {
                kind = 0;
                break;
            }
        }
        if (kind >= 0) break;
    }
    if (kind == 0) {
        if (proto->tcp_context) {
            if (mm_tcp_replay(proto->tcp_context, data, used) != 0) return -1;
        } else {
            memcpy(serial->replay, data, used);
            serial->replay_length = used;
        }
        terminal_timeout(proto, 1000);
        return 0;
    }
    if (kind < 0) {
        fprintf(stderr, "Dial-in: session detection failed or timed out.\n");
        terminal_timeout(proto, 1000);
        return -1;
    }

#ifdef _WIN32
    WSADATA wsa;
    if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) {
        terminal_timeout(proto, 1000);
        return -1;
    }
#endif
    SOCKET bridge = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (bridge != INVALID_SOCKET) {
        struct sockaddr_in address;
        memset(&address, 0, sizeof(address));
        address.sin_family = AF_INET;
        address.sin_port = htons(port);
        address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        if (connect_bridge(bridge, &address) == 0) {
            printf("Dial-in: TTY session routed to 127.0.0.1:%u.\n", port);
#ifdef _WIN32
            DWORD timeout = 2000;
            setsockopt(bridge, SOL_SOCKET, SO_SNDTIMEO, (const char *)&timeout, sizeof(timeout));
#else
            struct timeval timeout = {2, 0};
            setsockopt(bridge, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
#ifdef SO_NOSIGPIPE
            int enabled = 1;
            setsockopt(bridge, SOL_SOCKET, SO_NOSIGPIPE, &enabled, sizeof(enabled));
#endif
#endif
            int send_flags = 0;
#ifdef MSG_NOSIGNAL
            send_flags = MSG_NOSIGNAL;
#endif
            int ok = bridge_send(bridge, data, used, send_flags) == 0;
            double started = now_seconds(), last_rx = started;
            while (ok && mm_manager_is_running() && carrier(proto) && now_seconds() - started < 3600 &&
                   now_seconds() - last_rx < 35) {
                fd_set reads;
                FD_ZERO(&reads);
                FD_SET(bridge, &reads);
                struct timeval poll = {0, 0};
                int ready = select((int)bridge + 1, &reads, NULL, NULL, &poll);
                if (ready < 0) break;
                if (ready > 0) {
                    int n = recv(bridge, (char *)data, 256, 0);
                    if (n <= 0) break;
                    if (terminal_write(proto, data, (size_t)n) != 0) { ok = 0; break; }
                }
                ssize_t n = terminal_read(proto, data);
                if (n < 0) break;
                if (n > 0) {
                    last_rx = now_seconds();
                    ok = bridge_send(bridge, data, (size_t)n, send_flags) == 0;
                }
            }
        } else fprintf(stderr, "Dial-in: Python service unavailable.\n");
#ifdef _WIN32
        closesocket(bridge);
#else
        close(bridge);
#endif
    }
#ifdef _WIN32
    WSACleanup();
#endif
    terminal_timeout(proto, 1000);
    return 1;
}
