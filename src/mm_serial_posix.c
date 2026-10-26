/*
 * POSIX serial port library, part of mm_manager.
 *
 * www.github.com/hharte/mm_manager
 *
 * Copyright (c) 2020-2023, Howard M. Harte
 */

#include <stdio.h>  /* Standard input/output definitions */
#include <stdlib.h>
#include <stdint.h>
#include <string.h> /* String function definitions */
#include <fcntl.h>  /* File control definitions */
#include <errno.h>  /* Error number definitions */
#include <termios.h>
#include <unistd.h>
#include <sys/ioctl.h>
#include <poll.h>

/*
 * Open serial port specified in modem_dev.
 *
 * Returns the file descriptor on success or -1 on error.
 */
int platform_open_serial(const char *modem_dev) {
    int fd;

    fd = open(modem_dev, O_RDWR | O_NOCTTY | O_NDELAY | O_SYNC);

    if (fd != -1) {
        if (fcntl(fd, F_SETFL, 0) != 0) {
            close(fd);
            return -1;
        }
    }
    return fd;
}

/* Initialize Serial Port options */
int platform_init_serial(int fd, int baudrate) {
    struct termios options;
    speed_t speed;

    switch (baudrate) {
        case 1200:
            speed = B1200;
            break;
        case 2400:
            speed = B2400;
            break;
        case 4800:
            speed = B4800;
            break;
        case 9600:
            speed = B9600;
            break;
        case 19200:
            speed = B19200;
            break;
        case 38400:
            speed = B38400;
            break;
        case 57600:
            speed = B57600;
            break;
        case 115200:
            speed = B115200;
            break;
        case 230400:
            speed = B230400;
            break;
        default:
            printf("%s: Invalid baud rate: %d, defaulting to 1200.\n",
                   __func__, baudrate);
            speed = B1200;
            break;
    }

    /* Set serial port options. */
    cfmakeraw(&options);
    options.c_cflag |= (CS8 | CLOCAL | CREAD | CRTSCTS | HUPCL);
    options.c_cflag &= ~(PARODD | CSTOPB);
    options.c_lflag &= ~(ECHOCTL | ECHOPRT | FLUSHO);
    options.c_iflag |= (IGNPAR | IGNBRK);

#ifdef __APPLE__
    options.c_cflag &= ~(CDTR_IFLOW | CDSR_OFLOW);
    options.c_lflag &= ~(NOKERNINFO);
#endif /* __APPLE__ */

    options.c_cc[VMIN]  = 0;
    options.c_cc[VTIME] = 10;

    cfsetispeed(&options, speed);
    cfsetospeed(&options, speed);

    tcsetattr(fd, TCSANOW, &options);

    return 0;
}

int platform_close_serial(int fd) {
    return close(fd);
}

int platform_serial_read_timeout(int fd, int milliseconds) {
    struct termios options;
    if (tcgetattr(fd, &options) != 0) return -1;
    options.c_cc[VMIN] = 0;
    options.c_cc[VTIME] = (cc_t)((milliseconds + 99) / 100);
    if (tcsetattr(fd, TCSANOW, &options) != 0) return -1;
    int flags = fcntl(fd, F_GETFL, 0);
    if (flags < 0) return -1;
    /* The bridge must also escape a stalled CTS/output queue. */
    return fcntl(fd, F_SETFL, milliseconds == 1000 ?
        (flags & ~O_NONBLOCK) : (flags | O_NONBLOCK));
}

ssize_t platform_read_serial(int fd, void *buf, size_t count) {
    ssize_t n = read(fd, buf, count);
    if ((n == 0 && (fcntl(fd, F_GETFL, 0) & O_NONBLOCK)) ||
        (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK))) {
        struct pollfd descriptor = {fd, POLLIN, 0};
        int ready = poll(&descriptor, 1, 100);
        if (ready <= 0) return ready;
        if (descriptor.revents & (POLLERR | POLLHUP | POLLNVAL)) return -1;
        n = read(fd, buf, count);
        if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) return 0;
    }
    return n;
}

ssize_t platform_write_serial(int fd, const void *buf, size_t count) {
    ssize_t n = write(fd, buf, count);
    if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
        struct pollfd descriptor = {fd, POLLOUT, 0};
        if (poll(&descriptor, 1, 2000) <= 0) return -1;
        n = write(fd, buf, count);
    }
    return n;
}

int platform_drain_serial(int fd) {
    return tcdrain(fd);
}

int platform_flush_serial(int fd) {
    return tcflush(fd, TCIOFLUSH);
}

int platform_serial_set_dtr(int fd, int set) {
    int status = 0;

    ioctl(fd, TIOCMGET, &status);

    if (set) {
        status |= TIOCM_DTR;
    } else {
        status &= ~TIOCM_DTR;
    }
    ioctl(fd, TIOCMSET, status);

    return 0;
}

int platform_serial_get_modem_status(int fd) {
    int status = 0;
    int retstatus = 0;

    ioctl(fd, TIOCMGET, &status);

    if (status & TIOCM_CTS) {
        retstatus |= 0x0010;
    }

    if (status & TIOCM_DSR) {
        retstatus |= 0x0020;
    }

    if (status & TIOCM_RNG) {
        retstatus |= 0x0040;
    }

    if (status & TIOCM_CAR) {
        retstatus |= 0x0080;
    }

    return retstatus;
}
