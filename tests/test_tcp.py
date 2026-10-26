"""Loopback integration checks: python3 tests/test_tcp.py /path/to/mm_manager."""

import datetime
import pathlib
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest


BINARY = str(pathlib.Path(sys.argv.pop(1)).resolve())
TERMINAL = bytes.fromhex("5551234567")


def crc16(data):
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0xA001 if crc & 1 else 0)
    return crc


def frame(flags, payload=b""):
    data = bytes([2, flags, len(payload) + 5]) + payload
    return data + struct.pack("<H", crc16(data)) + b"\x03"


def receive_exact(peer, length):
    result = b""
    while len(result) < length:
        chunk = peer.recv(length - len(result))
        if not chunk:
            raise AssertionError("Unexpected TCP EOF")
        result += chunk
    return result


def receive_frame(peer):
    header = receive_exact(peer, 3)
    packet = header + receive_exact(peer, header[2] - 2)
    assert packet[0] == 2 and packet[-1] == 3, packet.hex()
    assert crc16(packet[:-3]) == int.from_bytes(packet[-3:-1], "little")
    return packet


class TCPIntegration(unittest.TestCase):
    extra_options = ()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        shutil.copytree(
            pathlib.Path(__file__).resolve().parents[1] / "config",
            pathlib.Path(self.directory.name) / "config",
        )
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        self.process = subprocess.Popen(
            [BINARY, *self.start_options()],
            cwd=self.directory.name, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        self.addCleanup(self.stop)
        self.peer = self.connect()

    def start_options(self):
        return ["-T", f"127.0.0.1:{self.port}", "-n", "5551234567",
                "-l", "session.dlog", "-p", "session.pcap", *self.extra_options]

    def connect(self):
        deadline = time.monotonic() + 5
        while True:
            try:
                peer = socket.create_connection(("127.0.0.1", self.port), timeout=2)
                self.addCleanup(peer.close)
                return peer
            except ConnectionRefusedError:
                if self.process.poll() is not None or time.monotonic() > deadline:
                    raise AssertionError("TCP listener failed to start")
                time.sleep(0.05)

    def stop(self):
        if self.process.poll() is None:
            self.process.send_signal(signal.SIGINT)
        try:
            output, _ = self.process.communicate(timeout=4)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.communicate()
            self.fail("Manager failed to stop within four seconds")
        self.assertEqual(self.process.returncode, 0, output.decode(errors="replace"))

    def time_response(self, peer, rx_sequence, tx_sequence):
        self.assertEqual(receive_frame(peer), frame(8 | rx_sequence))
        response = receive_frame(peer)
        self.assertEqual(response[1], tx_sequence)
        self.assertEqual(response[3:8], TERMINAL)
        self.assertEqual(response[8], 0x14)
        self.assertEqual(response[9], datetime.datetime.now().year - 1900)
        self.assertEqual(response[-4], 0x0D)

    def test_fragmented_and_coalesced_frames(self):
        request = frame(0, TERMINAL + b"\x24")
        for byte in request:
            self.peer.sendall(bytes([byte]))
            time.sleep(0.005)
        self.time_response(self.peer, 0, 0)
        # ACK and next request share a single TCP write.
        self.peer.sendall(frame(8) + frame(1, TERMINAL + b"\x24"))
        self.time_response(self.peer, 1, 1)
        self.peer.sendall(frame(9))
        self.peer.close()
        self.stop()
        log = pathlib.Path(self.directory.name, "session.dlog").read_text()
        self.assertIn("UART: RX: 02", log)
        self.assertIn("UART: TX: 02", log)
        self.assertGreater(pathlib.Path(self.directory.name, "session.pcap").stat().st_size, 24)

    def test_crc_nack_and_reconnect(self):
        bad = bytearray(frame(0, TERMINAL + b"\x24"))
        bad[-3] ^= 1
        self.peer.sendall(bad)
        self.assertEqual(receive_frame(self.peer), frame(0))  # NACK.
        self.peer.sendall(frame(0))  # Terminal NACK completes retry handshake.
        self.peer.sendall(frame(0, TERMINAL + b"\x24"))
        self.time_response(self.peer, 0, 0)
        self.peer.sendall(frame(8) + frame(0x20))
        self.assertEqual(self.peer.recv(1), b"")
        self.peer.close()
        # Abrupt EOF partway through a frame must also free the active session.
        peer = self.connect()
        peer.sendall(b"\x02\x00")
        peer.close()
        peer = self.connect()
        peer.sendall(frame(0, TERMINAL + b"\x24"))
        self.time_response(peer, 0, 0)  # Fresh outgoing sequence.
        peer.sendall(frame(8))

    def test_shutdown_while_waiting_for_frame(self):
        self.peer.sendall(b"\x02")
        self.stop()

    def test_outgoing_nack_retries_same_frame(self):
        self.peer.sendall(frame(0, TERMINAL + b"\x24"))
        self.assertEqual(receive_frame(self.peer), frame(8))
        response = receive_frame(self.peer)
        self.peer.sendall(frame(0))
        self.assertEqual(receive_frame(self.peer), response)
        self.peer.sendall(frame(8))

    def test_shutdown_while_accepting(self):
        self.peer.close()
        time.sleep(0.1)
        self.stop()

    def test_invalid_options(self):
        for options in (
            ["-T", "127.0.0.1:0"], ["-T", "127.0.0.1:65536"],
            ["-T", "127.0.0.1:abc"], ["-T", "127.0.0.1"],
            ["-T", "127.0.0.1:12345", "-m"],
            ["-T", "127.0.0.1:12345", "-f", "unused"],
        ):
            with self.subTest(options=options):
                result = subprocess.run(
                    [BINARY, *options, "-n", "5551234567"],
                    cwd=self.directory.name, capture_output=True, timeout=4,
                )
                self.assertNotEqual(result.returncode, 0)


class DotenvTCPIntegration(TCPIntegration):
    """Exercise configuration-only startup through the real protocol stack."""
    def start_options(self):
        tables = pathlib.Path(self.directory.name, "tables", "default")
        tables.mkdir(parents=True)
        # These IDs used to be excluded by hardcoded firmware rules.
        (tables / "mm_table_51.bin").write_bytes(bytes(818))
        (tables / "mm_table_37.bin").write_bytes(bytes(228))
        pathlib.Path(self.directory.name, ".env").write_text(
            f"MM_TCP_ENDPOINT=127.0.0.1:{self.port}\n"
            "MM_NCC_NUMBER=5551234567\nMM_LOG_FILE=session.dlog\n"
            "MM_PCAP_FILE=session.pcap\n"
            "MM_TABLES_06CNB02=81\nMM_TABLES_12AAV08=55\n")
        return []

    def test_firmware_selection_and_fallback_after_reconnect(self):
        for firmware, expected in [(b"06CNB02", 81), (b"12AAV08", 55),
                                   (b"06CNB03", 21)]:
            with self.subTest(firmware=firmware):
                version = b"\x3c" + firmware + bytes(4) + b"UNKNOWN" + bytes(9)
                self.peer.sendall(frame(0, TERMINAL + version + b"\x2c\x04"))
                self.assertEqual(receive_frame(self.peer), frame(8))
                response = receive_frame(self.peer)
                self.assertEqual(response[8], 0x12)  # Table update notification.
                self.peer.sendall(frame(8))
                table = receive_frame(self.peer)
                self.assertEqual(table[8], expected)
                self.peer.close()
                self.peer = self.connect()


def tty_frame(command, data=b""):
    payload = bytes([command]) + bytes(data)
    packet = bytes([2, len(payload)]) + payload
    return packet + struct.pack("<H", crc16(packet)) + b"\x03"


def tty_receive(peer):
    header = receive_exact(peer, 2)
    packet = header + receive_exact(peer, header[1] + 3)
    assert packet[-1] == 3 and crc16(packet[:-3]) == int.from_bytes(packet[-3:-1], "little")
    return packet[2], packet[3:-3]


class MixedTCPIntegration(TCPIntegration):
    """Run the existing management checks with classification enabled too."""
    def setUp(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        source = pathlib.Path(__file__).resolve().parents[1] / "src" / "dialin"
        self.controller = subprocess.Popen(
            [sys.executable, "-u", str(source / "server.py"), "--app", str(source / "example.py"),
             "--port", str(port)], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        self.addCleanup(self.stop_controller)
        deadline = time.monotonic() + 5
        while True:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=.2):
                    break
            except ConnectionRefusedError:
                if self.controller.poll() is not None or time.monotonic() > deadline:
                    raise AssertionError("TTY controller failed to start")
                time.sleep(.02)
        time.sleep(.05)  # Allow the readiness probe's EOF session to close.
        self.extra_options = ("-j", str(port))
        super().setUp()

    def stop_controller(self):
        if self.controller.poll() is None:
            self.controller.terminate()
        try:
            self.controller.communicate(timeout=4)
        except subprocess.TimeoutExpired:
            self.controller.kill()
            self.controller.communicate()
            self.fail("TTY controller did not stop")

    def tty_handshake(self, firmware=b"06CNB02"):
        identity = bytes.fromhex(
            "05 12 34 56 78 90 07") + firmware + bytes.fromhex(
            "04 56 31 2e 30 01 02 07 20 26 10 24 15 42 17 21 10 01 01 23 01 00")
        opening = tty_frame(0x82, identity)
        for byte in opening:
            self.peer.sendall(bytes([byte]))
        if firmware == b"UNKNOWN":
            return
        self.assertEqual(tty_receive(self.peer), (1, b"\x80"))
        for command, payload in [(0xD5,b"\x19"), (0x96,bytes(8)), (0xC8,bytes.fromhex("12 34 56 78 90"))]:
            self.peer.sendall(tty_frame(command, payload))
            self.assertEqual(tty_receive(self.peer), (1, b"\x80"))
        command, challenge = tty_receive(self.peer)
        self.assertEqual(command, 0x17)
        pieces = [bytes([2,4,6,8,0]), bytes([15,42,17]), bytes([1,2,3,4,5,6,7,8,9,10]), challenge]
        result = crc16(b"".join(pieces[i] for i in (2,0,3,1)))
        self.peer.sendall(tty_frame(0x83, result.to_bytes(2,"little") + bytes(6)))
        self.assertEqual(tty_receive(self.peer), (1, b"\x80"))

    def tty_screen_until(self, wanted):
        for _ in range(100):
            command, payload = tty_receive(self.peer)
            if command == 0x89:
                self.fail("TTY call stopped before expected screen")
            if command & 0x80:
                self.peer.sendall(tty_frame(1,b"\x80"))
            if command == 0x85 and wanted.encode() in payload[2:]:
                return
        self.fail(f"TTY screen never contained {wanted}")

    def test_tty_application_then_management_on_same_listener(self):
        self.tty_handshake()
        self.tty_screen_until("MILLENNIUM ONLINE")
        self.peer.sendall(tty_frame(0xD0,b"\x02"))
        self.tty_screen_until("Mailbox:")
        self.peer.sendall(tty_frame(0x90,bytes([1,2,3,12])))
        self.tty_screen_until("Mailbox 123")
        self.peer.sendall(tty_frame(0xD0,b"\x01"))
        self.tty_screen_until("MILLENNIUM ONLINE")
        self.peer.sendall(tty_frame(0x90,b"\x0a"))
        self.tty_screen_until("Goodbye!")
        for _ in range(20):
            command, _ = tty_receive(self.peer)
            if command == 0x89: break
            if command & 0x80: self.peer.sendall(tty_frame(1,b"\x80"))
        else:
            self.fail("TTY service did not stop")
        self.assertEqual(self.peer.recv(1), b"")
        self.peer.close()
        peer = self.connect()
        peer.sendall(frame(0, TERMINAL + b"\x24"))
        self.time_response(peer, 0, 0)
        peer.sendall(frame(8))

    def test_unsupported_tty_profile_releases_listener(self):
        self.tty_handshake(b"UNKNOWN")
        self.assertEqual(self.peer.recv(1), b"")
        self.peer.close()
        peer = self.connect()
        peer.sendall(frame(0, TERMINAL + b"\x24"))
        self.time_response(peer, 0, 0)
        peer.sendall(frame(8))


if __name__ == "__main__":
    unittest.main()
