#ifndef MM_TCP_H_
#define MM_TCP_H_

#include <stddef.h>
#include <stdio.h>

typedef struct mm_tcp_context mm_tcp_context_t;

mm_tcp_context_t* mm_tcp_listen(const char* endpoint, FILE* logstream);
/* Accept/read return 0 on a one-second timeout, -1 on error, 1 on success. */
int mm_tcp_accept(mm_tcp_context_t* tcp);
int mm_tcp_read(mm_tcp_context_t* tcp, unsigned char* byte, int inject_error);
int mm_tcp_read_timeout(mm_tcp_context_t* tcp, unsigned char* byte, int inject_error, int milliseconds);
/* Replay classification lookahead once, without logging the same input twice. */
int mm_tcp_replay(mm_tcp_context_t* tcp, const unsigned char* bytes, size_t length);
int mm_tcp_write(mm_tcp_context_t* tcp, const void* buffer, size_t length);
void mm_tcp_disconnect(mm_tcp_context_t* tcp);
void mm_tcp_close(mm_tcp_context_t* tcp);

#endif
