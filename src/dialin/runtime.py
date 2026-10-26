"""Child-process application runner; stdout is reserved for the JSON bridge."""
import asyncio
import contextlib
import importlib.util
import json
import sys
from pathlib import Path


class Phone:
    def __init__(self, reader, output, metadata):
        self.reader, self.output, self.metadata = reader, output, metadata
        self.lock = asyncio.Lock()

    def emit(self, message):
        self.output.write(json.dumps(message) + '\n')
        self.output.flush()

    async def call(self, **message):
        async with self.lock:
            self.emit(message)
            return json.loads(await asyncio.to_thread(self.reader.readline))

    async def screen(self, line1='', line2=''):
        await self.call(op='screen', line1=line1, line2=line2)

    async def write_row(self, row, text):
        """Replace one row, padding trailing cells with spaces."""
        await self.call(op='write_row', row=row, text=text)

    async def write_text(self, col, row, text):
        """Overwrite only the supplied cells, preserving the configured cursor."""
        await self.call(op='write_text', col=col, row=row, text=text)

    async def fill(self, char=' '):
        """Fill both rows with one printable ASCII character; position at (0, 0)."""
        await self.call(op='fill', char=char)

    async def clear(self):
        await self.fill(' ')

    async def cursor(self, col, row, visible=False, erase=False):
        await self.call(op='cursor', col=col, row=row, visible=visible, erase=erase)

    async def text_and_cursor(self, col, row, text, cursor_col, cursor_row, visible=False, erase=False):
        """Write text first, then position/show/erase at the independent cursor."""
        await self.call(op='text_and_cursor', col=col, row=row, text=text,
                        cursor_col=cursor_col, cursor_row=cursor_row, visible=visible, erase=erase)

    async def read_key(self, timeout=None):
        response = await self.call(op='key', timeout=timeout)
        key = response['key']
        if key is None:
            return None
        if 1 <= key <= 9:
            return str(key)
        return {0: 'D', 10: '0', 11: '*', 12: '#', 13: 'A', 14: 'B', 15: 'C',
                16: 'LANGUAGE', 27: 'VOLUME_DOWN', 28: 'VOLUME_UP', 29: 'NEXT_CALL'}.get(key, key)

    async def message(self, line1, line2='', timeout=10):
        await self.screen(line1, line2)
        return await self.read_key(timeout)

    async def menu(self, title, options, timeout=60):
        if not options or any(k not in '1234567890' or len(k) != 1 for k in options):
            raise ValueError('Menu choices must have single numeric keys')
        choices = list(options.items())
        index = 0
        deadline = asyncio.get_running_loop().time() + timeout
        key, label = choices[index]
        await self.screen(title, f'{key} {label}'[:17] + ' #>')
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return None
            pressed = await self.read_key(remaining)
            if pressed is None:
                return None
            if pressed in options:
                return pressed
            if pressed == '#':
                index = (index + 1) % len(choices)
            elif pressed == '*':
                index = (index - 1) % len(choices)
            else:
                continue
            key, label = choices[index]
            await self.write_row(1, f'{key} {label}'[:17] + ' #>')

    async def input_number(self, prompt, max_length=16, timeout=60):
        """# submits; * deletes a digit, or cancels when empty."""
        if not 1 <= max_length <= 20:
            raise ValueError('max_length must be 1..20')
        value = ''
        deadline = asyncio.get_running_loop().time() + timeout
        try:
            await self.write_row(0, prompt)
            while True:
                await self.text_and_cursor(0, 1, value.ljust(20), min(len(value), 19), 1, True)
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    return None
                key = await self.read_key(remaining)
                if key is None:
                    return None
                if key == '#':
                    return value
                if key == '*':
                    if not value:
                        return None
                    value = value[:-1]
                elif isinstance(key, str) and len(key) == 1 and key.isdigit() and len(value) < max_length:
                    value += key
        finally:
            await self.cursor(0, 0, False)

    async def close(self):
        self.emit({'op': 'close'})


async def run(path):
    output = sys.stdout
    sys.stdout = sys.stderr  # User print() is diagnostic output, never bridge data.
    initial = json.loads(await asyncio.to_thread(sys.stdin.readline))
    phone = Phone(sys.stdin, output, initial['metadata'])

    async def heartbeat():
        while True:
            phone.emit({'op': 'heartbeat'})
            await asyncio.sleep(1)

    task = asyncio.create_task(heartbeat())
    try:
        path = Path(path).resolve()
        sys.path.insert(0, str(path.parent))
        spec = importlib.util.spec_from_file_location('dialin_application', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        phone.emit({'op': 'ready'})
        await module.main(phone)
        await phone.close()
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


if __name__ == '__main__':
    asyncio.run(run(sys.argv[1]))
