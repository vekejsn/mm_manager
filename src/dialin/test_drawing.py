"""Wire layouts, retained cells, cursor semantics, and helper update scope."""
import unittest
from unittest.mock import AsyncMock

from protocol import crc16_arc, cursor, decode, fill, text, text_and_cursor
from runtime import Phone
from server import Session, SessionError


class DrawingProtocolTests(unittest.TestCase):
    def test_documented_frame_vectors(self):
        self.assertEqual(crc16_arc(b'123456789'), 0xbb3d)
        self.assertEqual(fill(), bytes.fromhex('02 02 86 20 c3 c0 03'))
        self.assertEqual(text_and_cursor(0, 1, 'NAME: ', 6, 1, True),
                         bytes.fromhex('02 0c 93 06 01 02 00 01 4e 41 4d 45 3a 20 b5 46 03'))
        self.assertEqual(decode(text(7, 1, '123')), (0x85, b'\x07\x01123'))
        self.assertEqual(decode(cursor(19, 1, True, True)), (0x87, b'\x13\x01\x03'))

    def test_coordinates_ascii_and_row_bounds_are_enforced(self):
        for col, row, value in ((20, 0, ''), (-1, 0, 'x'), (0, 2, 'x'),
                                (True, 0, 'x'), (19, 1, 'xx'), (0, 0, '\n'), (0, 0, 'é')):
            with self.subTest(col=col, row=row, value=value), self.assertRaises(ValueError):
                text(col, row, value)
        with self.assertRaises(ValueError):
            text_and_cursor(0, 0, 'ok', 20, 1)
        for value in (0, 0x1f, 0x7f, 256, ' ', True):
            with self.subTest(fill=value), self.assertRaises(ValueError):
                fill(value)


class RetainedDrawingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.session = Session(None, None, None)
        self.session.request = AsyncMock()

    def commands(self):
        return [decode(call.args[0]) for call in self.session.request.await_args_list]

    async def test_partial_write_preserves_both_rows_and_cursor(self):
        await self.session.drawing({'op': 'screen', 'line1': 'abcdefghij', 'line2': 'DO NOT CHANGE'})
        await self.session.drawing({'op': 'cursor', 'col': 12, 'row': 1, 'visible': True})
        self.session.request.reset_mock()
        await self.session.drawing({'op': 'write_text', 'col': 3, 'row': 0, 'text': 'ZZ'})
        self.assertEqual(self.commands(), [(0x85, b'\x03\x00ZZ'), (0x87, bytes([12, 1, 2]))])
        self.assertEqual(self.session.screen, ('abcZZfghij'.ljust(20), 'DO NOT CHANGE'.ljust(20)))
        self.assertEqual(self.session.cursor_state, (12, 1, True))

    async def test_row_replacement_pads_only_selected_row(self):
        self.session.screen = ('UPPER'.ljust(20), 'OLD TEXT'.ljust(20))
        await self.session.drawing({'op': 'write_row', 'row': 1, 'text': 'NEW'})
        self.assertEqual(self.commands(), [(0x85, b'\x00\x01' + b'NEW'.ljust(20)),
                                           (0x87, bytes([0, 0, 0]))])
        self.assertEqual(self.session.screen, ('UPPER'.ljust(20), 'NEW'.ljust(20)))

    async def test_combined_erases_after_text_and_redraw_retains_result(self):
        await self.session.drawing({'op': 'text_and_cursor', 'col': 0, 'row': 1, 'text': 'ABC',
                                    'cursor_col': 1, 'cursor_row': 1, 'visible': True, 'erase': True})
        self.assertEqual(self.commands(), [(0x93, bytes([1, 1, 3, 0, 1]) + b'ABC')])
        self.assertEqual(self.session.screen[1], 'A C'.ljust(20))
        self.session.request.reset_mock()
        await self.session.draw_desired()
        self.assertEqual(self.commands(), [(0x85, bytes([0, 0]) + b' ' * 20),
                                           (0x85, bytes([0, 1]) + b'A C'.ljust(20)),
                                           (0x87, bytes([1, 1, 2]))])

    async def test_cursor_erase_does_not_shift_text(self):
        self.session.screen = ('ABCDE'.ljust(20), 'LOWER'.ljust(20))
        await self.session.drawing({'op': 'cursor', 'col': 2, 'row': 0, 'visible': False, 'erase': True})
        self.assertEqual(self.commands(), [(0x87, bytes([2, 0, 1]))])
        self.assertEqual(self.session.screen, ('AB DE'.ljust(20), 'LOWER'.ljust(20)))

    async def test_fill_repositions_but_preserves_cursor_visibility(self):
        self.session.cursor_state = (17, 1, True)
        await self.session.drawing({'op': 'fill', 'char': '.'})
        self.assertEqual(self.commands(), [(0x86, b'.')])
        self.assertEqual(self.session.screen, ('.' * 20, '.' * 20))
        self.assertEqual(self.session.cursor_state, (0, 0, True))

    async def test_warning_retains_app_changes_without_overwriting_warning(self):
        self.session.warning = True
        await self.session.drawing({'op': 'write_text', 'col': 7, 'row': 1, 'text': '123'})
        self.assertEqual(self.commands()[1], (0x85, bytes([0, 1]) + b'Press any key'.ljust(20)))
        self.assertEqual(self.commands()[-1], (0x87, bytes([0, 0, 0])))
        self.session.warning = False
        self.session.request.reset_mock()
        await self.session.draw_desired()
        self.assertEqual(self.commands()[1], (0x85, bytes([0, 1]) + b'       123'.ljust(20)))

    async def test_invalid_operations_leave_retained_state_and_wire_untouched(self):
        before = self.session.screen, self.session.cursor_state
        for message in ({'op': 'write_row', 'row': 2, 'text': 'BAD'},
                        {'op': 'write_text', 'col': 19, 'row': 0, 'text': 'BAD'},
                        {'op': 'fill', 'char': ''}, {'op': 'fill', 'char': '\x13'},
                        {'op': 'text_and_cursor', 'col': 0, 'row': 0, 'text': 'ABC',
                         'cursor_col': 20, 'cursor_row': 1}):
            with self.subTest(message=message), self.assertRaises((ValueError, SessionError)):
                await self.session.drawing(message)
            self.assertEqual((self.session.screen, self.session.cursor_state), before)
        self.session.request.assert_not_awaited()


class ApplicationDrawingTests(unittest.IsolatedAsyncioTestCase):
    async def test_menu_pages_change_only_lower_row(self):
        phone = Phone(None, None, {})
        phone.call = AsyncMock(side_effect=[{}, {'key': 12}, {}, {'key': 2}])
        self.assertEqual(await phone.menu('TITLE', {'1': 'One', '2': 'Two'}), '2')
        operations = [call.kwargs for call in phone.call.await_args_list]
        self.assertEqual(operations[0], {'op': 'screen', 'line1': 'TITLE', 'line2': '1 One #>'})
        self.assertEqual(operations[2], {'op': 'write_row', 'row': 1, 'text': '2 Two #>'})

    async def test_number_entry_uses_combined_updates_and_writes_prompt_once(self):
        phone = Phone(None, None, {})
        phone.call = AsyncMock(side_effect=[{}, {}, {'key': 1}, {}, {'key': 11}, {}, {'key': 12}, {}])
        self.assertEqual(await phone.input_number('NUMBER'), '')
        operations = [call.kwargs for call in phone.call.await_args_list]
        self.assertEqual(operations[0], {'op': 'write_row', 'row': 0, 'text': 'NUMBER'})
        updates = [op for op in operations if op['op'] == 'text_and_cursor']
        self.assertEqual([op['text'] for op in updates], [' ' * 20, '1'.ljust(20), ' ' * 20])
        self.assertEqual([op['cursor_col'] for op in updates], [0, 1, 0])
        self.assertTrue(all(op['cursor_row'] == 1 and op['visible'] for op in updates))


if __name__ == '__main__':
    unittest.main()
