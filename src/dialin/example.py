"""Example numeric-keypad application for the NOA 2x20 display."""
import asyncio


# Deliberately small demonstration dictionary; add words for your application.
T9_WORDS = (
    'hello', 'world', 'good', 'home', 'gone', 'hood', 'phone', 'python',
    'test', 'text', 'yes', 'no', 'thanks', 'you', 'call', 'me', 'a', 'i',
    'love', 'the', 'this', 'is', 'manager', 'millennium',
)
T9_DIGITS = {letter: digit for digit, letters in zip('23456789', (
    'abc', 'def', 'ghi', 'jkl', 'mno', 'pqrs', 'tuv', 'wxyz',
)) for letter in letters}


def t9_candidates(digits):
    """Return distinct matching word prefixes, or raw digits for an unknown word."""
    matches = []
    for word in T9_WORDS:
        code = ''.join(T9_DIGITS[letter] for letter in word)
        prefix = word[:len(digits)]
        if code.startswith(digits) and prefix not in matches:
            matches.append(prefix)
    return matches or [digits]


async def input_t9(phone, max_length=20, timeout=60):
    """Predictive 2..9 input; 1 cycles, 0 adds a space, * deletes/cancels, # submits."""
    if not 1 <= max_length <= 20:
        raise ValueError('max_length must be 1..20')
    committed, digits, candidate = '', '', 0
    deadline = asyncio.get_running_loop().time() + timeout
    try:
        await phone.write_row(0, '1> 0=space *=del #OK')
        while True:
            matches = t9_candidates(digits)
            value = committed + matches[candidate]
            await phone.text_and_cursor(0, 1, value.ljust(20), min(len(value), 19), 1, True)
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return None
            key = await phone.read_key(remaining)
            if key is None:
                return None
            if key == '#':
                return value.rstrip()
            if key == '*':
                if digits:
                    digits = digits[:-1]
                elif committed:
                    committed = committed[:-1]
                else:
                    return None
                candidate = 0
            elif key == '1' and digits:
                candidate = (candidate + 1) % len(matches)
            elif key == '0' and len(value) < max_length:
                committed, digits, candidate = value + ' ', '', 0
            elif isinstance(key, str) and len(key) == 1 and key in '23456789' and len(value) < max_length:
                digits += key
                candidate = 0
    finally:
        await phone.cursor(0, 0, False)


async def main(phone):
    while True:
        choice = await phone.menu('MILLENNIUM ONLINE', {
            '1': 'About', '2': 'Mailbox demo', '3': 'T9 text demo', '0': 'Goodbye',
        })
        if choice is None or choice == '0':
            await phone.screen('Goodbye!', 'Thanks for calling')
            return
        if choice == '1':
            await phone.message('Powered by Python', phone.metadata['terminal_id'])
        elif choice == '2':
            number = await phone.input_number('Mailbox: #=submit', max_length=10)
            if number is not None:
                await phone.message('Mailbox ' + number, 'No messages (demo)')
        elif choice == '3':
            value = await input_t9(phone)
            if value is not None:
                await phone.message('You typed:', value or '(empty)')
