"""Protocol/timeout integration tests; no modem or third-party packages needed."""
import asyncio
import unittest
from pathlib import Path

from protocol import auth_result, decode, encode
from provision import pack_service_number, patch_service
from server import Limits, Session, SessionError, destination_digits, identification

HERE = Path(__file__).resolve().parent
# Identification from the ROM-verified fake-email-gateway transcript.
IDENTITY = bytes.fromhex(
    '05 12 34 56 78 90 07 4e 4f 41 31 58 30 34 04 56 31 2e 33 '
    '01 02 07 20 26 10 24 15 42 17 01 00 00 01 23 01 00'
)


class CodecTests(unittest.TestCase):
    def test_recovered_authentication_vector(self):
        metadata, terminal, timestamp = identification(IDENTITY)
        self.assertEqual(metadata['terminal_id'], '1234567890')
        self.assertEqual(timestamp[-3:], bytes([15, 42, 17]))
        self.assertEqual(timestamp[0], 126)
        result = auth_result(terminal, timestamp, destination_digits(bytes.fromhex('55 51 23 4e')),
                             bytes.fromhex('01 23 45 67'))
        self.assertEqual(result, 0x29c0)

    def test_decimal_destination_and_extended_rejection(self):
        self.assertEqual(destination_digits(bytes.fromhex('10 02 ee')), bytes([1, 10, 10, 2]))
        with self.assertRaises(SessionError):
            destination_digits(b'\xbf')

    def test_provision_preserves_other_records_and_fields(self):
        original = bytes(range(256)) * 2 + bytes(range(58))
        updated = patch_service(original, 9, '18005551234', '123', 2, 'ONLINE')
        self.assertEqual(updated[:9 * 57], original[:9 * 57])
        self.assertEqual(updated[9 * 57:9 * 57 + 3], original[9 * 57:9 * 57 + 3])
        self.assertEqual(updated[9 * 57 + 55], original[9 * 57 + 55] | 0x10)
        self.assertEqual(pack_service_number('100'), bytes.fromhex('1a a0 00 00 00 00 00 00'))
        self.assertEqual(len(updated), 570)


class SessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.session = None
        self.running = None
        self.limits = Limits(ack=.05, line_bps=100000, ping=2, silence=4, idle=3,
                             idle_grace=1, maximum=120, app_heartbeat=2)

        async def accept(reader, writer):
            self.session = Session(reader, writer, HERE / 'example.py', self.limits)
            self.running = asyncio.create_task(self.session.run())

        self.server = await asyncio.start_server(accept, '127.0.0.1', 0)
        port = self.server.sockets[0].getsockname()[1]
        self.reader, self.writer = await asyncio.open_connection('127.0.0.1', port)
        while self.session is None:
            await asyncio.sleep(.001)

    async def asyncTearDown(self):
        self.writer.close()
        await self.writer.wait_closed()
        if self.running:
            await asyncio.wait_for(self.running, 5)
        self.server.close()
        await self.server.wait_closed()

    async def send(self, command, data=b'', fragmented=False):
        packet = encode(command, data)
        if fragmented:
            for byte in packet:
                self.writer.write(bytes([byte]))
                await self.writer.drain()
                await asyncio.sleep(0)
        else:
            self.writer.write(packet)
            await self.writer.drain()

    async def receive(self, timeout=3):
        async def read():
            head = await self.reader.readexactly(2)
            return decode(head + await self.reader.readexactly(head[1] + 3))
        return await asyncio.wait_for(read(), timeout)

    async def handshake(self, duplicate=False):
        await self.send(0x82, IDENTITY, fragmented=True)
        self.assertEqual(await self.receive(), (1, b'\x80'))
        if duplicate:
            await self.send(0x82, IDENTITY)
            self.assertEqual(await self.receive(), (1, b'\x80'))
        for command, data in ((0x95, b'\x22'), (0x96, bytes(range(8))), (0x88, bytes.fromhex('55 51 23 4e'))):
            await self.send(command, data)
            self.assertEqual(await self.receive(), (1, b'\x80'))
        command, challenge = await self.receive()
        self.assertEqual(command, 0x17)
        result = auth_result(bytes.fromhex('12 34 56 78 90'), bytes([26, 10, 24, 15, 42, 17]),
                             bytes([5, 5, 5, 1, 2, 3, 4]), challenge)
        await self.send(0x83, result.to_bytes(2, 'little') + bytes(6))
        self.assertEqual(await self.receive(), (1, b'\x80'))

    async def sequenced_handshake(self, firmware='06CNB02', mode=25):
        identity = bytearray(IDENTITY)
        identity[7:14] = firmware.encode('ascii')
        await self.send(0x82, identity, fragmented=True)
        self.assertEqual(await self.receive(), (1, b'\x80'))
        for command, data in ((0xD5, bytes([mode])), (0x96, bytes(8)), (0xC8, bytes.fromhex('55 51 23 4e'))):
            await self.send(command, data)
            self.assertEqual(await self.receive(), (1, b'\x80'))
        command, challenge = await self.receive()
        self.assertEqual(command, 0x17)
        result = auth_result(bytes.fromhex('12 34 56 78 90'), bytes([126,10,24,15,42,17]),
                             bytes([5,5,5,1,2,3,4]), challenge)
        await self.send(0x83, result.to_bytes(2, 'little') + bytes(6))
        self.assertEqual(await self.receive(), (1, b'\x80'))

    async def test_ported_target_sequence_retry_does_not_repeat_input(self):
        await self.sequenced_handshake()
        await self.screen_until('MILLENNIUM ONLINE')
        await self.send(0xD0, b'\x02')
        await self.send(0xD0, b'\x02')  # Same command/bit/data: lost-ACK retry.
        await self.screen_until('Mailbox:')
        await self.send(0x90, bytes([1,2,3,12]))
        await self.screen_until('Mailbox 123')
        self.assertEqual(self.session.metadata['firmware'], '06CNB02')
        self.assertEqual(self.session.metadata['selector'], 25)
        self.assertTrue(self.session.metadata['sequence_bit'])

    async def test_u5_alternating_identical_keys_are_distinct(self):
        await self.sequenced_handshake('NNK1F05', 0x22)
        await self.screen_until('MILLENNIUM ONLINE')
        await self.send(0xD0, b'\x02')
        await self.screen_until('Mailbox:')
        await self.send(0x90, b'\x01')
        await self.send(0xD0, b'\x01')
        await self.send(0x90, b'\x0c')
        await self.screen_until('Mailbox 11')

    async def screen_until(self, wanted):
        for _ in range(40):
            command, data = await self.receive()
            if command & 0x80:
                await self.send(1, b'\x80')
            if command == 0x85 and wanted.encode() in data[2:]:
                return
        self.fail(f'Screen never contained {wanted}')

    async def test_example_app_menu_numeric_entry_and_sequential_calls(self):
        # Uses the actual subprocess runner and application, not mocked callbacks.
        await self.handshake(duplicate=True)
        await self.screen_until('MILLENNIUM ONLINE')
        await self.send(0x90, bytes([2]))
        await self.screen_until('Mailbox:')
        await self.send(0x90, bytes([1, 2, 3, 12]))
        await self.screen_until('Mailbox 123')
        await self.send(0x90, bytes([1]))
        await self.screen_until('MILLENNIUM ONLINE')
        await self.send(0x90, bytes([10]))
        await self.screen_until('Goodbye!')
        while True:
            command, _ = await self.receive()
            if command == 0x89:
                break
            if command & 0x80:
                await self.send(1, b'\x80')
        await asyncio.wait_for(self.running, 3)
        self.assertIsNotNone(self.session.process.returncode)
        # Reuse the listener after a completed call.
        self.writer.close()
        await self.writer.wait_closed()
        self.session = None
        self.reader, self.writer = await asyncio.open_connection('127.0.0.1', self.server.sockets[0].getsockname()[1])
        while self.session is None:
            await asyncio.sleep(.001)
        await self.handshake()
        await self.screen_until('MILLENNIUM ONLINE')

    async def test_lost_ack_retries_then_closes(self):
        await self.handshake()
        packets = [await self.receive() for _ in range(3)]
        self.assertEqual(packets, [(0x84, b'\x01')] * 3)
        self.assertEqual(await self.receive(), (0x89, b'\x02'))
        await asyncio.wait_for(self.running, 2)

    async def test_payment_takeover_suspends_and_redraws(self):
        await self.handshake()
        await self.screen_until('MILLENNIUM ONLINE')
        # Drain the remaining initial screen and cursor.
        while True:
            command, _ = await self.receive()
            await self.send(1, b'\x80')
            if command == 0x87:
                break
        await self.send(0x84, b'\x02')
        self.assertEqual(await self.receive(), (1, b'\x80'))
        self.limits.idle = .1
        self.limits.idle_grace = .2
        await self.send(0x90, b'\x01')
        self.assertEqual(await self.receive(), (1, b'\x80'))
        with self.assertRaises(asyncio.TimeoutError):
            await self.receive(.4)  # Longer than idle+grace; local takeover pauses it.
        await self.send(0x84, b'\x03')
        self.assertEqual(await self.receive(), (1, b'\x80'))
        await self.screen_until('Powered by Python')

    async def test_idle_warning_and_disconnect_despite_pings(self):
        self.limits.idle = .2
        self.limits.idle_grace = .3
        self.limits.ping = .1
        await self.handshake()
        await self.screen_until('Still there?')
        stopped = False
        while not stopped:
            await self.send(0x0e)
            command, _ = await self.receive()
            if command & 0x80 and command != 0x89:
                await self.send(1, b'\x80')
            stopped = command == 0x89
        await asyncio.wait_for(self.running, 2)

    async def test_bad_crc_reports_status_then_accepts_retransmission(self):
        corrupt = bytearray(encode(0x82, IDENTITY))
        corrupt[-2] ^= 1
        self.writer.write(corrupt)
        await self.writer.drain()
        self.assertEqual(await self.receive(), (1, b'\x40'))
        await self.handshake()
        await self.screen_until('MILLENNIUM ONLINE')

    async def test_handshake_deadline(self):
        self.limits.handshake_stage = .05
        await self.send(0x82, IDENTITY)
        self.assertEqual(await self.receive(), (1, b'\x80'))
        await asyncio.wait_for(self.running, 1)
        self.assertTrue(self.session.writer.is_closing())

    async def test_receive_silence_timeout(self):
        self.limits.ping = .05
        await self.handshake()
        await self.screen_until('MILLENNIUM ONLINE')
        await self.screen_until('1 About')
        while True:
            command, _ = await self.receive()
            if command & 0x80:
                await self.send(1, b'\x80')
            if command == 0x87:
                break
        self.limits.silence = .2
        # No further inbound traffic; outbound pings must not extend receive life.
        await asyncio.wait_for(self.running, 2)

    async def until_stopped(self):
        for _ in range(100):
            command, _ = await self.receive()
            if command == 0x89:
                await asyncio.wait_for(self.running, 3)
                return
            if command & 0x80:
                await self.send(1, b'\x80')
        self.fail('Session did not stop')

    async def test_stalled_application_is_terminated(self):
        self.session.app = HERE / 'test_apps' / 'stalled.py'
        self.limits.app_heartbeat = .3
        self.limits.ping = .1
        await self.handshake()
        await self.until_stopped()
        self.assertIsNotNone(self.session.process.returncode)

    async def test_application_startup_timeout(self):
        self.session.app = HERE / 'test_apps' / 'slow_start.py'
        self.limits.app_start = .2
        await self.handshake()
        await self.until_stopped()
        self.assertIsNotNone(self.session.process.returncode)

    async def test_crashed_application_releases_connection(self):
        self.session.app = HERE / 'test_apps' / 'crashed.py'
        await self.handshake()
        await self.until_stopped()
        self.assertIsNotNone(self.session.process.returncode)

    async def test_maximum_call_deadline(self):
        self.limits.maximum = .5
        await self.handshake()
        await self.until_stopped()

    async def test_frame_gap_recovers_without_losing_following_frame(self):
        self.limits.frame_gap = .03
        self.writer.write(b'\x02\x25\x82')
        await self.writer.drain()
        await asyncio.sleep(.08)
        await self.handshake()
        await self.screen_until('MILLENNIUM ONLINE')

    async def test_invalid_authentication_does_not_acquire_display(self):
        for command, data in ((0x82, IDENTITY), (0x95, b'\x22'), (0x96, bytes(8)), (0x88, b'\xee')):
            await self.send(command, data)
            self.assertEqual(await self.receive(), (1, b'\x80'))
        command, challenge = await self.receive()
        self.assertEqual(command, 0x17)
        _, terminal, timestamp = identification(IDENTITY)
        wrong = auth_result(terminal, timestamp, b'', challenge) ^ 1
        await self.send(0x83, wrong.to_bytes(2, 'little') + bytes(6))
        await asyncio.wait_for(self.running, 1)
        self.assertFalse(self.session.ready)

    async def test_drawing_api_and_partial_state_survive_payment_takeover(self):
        self.session.app = HERE / 'test_apps' / 'drawing.py'
        await self.handshake()
        commands = []
        while True:
            command, data = await self.receive()
            if command & 0x80:
                await self.send(1, b'\x80')
            commands.append((command, data))
            if command == 0x93:
                break
        self.assertEqual(commands[:3], [(0x84, b'\x01'), (0x86, b' '), (0x87, bytes([0, 0, 0]))])
        self.assertIn((0x85, b'\x03\x01ZZ'), commands)
        self.assertIn((0x87, bytes([4, 1, 3])), commands)
        self.assertEqual(commands[-1], (0x93, bytes([2, 1, 3, 0, 0]) + b'NAME: '))
        await self.send(0x84, b'\x02')
        self.assertEqual(await self.receive(), (1, b'\x80'))
        with self.assertRaises(asyncio.TimeoutError):
            await self.receive(.1)
        await self.send(0x84, b'\x03')
        self.assertEqual(await self.receive(), (1, b'\x80'))
        for expected in ((0x85, bytes([0, 0]) + b'NAME: '.ljust(20)),
                         (0x85, bytes([0, 1]) + b'ab Z fghij'.ljust(20)),
                         (0x87, bytes([2, 1, 2]))):
            self.assertEqual(await self.receive(), expected)
            await self.send(1, b'\x80')
        await self.send(0x90, bytes([12]))
        self.assertEqual(await self.receive(), (1, b'\x80'))
        for expected in ((0x86, b'.'), (0x86, b' ')):
            self.assertEqual(await self.receive(), expected)
            await self.send(1, b'\x80')
        self.assertEqual(await self.receive(), (0x89, b'\x02'))
        await asyncio.wait_for(self.running, 3)


if __name__ == '__main__':
    unittest.main()
