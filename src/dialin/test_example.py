"""Predictive T9 example editing and ambiguity checks."""
import unittest
from unittest.mock import AsyncMock

from example import input_t9, t9_candidates


class T9ExampleTests(unittest.IsolatedAsyncioTestCase):
    async def enter(self, keys, **options):
        self.phone = AsyncMock()
        self.phone.read_key.side_effect = keys
        return await input_t9(self.phone, **options)

    async def test_predictive_phrase(self):
        self.assertEqual(await self.enter(list('43556096753#')), 'hello world')
        self.phone.write_row.assert_awaited_once_with(0, '1> 0=space *=del #OK')
        self.phone.cursor.assert_awaited_once_with(0, 0, False)
        updates = self.phone.text_and_cursor.await_args_list
        self.assertEqual(updates[-1].args, (0, 1, 'hello world'.ljust(20), 11, 1, True))

    async def test_candidate_cycling(self):
        self.assertEqual(t9_candidates('4663'), ['good', 'home', 'gone', 'hood'])
        self.assertEqual(await self.enter(list('46631#')), 'home')

    async def test_delete_pending_and_committed_text(self):
        self.assertEqual(await self.enter(list('43556*60*#')), 'hello')

    async def test_unknown_word_falls_back_to_digits(self):
        self.assertEqual(await self.enter(list('222222#')), '222222')

    async def test_cancel_and_timeout_hide_cursor(self):
        for keys in (['*'], [None]):
            with self.subTest(keys=keys):
                self.assertIsNone(await self.enter(keys))
                self.phone.cursor.assert_awaited_once_with(0, 0, False)

    async def test_length_limit_preserves_row_boundary(self):
        self.assertEqual(await self.enter(list('4355609#'), max_length=5), 'hello')
        self.assertTrue(all(len(call.args[2]) == 20 for call in self.phone.text_and_cursor.await_args_list))


if __name__ == '__main__':
    unittest.main()
