/* Raw Millennium frames over a TCP byte stream. */
#ifdef _WIN32
#include <winsock2.h>
#include <ws2tcpip.h>
#else
#include <errno.h>
#include <fcntl.h>
#include <netdb.h>
#include <signal.h>
#include <sys/select.h>
#include <sys/socket.h>
#include <unistd.h>
typedef int SOCKET;
#define INVALID_SOCKET (-1)
#define closesocket close
#endif
#include <stdlib.h>
#include <string.h>
#include "mm_tcp.h"

#ifdef _WIN32
extern int manager_running;
#else
extern volatile sig_atomic_t manager_running;
#endif

struct mm_tcp_context {
    SOCKET listener;
    SOCKET client;
    FILE* logstream;
    unsigned char replay[1024];
    size_t replay_length;
    size_t replay_offset;
};

static int interrupted(void) {
#ifdef _WIN32
    return WSAGetLastError() == WSAEINTR;
#else
    return errno == EINTR;
#endif
}

static int would_block(void) {
#ifdef _WIN32
    return WSAGetLastError() == WSAEWOULDBLOCK;
#else
    return errno == EAGAIN || errno == EWOULDBLOCK;
#endif
}

static int nonblocking(SOCKET sock) {
#ifdef _WIN32
    u_long enabled = 1;
    return ioctlsocket(sock, FIONBIO, &enabled);
#else
    int flags = fcntl(sock, F_GETFL, 0);
    return flags < 0 ? -1 : fcntl(sock, F_SETFL, flags | O_NONBLOCK);
#endif
}

static int wait_socket(SOCKET sock, int writing, int milliseconds) {
    fd_set fds;
    struct timeval timeout = { milliseconds / 1000, (milliseconds % 1000) * 1000 };
    int result;
    FD_ZERO(&fds);
    FD_SET(sock, &fds);
#ifdef _WIN32
    result = select(0, writing ? NULL : &fds, writing ? &fds : NULL, NULL, &timeout);
#else
    result = select(sock + 1, writing ? NULL : &fds, writing ? &fds : NULL, NULL, &timeout);
#endif
    return result < 0 && interrupted() ? 0 : result;
}

mm_tcp_context_t* mm_tcp_listen(const char* endpoint, FILE* logstream) {
    mm_tcp_context_t* tcp;
    struct addrinfo hints = { 0 }, *addresses = NULL, *address;
    char host[256];
    const char* port = strrchr(endpoint, ':');
    const char* start = endpoint;
    size_t host_length;
    unsigned long port_number;
    char* end;
#ifdef _WIN32
    WSADATA wsa;
#endif

    if (!port || port == endpoint || !port[1]) goto invalid;
    host_length = (size_t)(port - endpoint);
    if (*start == '[') {
        if (host_length < 3 || port[-1] != ']') goto invalid;
        start++;
        host_length -= 2;
    } else if (memchr(start, ':', host_length)) goto invalid;
    if (host_length >= sizeof(host)) goto invalid;
    memcpy(host, start, host_length);
    host[host_length] = '\0';
    for (end = (char*)port + 1; *end; end++) {
        if (*end < '0' || *end > '9') goto invalid;
    }
    port_number = strtoul(port + 1, &end, 10);
    if (*end || port_number < 1 || port_number > 65535) goto invalid;

    tcp = (mm_tcp_context_t*)calloc(1, sizeof(*tcp));
    if (!tcp) return NULL;
    tcp->listener = tcp->client = INVALID_SOCKET;
    tcp->logstream = logstream;
#ifdef _WIN32
    if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) {
        free(tcp);
        return NULL;
    }
#endif
    hints.ai_family = AF_UNSPEC;
    hints.ai_socktype = SOCK_STREAM;
    hints.ai_flags = AI_PASSIVE;
    if (getaddrinfo(strcmp(host, "*") == 0 ? NULL : host, port + 1, &hints, &addresses) != 0) {
        fprintf(stderr, "Unable to resolve TCP bind address: %s\n", endpoint);
        mm_tcp_close(tcp);
        return NULL;
    }
    for (address = addresses; address; address = address->ai_next) {
        int reuse = 1;
        tcp->listener = socket(address->ai_family, address->ai_socktype, address->ai_protocol);
        if (tcp->listener == INVALID_SOCKET) continue;
#ifndef _WIN32
        setsockopt(tcp->listener, SOL_SOCKET, SO_REUSEADDR, (const char*)&reuse, sizeof(reuse));
#else
        setsockopt(tcp->listener, SOL_SOCKET, SO_EXCLUSIVEADDRUSE, (const char*)&reuse, sizeof(reuse));
#endif
        if (bind(tcp->listener, address->ai_addr, (int)address->ai_addrlen) == 0 &&
            listen(tcp->listener, 1) == 0 && nonblocking(tcp->listener) == 0) break;
        closesocket(tcp->listener);
        tcp->listener = INVALID_SOCKET;
    }
    freeaddrinfo(addresses);
    if (tcp->listener == INVALID_SOCKET) {
        fprintf(stderr, "Unable to listen on TCP %s\n", endpoint);
        mm_tcp_close(tcp);
        return NULL;
    }
    printf("Listening for raw Millennium frames on TCP %s\n", endpoint);
    return tcp;
invalid:
    fprintf(stderr, "Invalid TCP endpoint '%s'; use host:port or [IPv6]:port (port 1-65535).\n", endpoint);
    return NULL;
}

int mm_tcp_accept(mm_tcp_context_t* tcp) {
    int ready = wait_socket(tcp->listener, 0, 1000);
    if (ready <= 0) return ready;
    tcp->client = accept(tcp->listener, NULL, NULL);
    if (tcp->client == INVALID_SOCKET) return would_block() || interrupted() ? 0 : -1;
    tcp->replay_length = tcp->replay_offset = 0;
    if (nonblocking(tcp->client) != 0) {
        mm_tcp_disconnect(tcp);
        return -1;
    }
#ifdef SO_NOSIGPIPE
    {
        int enabled = 1;
        if (setsockopt(tcp->client, SOL_SOCKET, SO_NOSIGPIPE, &enabled, sizeof(enabled)) != 0) {
            mm_tcp_disconnect(tcp);
            return -1;
        }
    }
#endif
    return 1;
}

int mm_tcp_read(mm_tcp_context_t* tcp, unsigned char* byte, int inject_error) {
    return mm_tcp_read_timeout(tcp, byte, inject_error, 1000);
}

int mm_tcp_replay(mm_tcp_context_t* tcp, const unsigned char* bytes, size_t length) {
    if (!tcp || length > sizeof(tcp->replay)) return -1;
    memcpy(tcp->replay, bytes, length);
    tcp->replay_length = length;
    tcp->replay_offset = 0;
    return 0;
}

int mm_tcp_read_timeout(mm_tcp_context_t* tcp, unsigned char* byte, int inject_error, int milliseconds) {
    int ready, result;
    if (!manager_running || tcp->client == INVALID_SOCKET) return -1;
    if (tcp->replay_offset < tcp->replay_length) {
        *byte = tcp->replay[tcp->replay_offset++];
        if (inject_error) *byte = (unsigned char)~*byte;
        return 1;
    }
    ready = wait_socket(tcp->client, 0, milliseconds);
    if (ready <= 0) return ready;
    result = recv(tcp->client, (char*)byte, 1, 0);
    if (result < 0) return would_block() || interrupted() ? 0 : -1;
    if (result == 0) return -1; /* EOF is a disconnect, not a timeout. */
    if (inject_error) *byte = (unsigned char)~*byte;
    if (tcp->logstream) fprintf(tcp->logstream, "UART: RX: %02X\n", *byte);
    return 1;
}

int mm_tcp_write(mm_tcp_context_t* tcp, const void* buffer, size_t length) {
    const unsigned char* bytes = (const unsigned char*)buffer;
    size_t sent = 0;
    int timeouts = 0;
    while (sent < length && manager_running && tcp->client != INVALID_SOCKET) {
        int ready = wait_socket(tcp->client, 1, 1000);
        int result;
        if (ready < 0 || (ready == 0 && ++timeouts > 10)) return -1;
        if (ready == 0) continue;
#ifdef MSG_NOSIGNAL
        result = (int)send(tcp->client, (const char*)bytes + sent, (int)(length - sent), MSG_NOSIGNAL);
#else
        result = (int)send(tcp->client, (const char*)bytes + sent, (int)(length - sent), 0);
#endif
        if (result < 0 && (would_block() || interrupted())) continue;
        if (result <= 0) return -1;
        if (tcp->logstream) {
            for (int i = 0; i < result; i++) fprintf(tcp->logstream, "UART: TX: %02X\n", bytes[sent + i]);
        }
        sent += (size_t)result;
        timeouts = 0;
    }
    return sent == length ? 0 : -1;
}

void mm_tcp_disconnect(mm_tcp_context_t* tcp) {
    if (tcp && tcp->client != INVALID_SOCKET) {
        closesocket(tcp->client);
        tcp->client = INVALID_SOCKET;
    }
    if (tcp) tcp->replay_length = tcp->replay_offset = 0;
}

void mm_tcp_close(mm_tcp_context_t* tcp) {
    if (!tcp) return;
    mm_tcp_disconnect(tcp);
    if (tcp->listener != INVALID_SOCKET) closesocket(tcp->listener);
#ifdef _WIN32
    WSACleanup();
#endif
    free(tcp);
}
