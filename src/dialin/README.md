# Python dial-in applications (Millennium TTY)

One `mm_manager` accepts management and interactive display-service sessions
on the same serial modem or raw TCP listener. One session occupies the transport
at a time. Supported TTY profiles are NOA1X04, NNK1F05/U5 and the ported 06CNB02
client. Unmodified 06CNB02 is management-only.

## Start it

Requirements: Python **3.10 or later**, the rebuilt `mm_manager`, and a phone
configured to enter the NOA TTY/modem service. The Python service uses only
the standard library.

From the repository root, start the application service:

```powershell
python src/dialin/server.py --app src/dialin/example.py --port 27274
```

Then start the manager with its normal modem settings plus `-j 27274`:

```powershell
.\mm_manager.exe -m -f \\.\COM3 -n 18005551234 -j 27274 -vv -l dialin.dlog
```

Linux example:

```sh
./mm_manager -m -f /dev/ttyACM0 -n 18005551234 -j 27274 -vv -l dialin.dlog
```

For the local emulator's TCP bridge, keep the controller on loopback 27274 and
use a separate external manager listener:

```sh
python3 src/dialin/server.py --app src/dialin/example.py --port 27274
/path/to/mm_manager -n 1234 -T 0.0.0.0:16420 -j 27274 -vv -l dialin.dlog
```

Configure the emulator's NCC target as `127.0.0.1:16420`. The emulator HTTP API
on port 8787 controls/debugs the emulator; it does not carry the TTY session.
Accelerated emulator tests can use `--ping 1` and bounded/real-time-paced CPU
execution so firmware virtual-time deadlines and controller wall-time deadlines
remain compatible. Physical-line deployments retain the normal defaults.

For a supervised local run with isolated configuration/database/log state:

```sh
python3 tools/run_tty_lab.py --manager /path/to/mm_manager \
  --state-dir /path/to/demo-state --number 1234 --tcp 0.0.0.0:16420
```

The supervisor starts both processes and writes `ready.json`, `controller.log`,
`manager.log` and `session.dlog` in the state directory.

On 2026-10-25, the independent emulator's patched `06CNB02` CPU authenticated
through this TCP manager/controller path and operated the example application's
About screen, mailbox `123`, T9 input `hello`, menu returns and clean exit using
emulated keypad events. A management time request succeeded on the same listener
afterward. The firmware-side reproduction is `firmware_analysis/demo_8787.py tcp`
in the separate firmware research workspace, with evidence in
`firmware_analysis/demo_tcp_result.json`. That test uses synthetic RAM
provisioning and an established-carrier fixture; native dialing and production
phone provisioning are not validated by it.

`-j` enables classification after modem CONNECT or TCP accept. Management
lookahead is replayed byte-for-byte into the existing parser, including CRC
errors so its normal NACK recovery still operates. A validated TTY identification frame routes the connection to the
loopback service. The chosen protocol stays fixed until disconnect. Without
`-j`, the existing management path is used directly. Transcript replay mode
also continues to use the existing management path.

The manager owns the terminal transport and bridges raw bytes over loopback TCP; the
Python service owns NOA framing, authentication, ACK scheduling and supervision.
Each application's `main(phone)` runs in a separate Python child process.
Closing the bridge causes the manager to hang up with DTR and wait for the
next call. Start the service before making an interactive call; a missing
service causes that call to disconnect.

### How the processes communicate

```text
Phone <-- TTY binary frames over modem/TCP --> mm_manager (C)
                                                |
                                    raw TCP, 127.0.0.1:27274
                                                |
                                     server.py (Python)
                                                |
                                 JSON lines over stdin/stdout pipes
                                                |
                                runtime.py + your application script
```

There is no HTTP server, REST API, or web request in this path. The local TCP
connection is a byte stream carrying the original NOA frames, including their
length fields and CRCs. TCP may split or combine writes; `server.py` rebuilds
frames from their declared lengths. Only mm_manager opens the serial port.

For each ready call, `server.py` starts `runtime.py` as a child process and
loads the script named by `--app`. Their pipes carry newline-delimited JSON.
For example, `await phone.screen('HELLO', 'Press 1')` sends:

```json
{"op":"screen","line1":"HELLO","line2":"Press 1"}
```

The service converts that request into NOA drawing frames, waits for phone
ACKs, and replies on the child's stdin with `{"ok":true}`. A `read_key()` call
sends a `key` request; the service returns a decoded event's wire value or a
timeout result, and the runtime converts numeric keys into strings. Heartbeat
messages use the same child-to-service pipe. Application `print()` output is
redirected to stderr so it cannot interfere with the JSON messages.

This split keeps modem ownership in C and protocol supervision in the Python
service, independently of application execution. If an application blocks,
the service can still send keepalives, enforce deadlines, and terminate the
child. Both programs run on the same computer; no separate network host is
needed.

### Source layout

- `src/mm_dialin.c` / `.h`: C session detection and local TCP bridge.
- `src/dialin/server.py`: NOA protocol engine and application supervision.
- `src/dialin/protocol.py`: frame codec and challenge-response calculation.
- `src/dialin/runtime.py`: application runner and `phone` API.
- `src/dialin/example.py`: example menu application.
- `src/dialin/provision.py`: service-table provisioning helper.
- `src/dialin/test_server.py`, `test_drawing.py`, `test_router.c`, `test_apps/`: verification.

The original `Dialin/` directory contains the supplied specification, ROM
research and offline reference tools. Runtime code and tests do not need that
directory to be present.

`-l` records UART traffic for both protocols. Existing management PCAP/UDP
captures and their dissector describe management packets; NOA traffic is
available in the UART transcript and is not encoded as management PCAP packets.

## Write an application

Save a Python file containing an async entry point:

```python
async def main(phone):
    while True:
        choice = await phone.menu('MAIN MENU', {
            '1': 'About',
            '2': 'Mailbox',
            '0': 'Goodbye',
        }, timeout=60)

        if choice is None or choice == '0':
            await phone.screen('Goodbye!', 'Thanks for calling')
            return
        if choice == '1':
            await phone.message('Powered by Python', 'Press any key')
        elif choice == '2':
            mailbox = await phone.input_number('Mailbox: #=submit', max_length=10)
            if mailbox is not None:
                await phone.message('Mailbox ' + mailbox, 'No messages')
```

Point `--app` at that file. A fresh process is loaded on each call, so changes
to the script apply to the next call. `print()` goes to the service's stderr.
Use asynchronous I/O or `asyncio.to_thread()` for blocking work. A blocked
application event loop misses its heartbeat and is terminated.

### API

The `phone` methods are implemented by [runtime.py](runtime.py), with requests
handled by [server.py](server.py) and NOA frames encoded by
[protocol.py](protocol.py). Drawing primitives map to firmware commands;
menus, pagination, text editing and key meanings are Python application logic.

| Method / property | Behavior |
|---|---|
| `await phone.screen(line1='', line2='')` | Write two padded, 20-character rows |
| `await phone.write_row(row, text)` | Replace just one row, padded to 20 characters (`85`) |
| `await phone.write_text(col, row, text)` | Overwrite only the supplied cells (`85`) |
| `await phone.fill(char=' ')` | Fill both rows with a printable ASCII character (`86`) |
| `await phone.clear()` | Fill with spaces (`86 20`) |
| `await phone.cursor(col, row, visible=False, erase=False)` | Position/show/hide cursor; optionally erase one cell (`87`) |
| `await phone.text_and_cursor(col, row, text, cursor_col, cursor_row, visible=False, erase=False)` | Write text, then apply the independent final cursor/erase operation (`93`) |
| `await phone.read_key(timeout=None)` | Return a decoded key, or `None` on input timeout |
| `await phone.menu(title, options, timeout=60)` | Return selected numeric option key, or `None` |
| `await phone.message(line1, line2='', timeout=10)` | Show message and wait for a key or timeout |
| `await phone.input_number(prompt, max_length=16, timeout=60)` | Numeric editing: `#` submits, `*` deletes/cancels |
| `await phone.close()` | End this call; normally just return from `main()` |
| `phone.metadata` | Terminal ID, firmware, service mode, destination and raw call metadata |

### Method-by-method API reference

Your script defines `async def main(phone)`. The runner supplies `phone`; you
do not construct it or open a connection yourself. Use `await` for every method
below. Times are in seconds. Coordinates are zero-based: upper row = `0`, lower
row = `1`, columns = `0`..`19`. Display text must be printable ASCII.

Drawing methods return `None`. They normally finish after the service has
processed the requested drawing and received the relevant phone ACKs. They
can wait while the phone temporarily owns the display. A validation or
connection failure can end the session; the current bridge does not expose a
structured, recoverable per-operation error response to the application.

#### `await phone.screen(line1='', line2='')`

Replace the entire display with two rows.

- **`line1: str`** — upper-row text, at most 20 characters; defaults to empty.
- **`line2: str`** — lower-row text, at most 20 characters; defaults to empty.
- **Returns:** `None`.
- Each row is padded with spaces to 20 cells, clearing previous trailing text.
  Omitting a row clears it. The configured cursor position and visibility are
  restored after writing both rows.

```python
await phone.screen('MAIN MENU', 'Press a number')
await phone.screen('Finished')  # Also clears the lower row.
```

#### `await phone.write_row(row, text)`

Replace just one complete row.

- **`row: int`** — `0` for upper, `1` for lower.
- **`text: str`** — at most 20 characters; padded with spaces to clear the rest
  of that row. Use `''` to clear the selected row.
- **Returns:** `None`.
- The other row and configured cursor position/visibility are preserved.

```python
await phone.write_row(0, 'STATUS')
await phone.write_row(1, 'Connected')
await phone.write_row(1, '')  # Keep STATUS; clear only the lower row.
```

#### `await phone.write_text(col, row, text)`

Overwrite a substring without replacing the rest of its row.

- **`col: int`** — starting column, `0`..`19`.
- **`row: int`** — `0` or `1`.
- **`text: str`** — must fit in the remaining cells: `len(text) <= 20 - col`.
- **Returns:** `None`.
- There is no padding, insertion, shifting, or wrapping. Existing cells
  outside the supplied text remain unchanged. The configured cursor is
  restored after the write.

```python
await phone.write_row(1, 'Count: 000')
await phone.write_text(7, 1, '123')  # Lower row now reads Count: 123.
```

#### `await phone.fill(char=' ')`

Replace all 40 display cells with the same character.

- **`char: str`** — exactly one printable ASCII character; defaults to a space.
- **Returns:** `None`.
- Moves the cursor to `(0, 0)` while preserving whether it is visible.

```python
await phone.fill('.')  # Both rows become ....................
```

#### `await phone.clear()`

Clear both rows. This is shorthand for `await phone.fill(' ')`.

- **Parameters:** none.
- **Returns:** `None`.
- Leaves the cursor at `(0, 0)` and preserves its visibility. To also hide it,
  call `cursor()` explicitly.

```python
await phone.clear()
await phone.cursor(0, 0, visible=False)
```

#### `await phone.cursor(col, row, visible=False, erase=False)`

Set the cursor position and visibility, optionally blanking its new cell.

- **`col: int`**, **`row: int`** — destination coordinates.
- **`visible: bool`** — show the cursor when `True`; hide it when `False`
  (the default). Visibility is explicitly set, not left unchanged.
- **`erase: bool`** — overwrite that one cell with a space when `True`;
  defaults to `False`.
- **Returns:** `None`.
- Erasing does not delete/shift text or clear the rest of the row. The cursor
  stays at the selected cell. This position/visibility becomes the configured
  cursor state used by subsequent row/text writes.

```python
await phone.cursor(6, 1, visible=True)
await phone.cursor(6, 1, visible=True, erase=True)  # Blank just that cell.
```

#### `await phone.text_and_cursor(col, row, text, cursor_col, cursor_row, visible=False, erase=False)`

Write a substring and then apply a final cursor operation in one NOA command.

- **`col: int`**, **`row: int`** — starting position for the text.
- **`text: str`** — exact text, without automatic padding; must fit in its row.
- **`cursor_col: int`**, **`cursor_row: int`** — independent final cursor
  position; it may be on a different row from the text.
- **`visible: bool`**, **`erase: bool`** — same meanings/defaults as `cursor()`.
- **Returns:** `None`.
- The phone writes text first, then positions/erases/shows the cursor. An erase
  overlapping the newly written text will blank that character. Untouched
  cells remain unchanged. Use `.ljust(20)` when replacing a whole row.

```python
await phone.text_and_cursor(
    col=0, row=1, text='Name: '.ljust(20),
    cursor_col=6, cursor_row=1, visible=True,
)
```

#### `await phone.read_key(timeout=None)`

Wait for and consume the next queued keypress. This does not draw or echo text.

- **`timeout: number | None`** — positive wait limit in seconds, no larger
  than the configured maximum call duration. `None` means no per-read limit;
  global session deadlines still apply.
- **Returns:** a key string, an unmapped integer key code, or `None` on timeout.
- Numeric keys return `'0'`..`'9'`; star/hash return `'*'` and `'#'`. Other named
  results include `'A'`..`'D'`, `'LANGUAGE'`, `'VOLUME_DOWN'`, `'VOLUME_UP'`, and
  `'NEXT_CALL'`. Unmapped repertory/keyboard codes remain integers.
- A key already queued is returned without waiting for a new press. Drawing
  calls do not flush the input queue.

```python
key = await phone.read_key(timeout=30)
if key is None:
    await phone.screen('No key received')
elif key == '1':
    await phone.write_row(1, 'You pressed 1')
```

#### `await phone.message(line1, line2='', timeout=10)`

Display a two-row message, then wait for any key or an input timeout.

- **`line1: str`**, **`line2: str`** — same display limits/padding as `screen()`;
  the upper row is required and the lower row defaults to empty.
- **`timeout: number | None`** — forwarded to `read_key()`; defaults to 10 seconds.
- **Returns:** the key received, or `None` on timeout.
- The wait begins after drawing completes. The message is not automatically
  cleared afterward. A previously queued key can dismiss it immediately.

```python
key = await phone.message('Message sent', 'Press any key', timeout=10)
```

#### `await phone.menu(title, options, timeout=60)`

Show a titled, paginated numeric-choice menu.

- **`title: str`** — upper-row title, at most 20 printable ASCII characters.
- **`options: mapping[str, str]`** — nonempty mapping from single numeric keys
  (`'0'`..`'9'`) to labels. Mapping order determines page order.
- **`timeout: number`** — whole-operation timeout, default 60 seconds; drawing
  and paging consume this budget rather than restarting it.
- **Returns:** the selected key string, or `None` on timeout.
- Displays one option at a time on the lower row. Its key/label portion is
  truncated to 17 characters, followed by ` #>`. `#` pages forward and `*`
  backward, wrapping at either end. Any listed numeric key selects its option,
  even when that option is not currently visible. Other keys are ignored.
- This layout and these paging keys are helper conventions, not firmware rules.

```python
choice = await phone.menu('MAIN MENU', {
    '1': 'About',
    '2': 'Messages',
    '0': 'Exit',
}, timeout=60)
if choice == '2':
    await phone.message('No messages')
```

#### `await phone.input_number(prompt, max_length=16, timeout=60)`

Edit a numeric string using the phone keypad.

- **`prompt: str`** — upper-row prompt, at most 20 printable ASCII characters.
- **`max_length: int`** — maximum digits, `1`..`20`; defaults to 16.
- **`timeout: number`** — whole-operation timeout, default 60 seconds; it is
  not restarted for each digit.
- **Returns:** the entered digit string on `#`, including `''` for an empty
  submission; `None` on cancellation or timeout. Leading zeros are preserved.
- Digits append to the lower row; extra digits at the limit are ignored.
  `*` deletes the last digit, or cancels when already empty. Other keys are
  ignored. The input cursor is visible while editing and hidden at `(0, 0)`
  on exit; the final text remains on the display.

```python
number = await phone.input_number('Mailbox: #=submit', max_length=10)
if number is None:
    await phone.message('Entry cancelled')
else:
    await phone.message('Mailbox ' + number)
```

#### `await phone.close()`

Tell the service to finish the current interactive call.

- **Parameters:** none.
- **Returns:** `None` after emitting the close instruction; it does not wait
  for a final phone ACK or confirmed hangup.
- The service sends a best-effort stop request, closes its bridge connection,
  and mm_manager hangs up. Treat this as terminal: return from your application
  rather than calling more phone methods. Returning normally from `main()`
  already calls `close()` automatically, so explicit use is usually unnecessary.

```python
await phone.screen('Goodbye!')
return  # Inside main(phone): the runner closes the call automatically.
```

#### `phone.metadata`

A dictionary available when `main(phone)` starts; read it without `await`.

| Key | Type | Meaning |
|---|---|---|
| `terminal_id` | `str` | Ten-digit terminal ID, preserving leading zeros |
| `firmware` | `str` | `NOA1X04`, `NNK1F05`, or ported `06CNB02` |
| `mode` | `int` | Numeric selector 1–32 or service variant `0x21`–`0x23` |
| `selector` | `int` | Present for numeric selector modes 1–32 |
| `sequence_bit` | `bool` | Whether outgoing terminal requests alternate bit 6 |
| `destination` | `str` | Decoded decimal destination digits; may be empty |
| `raw_identification` | `str` | Full identification data as hexadecimal text |
| `call_record` | `str` | Eight-byte call timestamp/sequence record as hexadecimal text |

```python
terminal = phone.metadata['terminal_id']
await phone.message('Terminal ID', terminal)
```

Changing this dictionary does not provision or send settings to the phone.

#### Example helper: `await input_t9(phone, max_length=20, timeout=60)`

Defined in [example.py](example.py), not on the `phone` object. It edits a text
string using the example's small predictive dictionary.

- **`phone`** — the supplied application phone object.
- **`max_length: int`** — total characters, including spaces, `1`..`20`;
  defaults to 20.
- **`timeout: number`** — whole-operation timeout, default 60 seconds.
- **Returns:** text on `#`, with trailing spaces removed; `None` on timeout or
  cancellation. Empty submission returns `''`.
- `2`..`9` enter a word's key sequence, `1` cycles candidate prefixes, `0`
  commits the current candidate and adds a space, and `*` deletes pending
  digits or committed characters (cancelling when empty). Unknown sequences
  are displayed as digits. The cursor is hidden at `(0, 0)` on exit.

```python
from example import input_t9

text = await input_t9(phone, max_length=20, timeout=60)
if text is not None:
    await phone.message('You typed:', text or '(empty)')
```

`call()` and `emit()` in the runner are internal JSON-bridge plumbing; use the
application methods above rather than constructing bridge messages yourself.

### Firmware capabilities versus Python helper conventions

The supplied [NOA specification, section 7](../../Dialin/NOA1X04_REMOTE_DISPLAY_PROTOCOL_SPEC.md#7-display-command-reference)
defines two independently addressed rows of 20 character cells. Its drawing
commands contain coordinates and text, not menu items or pagination rules.
The [email gateway walkthrough](../../Dialin/FAKE_EMAIL_GATEWAY_EXACT_PACKETS.md#2-acquire-display-and-ask-to-send-email)
actually displays `* YES # NO` together on the lower row: multiple choices can
be visible simultaneously.

Consequently, **one choice at a time is a convention of our `phone.menu()`
helper, not a limitation imposed by the phone**. Reserving the upper row for
a title, using `#`/`*` for paging, numeric editing, and the T9 dictionary are
also Python conventions. You can draw different layouts and interpret input
keys yourself with `screen()`/`write_row()` and `read_key()`.

For example, this displays all four choices at once, with no title row or
automatic paging:

```python
await phone.screen('1 About  2 Mailbox', '3 T9     0 Goodbye')
choice = await phone.read_key(timeout=60)
if choice == '3':
    # Run your application's T9 editor here.
    pass
```

Both strings fit within 20 characters. This custom layout does not change
the behavior of `phone.menu()`.

### Drawing individual rows and cells

Coordinates are zero-based: **row 0 is the upper row, row 1 the lower row**,
and columns are 0..19. For example:

On acquisition the service clears both rows and hides the cursor at (0, 0),
establishing a known initial canvas before starting your application.

```python
await phone.clear()
await phone.write_row(0, 'MAILBOX')
await phone.write_row(1, 'Count: 000')
await phone.write_text(7, 1, '123')  # Changes only those three cells on row 1.
await phone.cursor(10, 1, visible=True)

# One combined NOA command: text first, then a visible cursor at (6, 1).
await phone.text_and_cursor(0, 1, 'NAME: '.ljust(20), 6, 1, visible=True)
```

`write_row()` overwrites the selected row and clears its trailing cells with
spaces; the other row is untouched. `write_text()` and `text_and_cursor()`
preserve all cells outside their supplied text. Text must be printable ASCII
and fit inside the selected row; invalid coordinates or overflowing writes
are rejected rather than relying on the phone's undefined panel wrap behavior.
Newline is not a row change.

`screen()`, `write_row()` and `write_text()` explicitly restore the configured
cursor position/visibility after writing. This adds an `87` command after the
text write(s), so updating a counter does not move an input cursor. In contrast,
`text_and_cursor()` sends one `93` command with its final cursor fields.
`fill()`/`clear()` leave the cursor at (0, 0) and preserve its visibility, matching
the firmware's fill semantics.

Cursor `erase=True` replaces exactly one cell with a space, without shifting
text. In a combined command this happens **after** writing text, so an
overlapping final cursor can erase a character from that same write. Text and
cursor coordinates in a combined command can point at different rows.

All operations update the retained application screen and cursor, including
partial writes, fills and erased cells. After payment takeover the service
reconstructs both rows and restores the cursor without erasing a second time.
Application changes made during an inactivity/call-ending warning are retained
without replacing that warning; dismissal restores the application contents.
Drawing is ACK-paced and sequential, not an atomic framebuffer swap.

The ordinary wire operations for each drawing method are:

| Python method | NOA requests sent, in order |
|---|---|
| `screen(upper, lower)` | `85 00 00 <20 padded bytes>`, `85 00 01 <20 padded bytes>`, then `87` to restore the configured cursor |
| `write_row(row, text)` | `85 00 row <20 padded bytes>`, then `87` to restore the configured cursor |
| `write_text(col, row, text)` | `85 col row <exact text bytes>`, then `87` to restore the configured cursor |
| `fill(char)` / `clear()` | `86 <fill byte>`; `clear()` uses byte `20` (space) |
| `cursor(...)` | `87 col row options`; bit 0 = erase cell, bit 1 = visible cursor |
| `text_and_cursor(...)` | `93 cursor_col cursor_row options text_col text_row <exact text bytes>` |

Values in this table are hexadecimal command/payload descriptions; the codec
adds the length, CRC and frame markers. The service waits for each request's
ACK before advancing. ACK means the firmware accepted the frame/CRC, not a
confirmation that characters were visibly drawn. While a service warning is
visible, drawing calls update the retained app state and render that warning
instead; payment takeover pauses drawing until the phone releases control.

### Menu and input behavior

The example application's **3 — T9 text demo** demonstrates predictive text
using a small editable dictionary in `example.py`. Press 2..9 once per letter,
`1` to cycle matching candidates, `0` to commit a word and add a space, `*` to
delete (or cancel when empty), and `#` to submit. Try `43556 0 96753 #` for
`hello world`, or `4663` followed by `1` to change `good` to `home`.
Unknown word sequences remain visible as digits. Input is limited to 20
characters and times out after 60 seconds. This is a predictive dictionary
demo, not multi-tap entry or a full language dictionary.

#### `phone.menu(title, options, timeout=60)`

- `options` must be a nonempty mapping with single-character numeric string
  keys (`'0'`..`'9'`). Mapping iteration order determines the page order.
- The upper row displays `title`, which must be at most 20 printable ASCII
  characters. An overlong title is rejected, not scrolled or truncated.
- The lower row displays exactly one option, formatted as
  `(key + ' ' + label)[:17] + ' #>'`, then padded to 20 cells. Thus the option
  key/label portion is truncated to 17 characters and the suffix uses 3 cells.
- `#` advances to the next option; `*` goes back. Both wrap around at the ends.
- Any listed numeric key selects that option immediately, even if another
  option is currently displayed. Other keys are ignored.
- The result is the selected **key string**, not its label. Expiry returns
  `None`. The timeout covers the whole menu operation, including drawing;
  paging and other keypresses do not restart it.

Menus draw their title and first choice once; paging changes only the lower
row. For the example menu, `MILLENNIUM ONLINE` is 17 characters, and its longest
formatted option, `2 Mailbox demo #>`, is also 17 characters. The number of
entries in the Python mapping does not concatenate their labels onto a row.

#### Input, messages and completion

- `read_key(timeout)` consumes the next queued key event. Expiry returns
  `None`; `timeout=None` has no per-read deadline, but the global inactivity,
  connection and maximum-call limits still apply. Drawing does not discard
  queued keys.
- `message()` calls `screen()`, then `read_key()`. It returns that key or
  `None`; its timeout starts after the screen operation completes.
- `input_number()` accepts 1..20 as `max_length` (default 16). It writes its
  prompt once, then updates the lower row and visible input cursor together
  using `93`. Padding removes stale digits after deletion. Digits beyond the
  limit and unrelated keys are ignored. `#` submits, including an empty string;
  `*` deletes the last digit, or cancels with `None` when already empty.
  Timeout also returns `None`, and covers the whole editing operation. On exit
  the helper hides the cursor at (0, 0).
- `input_t9()` is a helper in `example.py`, not a method of `phone` and not
  firmware-provided text prediction. Its example-specific controls and small
  dictionary are described above. It also hides the cursor on exit.
- `close()` sends a close instruction to the service without waiting for an
  application reply. The service makes a best-effort stop request and closes
  the connection so mm_manager can hang up. Returning from `main()` invokes
  this automatically.

Numeric keys are strings (`'0'`..`'9'`, `'*'`, `'#'`). Other decoded keys are
`A`..`D`, `LANGUAGE`, `VOLUME_DOWN`, `VOLUME_UP`, and `NEXT_CALL`. Unmapped
keys, including repertory/keyboard codes, remain integer wire codes. Generic
keyboard text editing is not part of this initial numeric-keypad API.

Applications issue one operation at a time through the bridge. Wire requests
are stop-and-wait; key events, terminal requests, and pings continue to be
processed while waiting for ACKs. There is a bounded key queue (64 events)
and bounded application/ACK queues (8 each). Overflow ends the call rather
than consuming unbounded memory.

## Timeouts and recovery

All protocol deadlines use monotonic time. `python src/dialin/server.py --help`
lists the configurable limits, including:

| Limit | Default | CLI setting |
|---|---:|---|
| Detection after CONNECT | 10 seconds | Manager-side fixed bound |
| Handshake stage / whole handshake | 10 / 45 seconds | `--handshake-stage`, `--handshake-total` |
| Incomplete-frame inter-byte gap | 2 seconds | `--frame-gap` |
| ACK wait | 2 seconds + estimated frame serialization + 0.2s bridge allowance | `--ack`, `--line-bps` |
| Command attempts | 3 total | `--attempts` |
| Outbound idle ping | 8 seconds | `--ping` |
| Valid inbound silence | 30 seconds | `--silence` |
| User inactivity / warning grace | 120 / 30 seconds | `--idle`, `--idle-grace` |
| Whole call duration | 900 seconds | `--maximum` |
| Script import/startup | 5 seconds | `--app-start` |
| Missing application heartbeat | 10 seconds | `--app-heartbeat` |
| Best-effort final stop write | 3 seconds | `--shutdown` |

Set `--line-bps` to the actual negotiated modem speed, **not** the manager's
serial-port baud setting. The default estimate is 1200 bps. Actual modem
pacing and table-31 delays still need live validation.

Keepalives and ACKs never reset user inactivity. The inactivity warning is
removed on a keypress and the desired application screen is redrawn. The
last 30 seconds of a maximum-duration call display an ending warning.

During the phone's payment/UI takeover, drawing pauses, the latest desired
screen is retained, and user inactivity timing pauses. ACK processing,
keepalives, application supervision, receive-silence and total-call deadlines
continue. Release triggers a redraw.

The C bridge independently limits raw receive silence to 35 seconds, total
duration to one hour, local connection/send waits to two seconds, and serial
polling to about 100ms. Keep Python `--silence` at or below 30 seconds;
`--maximum` is limited to 3500 seconds so Python normally closes first.
On POSIX, bridge-mode serial writes are nonblocking with a bounded output
wait; Windows uses a write timeout. A failed write releases the call.

Malformed frames get NOA error statuses where their framing permits it;
16 consecutive errors/noise bytes end the session. Duplicate startup frames
are re-ACKed without advancing the handshake twice. Authentication uses the
received terminal ID and BCD timestamp converted to raw time values, and
checks the response against a fresh four-byte challenge. Ordinary decimal
destinations are supported; extended destination symbols are rejected.

NOA has no general duplicate suppression: identical key packets can also be
legitimate repeated keypresses. For NNK1F05 and ported 06CNB02, the controller
normalizes command bit 6 and re-ACKs identical same-bit retries without repeating
their keys; identical keys with alternating bits are distinct input. Raw U5
keyboard command `11` is accepted as integer key codes, not a local text editor.
Only idempotent positioned drawing
and ownership requests are retried. Stop is sent once and does not wait for
an ACK, since firmware cleanup may disable its UART before sending it.

## Configure a service entry

The phone must be provisioned for the service before it sends NOA frames.
The alias is the number the user dials; the actual number points to the
same modem used for the manager. Variant 2 is the helper's initial default
and follows destination entry. Variant 1 has additional preconnection/DTMF
stages outside this binary service implementation.

Patch an explicit slot in a copy of your current table 55:

```powershell
python src/dialin/provision.py --table55 tables/default/mm_table_37.bin --output mm_table_37.new.bin --slot 9 --number 18005551234 --alias 123 --variant 2
```

This replaces the selected slot's number/prompts, sets its service variant
and lookup bit, and preserves classification/payment fields and other slots.
Choose a slot whose existing classification/payment configuration fits the
service. Inspect the reported original slot. The helper writes a new file;
install it as `tables/<terminal-id>/mm_table_37.bin`, then use the phone's
normal table-download process.

To copy table 31 and explicitly adjust packet timing at the same time, add:

```text
--table31 tables/default/mm_table_1f.bin --output31 mm_table_1f.new.bin --tx-delay 10 --rx-gap 10
```

Those optional values are 10ms ticks; they are examples, not recovered
deployment requirements. The helper preserves the table-31 routing bitmap:
research identifies it as a route selector, **not** a global TTY-enable mask.
Table payload files have no leading table ID: table 55 is 570 bytes and
table 31 is 36 bytes. Call/payment policy, modem compatibility and local
prompts must match your actual phone configuration. See
[the recovered parameter map](../../Dialin/NCC_TABLE_AND_TTY_PARAMETER_MAP.md).

## Verification

```powershell
python -m unittest discover -s src/dialin -p "test_*.py" -v
python -m ruff check src/dialin
```

The Python suite exercises the recovered authentication vector, fragmented
frames, CRC errors, duplicate startup, real application subprocesses/menu
input, payment takeover, retries, inactivity, maximum duration, carrier-side
EOF, startup failure, blocked/crashed applications and sequential calls.
The crash fixture intentionally prints a traceback on stderr.

Drawing checks include exact documented frame/CRC vectors, independent row and
partial-cell updates, fill/cursor semantics, text-before-erase ordering, invalid
coordinates/overflow, warning restoration, and a real application subprocess
whose partial display changes survive a simulated payment takeover.

`test_router.c` separately checks C classification and management-byte replay
using a simulated serial device. For example, from a Visual Studio developer
prompt (write build artifacts in a temporary build directory):

```text
cl /W3 /WX /D_CRT_SECURE_NO_WARNINGS /I <repo>/src <repo>/src/dialin/test_router.c <repo>/src/mm_dialin.c <repo>/src/mm_serial.c <repo>/src/mm_tcp.c /link ws2_32.lib
test_router.exe
```

These are simulated checks. Live activation, modem negotiation, table/payment
settings, VFD behavior and real line timing still require a NOA phone.
