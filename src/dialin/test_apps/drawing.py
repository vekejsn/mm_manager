"""Fixture: exercise each public drawing method through the real pipes."""


async def main(phone):
    await phone.screen('UPPER', 'OLD LOWER')
    await phone.write_row(1, 'abcdefghij')
    await phone.write_text(3, 1, 'ZZ')
    await phone.cursor(4, 1, True, erase=True)
    await phone.text_and_cursor(0, 0, 'NAME: ', 2, 1, True, erase=True)
    await phone.read_key()
    await phone.fill('.')
    await phone.clear()
