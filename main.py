import asyncio
import shutil
from websockets.server import serve, WebSocketServerProtocol
from websockets.client import connect, WebSocketClientProtocol
from websockets.exceptions import ConnectionClosedOK, ConnectionClosedError
from bitstream import BitStream
from numpy import uint16, uint32

def assertEqual(expected, actual):
    if expected != actual:
        print("Assertion failed: EXPECTED != ACTUAL")
        print("    EXPECTED:", expected)
        print("    ACTUAL:", actual)
        raise AssertionError(f"{expected} != {actual}")

def assertGreaterOrEqual(expected, actual):
    if not expected <= actual:
        print("Assertion failed: NOT EXPECTED <= ACTUAL")
        print("    EXPECTED:", expected)
        print("    ACTUAL:", actual)
        raise AssertionError(f"{expected} > {actual}")

def log(tag, *message, right_align = False, indent = 0):
    output = " ".join(str(item) for item in message)

    if right_align:
        output += f" {' ' * 4 * indent}[{tag}]"
        rows, _ = shutil.get_terminal_size((58, 20))
        rows = int(rows)

        print(output.rjust(rows))
    else:
        print(f"[{tag}]{' ' * 4 * indent}", output)

"""
A function to debug URP blocks over a websocket proxy

:param prefix: A string denoting the URP message prefix (normally either 'urp ' or 'urp: ')
               remember to include any trailing spaces
:yields: A list with any messages that should be forwarded to the destination.
         You must .send() the next byte of data or call next(). If you call next
         it is garuanteed to be a no-op, however you can catch a StopIteration if
         the generator has finished, which allows you to directly read into send
         without needing to store the send in-case the generator has finished. The
         generator will never finish after recieving a plain next(). If you call next
         the messages that were yielded *will be yielded again the next time you call
         next or send*
:returns: A list with any messages that should be forwarded to the destination
:raises: IOError if a message is given that is invalid URP (e.g. too long)

- This function prints debug information on stdout.
- This function will not necessarily pass through messages directly, however the messages
  it asks you to forward will always be valid URP with an identical meaning to before
"""
def debug_block(tag: str, prefix: str, right_align: bool):
    log(tag, f"waiting for a new block", right_align=right_align)
    block_started = False
    messages_left_in_block = 0
    expected_bits = 8 * 8

    forward = []

    message = BitStream()
    while messages_left_in_block or not block_started:
        while len(message) < expected_bits:
            next_part = yield forward
            if next_part is None: continue
            forward = []
            if type(next_part) == str:
                if next_part.startswith(prefix):
                    log(tag, f"Message {next_part} not debugged as it was of the wrong type (str not bytes)", right_align=right_align)
                forward.append(next_part)
                continue
            if not next_part.startswith(prefix.encode()):
                log(tag, f"Binary message {next_part} ignored", right_align=right_align)
                forward.append(next_part)
                continue
            message.write(next_part[len(prefix):], bytes)

        message_copy = message.copy(expected_bits)

        read_bits = 0

        def read(type: type | None = None, n: int | None = None):
            nonlocal message
            nonlocal read_bits
            nonlocal expected_bits

            result = message.read(type, n)

            if type == bytes:
                expected_bits -= (n or result) * 8

            elif type == bool and n is None:
                expected_bits -= 1
                read_bits += 1

            elif n is not None:
                expected_bits -= n
                expected_bits += 1

            else:
                raise NotImplementedError(f"The read method is not defined for {type} when n is None")

            return result

        if messages_left_in_block == 0:
            assertEqual(expected_bits, 8 * 8)

            block_header = message.read(bytes, 8)
            expected_bits -= 8 * 8
            read_bits += 8 * 8

            # Block header message
            expected_bits = int.from_bytes(block_header[0:4], "big") * 8
            messages_left_in_block = int.from_bytes(block_header[4:], "big")
            block_started = True

            log(tag, f"incoming block ({expected_bits / 8} bytes, {messages_left_in_block} messages)", right_align=right_align)

            assertEqual(1, messages_left_in_block) # We cannot yet handle multi-message blocks
        else:
            log(tag, f"Got a message ({expected_bits / 8} bytes)", right_align=right_align)

            assertGreaterOrEqual(8, expected_bits + read_bits)
            long_header = message.read(bool)
            expected_bits -= 1
            read_bits += 1

            if long_header:
                log(tag, f"Has a long header", right_align=right_align, indent=1)

                is_request = message.read(bool)
                expected_bits -= 1
                read_bits += 1

                log(tag, f"Is a {'request' if is_request else 'response'}", right_align=right_align, indent=1)

                new_type = message.read(bool)
                expected_bits -= 1
                read_bits += 1

                new_object_id = message.read(bool)
                expected_bits -= 1
                read_bits += 1

                new_thread_id = message.read(bool)
                expected_bits -= 1
                read_bits += 1

                uncached_parts = []
                if new_type:
                    uncached_parts.append("type")
                if new_object_id:
                    uncached_parts.append("object_id")
                if new_thread_id:
                    uncached_parts.append("thread_id")

                log(tag, f"Uncached:", uncached_parts, right_align=right_align, indent=1)

                has_long_function_id = message.read(bool)
                expected_bits -= 1
                read_bits += 1

                if message.read(bool):
                    log("WARNING", f"{tag} does not adhere to specification: the first-byte reserved bit is not set to 0", right_align=right_align)
                    # Doing something with this bit means we don't adhere to the spec either, but as
                    # this is a debug tool this is considered tolerable
                expected_bits -= 1
                read_bits += 1

                has_more_flags = message.read(bool)
                expected_bits -= 1
                read_bits += 1

                if has_more_flags:
                    must_reply = message.read(bool)
                    expected_bits -= 1
                    read_bits += 1

                    synchronous = message.read(bool)
                    expected_bits -= 1

                    log(tag, f"Must reply and synchronous set to {must_reply} manually", right_align=right_align, indent=1)

                    assertEqual(must_reply, synchronous)

                    if not synchronous:
                        log("warning", f"{tag} disabled the synchronous bit, but asynchronous mode can cause deadlocks in LibreOffice. It is recommended not to activate it if one side of the remote bridge is LibreOffice", right_align=right_align)

                    for i in range(6):
                        if message.read(bool):
                            log("WARNING", f"{tag} does not adhere to specification: the second-byte reserved bit {5 - i} is not set to 0", right_align=right_align)
                        expected_bits -= 1

            else:
                log(tag, f"Has a short header", right_align=right_align, indent=1)
                two_flag_bytes = message.read(bool)
                expected_bits -= 1

                if two_flag_bytes:
                    assertGreaterOrEqual(expected_bits + read_bits, 16)
                    function_id = message.read(uint16, 6 + 8)
                    expected_bits -= 6 + 8
                else:
                    function_id = message.read(uint16, 6)
                    expected_bits -= 6

                log(tag, f"Function ID", function_id, right_align=right_align, indent=1)

            messages_left_in_block -= 1

        if messages_left_in_block == 0:
            expected_bits = 8 * 8

        forward.append(prefix.encode() + message_copy.read(bytes))

    log(tag, f"Finished processing block", right_align=right_align)
    return forward


async def handle_from_sidecar(sidecar: WebSocketServerProtocol, coolwsd: WebSocketClientProtocol):
    while sidecar.open:
        try:
            gen = debug_block("SIDECAR", "urp ", False)
            while True:
                next(gen)
                for message in gen.send(await sidecar.recv()):
                    await coolwsd.send(message)
        except StopIteration as e:
            for message in e.value:
                await coolwsd.send(message)

async def handle_from_coolwsd(sidecar: WebSocketServerProtocol, coolwsd: WebSocketClientProtocol):
    while coolwsd.open:
        try:
            gen = debug_block("COOLWSD", "urp:\n", True)
            while True:
                next(gen)
                for message in gen.send(await coolwsd.recv()):
                    await sidecar.send(message)
        except StopIteration as e:
            for message in e.value:
                await sidecar.send(message)

async def handle_connection(sidecar: WebSocketServerProtocol):
    print(f"Got a client on {sidecar.path}")
    try:
        async with connect(f"ws://localhost:9980{sidecar.path}") as coolwsd:
            await asyncio.gather(
                handle_from_sidecar(sidecar, coolwsd),
                handle_from_coolwsd(sidecar, coolwsd),
            )
    except OSError:
        print("Could not connect to COOLWSD. Is the server running?")
    except ConnectionClosedOK as e:
        print("Connection was closed:", e.reason or "no reason known")
    except ConnectionClosedError as e:
        print("Connection was unexpectedly closed:", e.reason or "no reason known")

port = 9981

async def run():
    async with serve(handle_connection, "localhost", port) as sidecar:
        await sidecar.start_serving()
        log("PROXY", f"Listening for connections on {port}")
        while sidecar.is_serving():
            await asyncio.sleep(0)

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(run())
