import asyncio
import shutil
from websockets.server import serve, WebSocketServerProtocol
from websockets.client import connect, WebSocketClientProtocol


def log(tag, *message, right_align = False):
    output = ""
    for item in message:
        output += str(item)

    if right_align:
        output += f" [{tag}]"
        rows, _ = shutil.get_terminal_size((58, 20))
        rows = int(rows)

        print(output.rjust(rows))
    else:
        print(f"[{tag}]", output)

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
def debug_block(tag, prefix, right_align):
    log(tag, f"waiting for a new block", right_align=right_align)
    block_started = False
    messages_left_in_block = 0
    expected_bytes = 8

    forward = []

    message = prefix.encode()
    while messages_left_in_block or not block_started:
        while len(message) < expected_bytes + len(prefix):
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
            message += next_part[len(prefix):]

        next_message = prefix.encode() + message[expected_bytes + len(prefix):]
        message = message[:expected_bytes + len(prefix)]
        unprefixed = message[len(prefix):]

        if messages_left_in_block == 0:
            # Block header message
            expected_bytes = int.from_bytes(unprefixed[0:4])
            messages_left_in_block = int.from_bytes(unprefixed[4:])
            log(tag, f"incoming block ({expected_bytes} bytes)", right_align=right_align)
            block_started = True

            assert messages_left_in_block == 1 # We cannot yet handle multi-message blocks
        else:
            messages_left_in_block -= 1
            log(tag, f"Got a message ({len(unprefixed)} bytes)", right_align=right_align)

        if messages_left_in_block == 0:
            expected_bytes = 8

        forward.append(message)
        message = next_message

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

async def run():
    async with serve(handle_connection, "localhost", 9981) as sidecar:
        await sidecar.start_serving()
        while sidecar.is_serving():
            await asyncio.sleep(0)

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(run())