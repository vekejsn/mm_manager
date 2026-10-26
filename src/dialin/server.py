"""Millennium TTY service. Run: python src/dialin/server.py --app src/dialin/example.py.

The loopback TCP connection carries unmodified modem bytes. Applications run
in child processes; only this process implements framing and supervision.
"""
import argparse
import asyncio
import contextlib
import json
import logging
import secrets
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from protocol import auth_result, control, cursor, decode, encode, fill, printable, text, text_and_cursor

LOG = logging.getLogger("dialin")
SUPPORTED_FIRMWARE = {'NOA1X04', 'NNK1F05', '06CNB02'}
SEQUENCED_FIRMWARE = {'NNK1F05', '06CNB02'}


class SessionError(Exception):
    pass


@dataclass
class Limits:
    handshake_stage: float = 10
    handshake_total: float = 45
    frame_gap: float = 2
    ack: float = 2
    attempts: int = 3
    ping: float = 8
    silence: float = 30
    idle: float = 120
    idle_grace: float = 30
    maximum: float = 900
    app_start: float = 5
    app_heartbeat: float = 10
    shutdown: float = 3
    line_bps: int = 1200


def bcd(value):
    if value >> 4 > 9 or value & 15 > 9:
        raise SessionError("Invalid BCD field")
    return (value >> 4) * 10 + (value & 15)


def identification(data):
    if len(data) != 36 or [data[i] for i in (0, 6, 14, 19, 21)] != [5, 7, 4, 1, 7]:
        raise SessionError("Invalid NOA identification")
    terminal = ''.join(f'{bcd(value):02d}' for value in data[1:6])
    timestamp = bytes(bcd(value) for value in data[22:29])
    if timestamp[-3] > 23 or timestamp[-2] > 59 or timestamp[-1] > 59:
        raise SessionError("Invalid identification time")
    year = timestamp[0] * 100 + timestamp[1] - 1900
    if not 0 <= year <= 255:
        raise SessionError("Identification year is outside the firmware time range")
    return {"terminal_id": terminal, "firmware": data[7:14].decode('ascii'),
            "raw_identification": data.hex()}, data[1:6], bytes([year]) + timestamp[2:]


def destination_digits(data):
    """Ordinary decimal packed destinations; extended symbols rejected explicitly."""
    result = bytearray()
    for byte in data:
        for digit in (byte >> 4, byte & 15):
            if digit == 14:
                return bytes(result)
            if digit > 9:
                raise SessionError("Extended destination symbols are not supported")
            result.append(digit or 10)
    return bytes(result)


def line(value):
    if not isinstance(value, str) or len(value) > 20 or any(not 32 <= ord(c) <= 126 for c in value):
        raise SessionError("Screen rows must contain at most 20 printable ASCII characters")
    return value.ljust(20)


class Session:
    def __init__(self, reader, writer, app, limits=None):
        self.reader, self.writer, self.app = reader, writer, app
        self.limits = limits or Limits()
        self.started = self.last_rx = self.last_tx = self.last_key = time.monotonic()
        self.ack_queue = asyncio.Queue(8)
        self.keys = asyncio.Queue(64)
        self.requests = asyncio.Queue(8)
        self.write_lock = asyncio.Lock()
        self.command_lock = asyncio.Lock()
        self.display_lock = asyncio.Lock()
        self.available = asyncio.Event()
        self.available.set()
        self.local_since = None
        self.screen = (' ' * 20, ' ' * 20)
        self.cursor_state = (0, 0, False)
        self.redraw = asyncio.Event()
        self.process = None
        self.tasks = []
        self.bad_frames = 0
        self.metadata = {}
        self.warning = False
        self.maximum_warning = False
        self.ready = False
        self.sequenced = False
        self.wire_command = None
        self.last_request = None

    async def send(self, packet):
        async with self.write_lock:
            self.writer.write(packet)
            await asyncio.wait_for(self.writer.drain(), 2)
            self.last_tx = time.monotonic()

    async def status(self, value=0x80):
        await self.send(encode(1, [value]))

    async def frame(self):
        while True:
            first = await self.reader.readexactly(1)
            if first != b'\x02':
                self.bad_frames += 1
            else:
                try:
                    length = (await asyncio.wait_for(self.reader.readexactly(1), self.limits.frame_gap))[0]
                    if not 1 <= length <= 251:
                        raise ValueError("Invalid frame length")
                    # Reset the inter-byte deadline for each byte, not each frame.
                    tail = bytearray()
                    for _ in range(length + 3):
                        tail.extend(await asyncio.wait_for(self.reader.readexactly(1), self.limits.frame_gap))
                    packet = first + bytes([length]) + tail
                    try:
                        command, data = decode(packet)
                    except ValueError:
                        await self.status(0x20 if packet[-1] != 3 else 0x40)
                        raise
                    self.last_rx = time.monotonic()
                    self.bad_frames = 0
                    self.wire_command = command
                    if self.sequenced:
                        command &= 0xbf
                    return command, data
                except (ValueError, asyncio.TimeoutError):
                    self.bad_frames += 1
            if self.bad_frames >= 16:
                raise SessionError("Too many malformed frames")

    async def handshake(self):
        saved = {}
        fields = [(0x82, 36), (0x95, 1), (0x96, 8), (0x88, None)]
        for expected, length in fields:
            deadline = time.monotonic() + self.limits.handshake_stage
            while True:
                command, data = await asyncio.wait_for(self.frame(), max(0, deadline - time.monotonic()))
                if expected == 0x82 and command == 0xC2 and data[7:14] in (b'NNK1F05', b'06CNB02'):
                    command = 0x82
                if command in saved and saved[command] == data:
                    await self.status()
                    continue
                if command != expected or (length is not None and len(data) != length):
                    raise SessionError(f"Unexpected handshake command {command:02x}")
                if command == 0x82:
                    self.metadata, terminal_id, timestamp = identification(data)
                    if self.metadata['firmware'] not in SUPPORTED_FIRMWARE:
                        raise SessionError("Unsupported TTY firmware profile")
                    self.sequenced = self.metadata['firmware'] in SEQUENCED_FIRMWARE
                    self.metadata['sequence_bit'] = self.sequenced
                elif command == 0x95:
                    if not 1 <= data[0] <= 0x23:
                        raise SessionError("Unsupported service mode")
                    self.metadata['mode'] = data[0]
                    if data[0] <= 32:
                        self.metadata['selector'] = data[0]
                elif command == 0x96:
                    self.metadata['call_record'] = data.hex()
                else:
                    digits = destination_digits(data)
                    self.metadata['destination'] = ''.join(str(d % 10) for d in digits)
                saved[command] = data
                if self.sequenced:
                    self.last_request = (self.wire_command & 0x40, command, data)
                await self.status()
                break
        challenge = secrets.token_bytes(4)
        expected = auth_result(terminal_id, timestamp, digits, challenge).to_bytes(2, 'little') + bytes(6)
        response = None
        for _ in range(self.limits.attempts):
            await self.send(encode(0x17, challenge))
            deadline = time.monotonic() + self.limits.handshake_stage
            try:
                while True:
                    command, data = await asyncio.wait_for(self.frame(), max(0, deadline - time.monotonic()))
                    if command == 0x88 and data == saved[0x88]:
                        await self.status()
                        continue
                    if command != 0x83 or data != expected:
                        raise SessionError("Authentication response mismatch")
                    response = data
                    if self.sequenced:
                        self.last_request = (self.wire_command & 0x40, command, data)
                    await self.status()
                    break
            except asyncio.TimeoutError:
                continue
            break
        if response is None:
            raise SessionError("Authentication timeout")
        self.auth_response = response
        self.last_key = time.monotonic()
        LOG.info("TTY ready: %s", self.metadata)

    async def receive(self):
        while True:
            command, data = await self.frame()
            if command == 1:
                if len(data) != 1:
                    raise SessionError("Invalid ACK")
                self.ack_queue.put_nowait(data[0])
                continue
            if command == 0x0e:
                if data:
                    raise SessionError("Invalid ping")
                continue
            if command & 0x80:
                await self.status()
            if self.sequenced:
                request = (self.wire_command & 0x40, command, data)
                if request == self.last_request:
                    continue  # Re-ACK lost-ACK retries, without repeating a key.
                self.last_request = request
            if command == 0x83 and data == self.auth_response:
                continue  # Final handshake ACK may have been lost.
            if command in (0x90, 0x91):
                for key in data:
                    self.keys.put_nowait(key)
                self.last_key = time.monotonic()
                if self.warning:
                    self.warning = False
                    self.redraw.set()
            elif command == 0x84 and data == b'\x02':
                if self.local_since is None:
                    self.local_since = time.monotonic()
                self.available.clear()
            elif command == 0x84 and data == b'\x03':
                if self.local_since is not None:
                    self.last_key += time.monotonic() - max(self.local_since, self.last_key)
                self.local_since = None
                self.available.set()
                self.redraw.set()
            else:
                raise SessionError(f"Unsupported terminal request {command:02x}")

    async def request(self, packet):
        async with self.command_lock:
            await self.available.wait()
            while not self.ack_queue.empty():
                self.ack_queue.get_nowait()
            for _ in range(self.limits.attempts):
                await self.available.wait()
                await self.send(packet)
                # Allow modem serialization at the configured negotiated speed.
                timeout = self.limits.ack + len(packet) * 10 / self.limits.line_bps + .2
                try:
                    status = await asyncio.wait_for(self.ack_queue.get(), timeout)
                    if status == 0x80:
                        return
                    if status not in (0x20, 0x40):
                        raise SessionError(f"Unexpected ACK status {status:02x}")
                except asyncio.TimeoutError:
                    pass
            raise SessionError("Command ACK retry limit reached")

    async def draw(self, rows, cursor_state):
        for row, value in enumerate(rows):
            await self.request(text(0, row, line(value)))
        await self.request(cursor(*cursor_state))

    async def redraw_loop(self):
        while True:
            await self.redraw.wait()
            self.redraw.clear()
            await self.draw_desired()

    async def draw_desired(self):
        async with self.display_lock:
            await self.draw_desired_locked()

    async def draw_desired_locked(self):
        rows, position = self.screen, self.cursor_state
        if self.maximum_warning:
            rows, position = ("Call ending soon", "30 seconds remaining"), (0, 0, False)
        if self.warning:
            rows, position = ("Still there?", "Press any key"), (0, 0, False)
        await self.draw(rows, position)

    async def drawing(self, message):
        """Apply a validated operation to retained cells and the physical display.

        Entire operations share the display lock, including ACK waits. The
        receive task still handles takeover and keys independently of this lock.
        """
        async with self.display_lock:
            rows = list(self.screen)
            position = self.cursor_state
            op = message['op']
            if op == 'screen':
                rows = [line(message['line1']), line(message['line2'])]
                packets = [text(0, row, value) for row, value in enumerate(rows)]
                packets.append(cursor(*position))
            elif op == 'fill':
                char = message['char']
                if len(printable(char)) != 1:
                    raise SessionError('Fill requires exactly one printable ASCII character')
                packets = [fill(ord(char))]
                rows = [char * 20, char * 20]
                position = (0, 0, position[2])  # Fill repositions, but preserves visibility.
            elif op in ('write_row', 'write_text', 'text_and_cursor'):
                row = message['row']
                col = 0 if op == 'write_row' else message['col']
                value = line(message['text']) if op == 'write_row' else message['text']
                packet = text(col, row, value)  # Validate before indexing/updating rows.
                if op == 'text_and_cursor':
                    position = (message['cursor_col'], message['cursor_row'], bool(message.get('visible')))
                    packet = text_and_cursor(col, row, value, *position, erase=bool(message.get('erase')))
                    packets = [packet]
                else:
                    packets = [packet, cursor(*position)]
                rows[row] = rows[row][:col] + value + rows[row][col + len(value):]
            elif op == 'cursor':
                position = (message['col'], message['row'], bool(message.get('visible')))
                packets = [cursor(*position, erase=bool(message.get('erase')))]
            else:
                raise SessionError('Invalid drawing operation')
            if op in ('cursor', 'text_and_cursor') and message.get('erase'):
                col, row, _ = position
                # Combined commands draw text BEFORE erasing the final cursor cell.
                rows[row] = rows[row][:col] + ' ' + rows[row][col + 1:]
            self.screen, self.cursor_state = tuple(rows), position
            if self.warning or self.maximum_warning:
                # Retain app updates without overwriting a service warning.
                await self.draw_desired_locked()
            else:
                for packet in packets:
                    await self.request(packet)

    async def supervise(self):
        while True:
            await asyncio.sleep(.1)
            now = time.monotonic()
            if now - self.last_rx >= self.limits.silence:
                raise SessionError("Receive silence timeout")
            if now - self.started >= self.limits.maximum:
                raise SessionError("Maximum call duration reached")
            if now - self.last_tx >= self.limits.ping:
                await self.send(encode(0x0e))
            if self.process and now - self.last_heartbeat > self.limits.app_heartbeat:
                raise SessionError("Application heartbeat timeout")
            if self.available.is_set():
                idle = now - self.last_key
                if idle >= self.limits.idle + self.limits.idle_grace:
                    raise SessionError("User inactivity timeout")
                if idle >= self.limits.idle and not self.warning:
                    self.warning = True
                    self.redraw.set()
                if not self.maximum_warning and self.limits.maximum - (now - self.started) <= 30:
                    self.maximum_warning = True
                    self.redraw.set()

    async def app_reader(self):
        while True:
            raw = await self.process.stdout.readline()
            if not raw:
                raise SessionError("Application exited")
            message = json.loads(raw)
            if message == {"op": "heartbeat"}:
                self.last_heartbeat = time.monotonic()
            else:
                self.requests.put_nowait(message)

    async def respond(self, value):
        self.process.stdin.write((json.dumps(value) + '\n').encode())
        await asyncio.wait_for(self.process.stdin.drain(), 2)

    async def app_requests(self):
        while True:
            message = await self.requests.get()
            op = message.get('op')
            if op in ('screen', 'write_row', 'write_text', 'fill', 'cursor', 'text_and_cursor'):
                await self.drawing(message)
                await self.respond({"ok": True})
            elif op == 'key':
                timeout = message.get('timeout')
                if timeout is not None and (not isinstance(timeout, (int, float)) or not 0 < timeout <= self.limits.maximum):
                    raise SessionError("Invalid key timeout")
                try:
                    key = await asyncio.wait_for(self.keys.get(), timeout)
                except asyncio.TimeoutError:
                    key = None
                await self.respond({"key": key})
            elif op == 'close':
                raise SessionError("Application completed")
            else:
                raise SessionError("Invalid application operation")

    async def acquire_display(self):
        await self.request(control(True))
        # Ownership does not clear the phone's existing local prompts. Start
        # from a known canvas so even a first partial write can be restored.
        await self.request(fill())
        await self.request(cursor(0, 0, False))

    async def run(self):
        try:
            await asyncio.wait_for(self.handshake(), self.limits.handshake_total)
            self.ready = True
            self.tasks = [asyncio.create_task(self.receive()), asyncio.create_task(self.supervise())]
            # Acquire only after the final authentication ACK.
            setup = asyncio.create_task(self.acquire_display())
            self.tasks.append(setup)
            done, _ = await asyncio.wait([*self.tasks, setup], return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
            if setup not in done:
                raise SessionError("Connection ended during display acquisition")
            self.tasks.remove(setup)
            self.last_heartbeat = time.monotonic()
            self.process = await asyncio.create_subprocess_exec(
                sys.executable, '-u', str(Path(__file__).with_name('runtime.py')), str(self.app),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                limit=8192)
            await self.respond({"metadata": self.metadata})
            ready = await asyncio.wait_for(self.process.stdout.readline(), self.limits.app_start)
            if json.loads(ready) != {"op": "ready"}:
                raise SessionError("Application did not become ready")
            self.tasks += [asyncio.create_task(self.app_reader()), asyncio.create_task(self.app_requests()),
                           asyncio.create_task(self.redraw_loop())]
            done, _ = await asyncio.wait(self.tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        except (SessionError, asyncio.TimeoutError, asyncio.IncompleteReadError,
                ConnectionError, ValueError, KeyError, asyncio.QueueFull) as error:
            LOG.info("Session ended: %s", error)
        finally:
            for task in self.tasks:
                task.cancel()
            await asyncio.gather(*self.tasks, return_exceptions=True)
            if self.process:
                if self.process.returncode is None:
                    with contextlib.suppress(ProcessLookupError):
                        self.process.terminate()
                try:
                    await asyncio.wait_for(self.process.wait(), 2)
                except asyncio.TimeoutError:
                    self.process.kill()
                    await self.process.wait()
            # Stop has no guaranteed final ACK; never retransmit it blindly.
            if self.ready:
                with contextlib.suppress(ConnectionError, asyncio.TimeoutError):
                    await asyncio.wait_for(self.send(encode(0x89, [2])), self.limits.shutdown)
            self.writer.close()
            with contextlib.suppress(ConnectionError, asyncio.TimeoutError):
                await asyncio.wait_for(self.writer.wait_closed(), 2)


async def serve(args):
    limits = Limits()
    for name in vars(limits):
        value = getattr(args, name, None)
        if value is not None:
            setattr(limits, name, value)
    active = False

    async def connected(reader, writer):
        nonlocal active
        if active:
            writer.close()
            return
        active = True
        try:
            await Session(reader, writer, args.app, limits).run()
        finally:
            active = False

    server = await asyncio.start_server(connected, '127.0.0.1', args.port, limit=4096)
    LOG.info("Listening on 127.0.0.1:%s; app %s", args.port, args.app)
    async with server:
        await server.serve_forever()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', type=Path, required=True)
    parser.add_argument('--port', type=int, default=27274)
    for name, default in vars(Limits()).items():
        parser.add_argument('--' + name.replace('_', '-'), type=type(default), default=default)
    args = parser.parse_args()
    if not args.app.is_file() or not 1 <= args.port <= 65535:
        parser.error('Provide an existing application file and a valid TCP port')
    if any(getattr(args, name) <= 0 for name in vars(Limits())):
        parser.error('Limits must be positive')
    if args.ping >= args.silence or args.maximum > 3500:
        parser.error('Ping must be below silence; maximum must not exceed 3500 seconds')
    args.app = args.app.resolve()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    try:
        asyncio.run(serve(args))
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
