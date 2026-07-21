"""
Pure protocol logic for transferring embroidery files to Ricoma machines.
No GUI dependencies.
"""
import os
import socket
import struct
import threading
import time
from pathlib import Path
from typing import Callable, Optional

MACHINE_PORT = 222
CHUNK_SIZE = 1024
HEADER_SIZE = 50
GREETING_SIZE = 6
ACK_SIZE = 50

# How long to wait for the 6-byte machine greeting.
# The greeting arrives piggy-backed on the SYN-ACK and may not be visible to
# all OS TCP stacks – match the original Chroma behaviour of a short read with
# silent ignore on timeout.
GREETING_TIMEOUT = 0.5

# Per-chunk socket connect / ACK timeout.
CONNECT_TIMEOUT = 10


def make_filename_83(name: str, ext: str) -> bytes:
    """
    Build an 11-byte 8.3 filename field (8 name + 3 ext, no dot, null-padded).
    Name is kept as-is (preserves case and printable ASCII); extension is
    lowercased, matching the observed Chroma behaviour.
    """
    name_part = name[:8].ljust(8, "\x00")
    ext_part = ext[:3].lower().ljust(3, "\x00")
    return (name_part + ext_part).encode("ascii", errors="replace")


def build_packet_header(
    file_size: int,
    chunk_num: int,
    is_last: bool,
    filename_83: bytes,
    chunk_payload_size: int,
) -> bytes:
    """
    Build a 50-byte packet header.

    Layout:
      [0-5]   : 6 zero bytes
      [6-7]   : file size BE uint16 (& 0xFFFF)
      [8-9]   : 2 zero bytes
      [10-11] : chunk number 1-indexed BE uint16
      [12]    : is_last flag (0 or 1)
      [13-23] : 11-byte 8.3 filename
      [24-28] : 5 zero bytes
      [29-30] : chunk payload size BE uint16
      [31]    : 1 zero byte
      [32]    : sequence byte = (0x70 + chunk_num - 1) & 0xFF
      [33-49] : 17 zero bytes
    """
    header = bytearray(HEADER_SIZE)
    struct.pack_into(">H", header, 6, file_size & 0xFFFF)
    struct.pack_into(">H", header, 10, chunk_num)
    header[12] = 1 if is_last else 0
    header[13:24] = filename_83[:11]
    struct.pack_into(">H", header, 29, chunk_payload_size)
    header[32] = (0x70 + chunk_num - 1) & 0xFF
    return bytes(header)


def _recv_cancellable(
    sock: socket.socket,
    n: int,
    cancel_event: Optional[threading.Event],
    overall_timeout: float = CONNECT_TIMEOUT,
) -> bytes:
    """
    Read exactly *n* bytes from *sock*, checking *cancel_event* every second.

    Raises:
        RuntimeError: if cancel_event is set.
        TimeoutError: if overall_timeout elapses before n bytes are received.
        RuntimeError: if the connection is closed before n bytes are received.
    """
    buf = b""
    # Poll in 1-second slices so the cancel event is noticed quickly.
    sock.settimeout(1.0)
    deadline = time.monotonic() + overall_timeout

    while len(buf) < n:
        if cancel_event and cancel_event.is_set():
            raise RuntimeError("Transfer cancelled by user.")
        if time.monotonic() > deadline:
            raise TimeoutError(
                f"Timed out waiting for {n} bytes from machine "
                f"(received {len(buf)})."
            )
        try:
            data = sock.recv(n - len(buf))
        except socket.timeout:
            continue
        if not data:
            raise RuntimeError(
                f"Connection closed by machine after {len(buf)} of {n} bytes."
            )
        buf += data

    return buf


def send_file(
    filepath: str,
    machine_ip: str,
    short_name: str,
    progress_cb: Optional[Callable[[int, int], None]] = None,
    log_cb: Optional[Callable[[str], None]] = None,
    cancel_event: Optional[threading.Event] = None,
) -> None:
    """
    Transfer a DST/DSB file to a Ricoma embroidery machine.

    Each 1024-byte chunk is sent over its own TCP connection to port 222.

    Args:
        filepath:      Path to the embroidery file on disk.
        machine_ip:    IP address of the target machine.
        short_name:    Base name for 8.3 filename field (up to 8 chars, no ext).
        progress_cb:   Called with (sent_chunks, total_chunks) after each chunk.
        log_cb:        Called with a log message string.
        cancel_event:  threading.Event; if set the transfer aborts between
                       1-second polling intervals.  Note: a blocking connect()
                       call cannot be interrupted – cancellation takes effect
                       as soon as connect() either succeeds or times out
                       (≤ CONNECT_TIMEOUT seconds).

    Raises:
        RuntimeError: on protocol errors or cancellation.
        OSError / TimeoutError: on network errors.
    """

    def _log(msg: str) -> None:
        if log_cb:
            log_cb(msg)

    path = Path(filepath)
    ext = path.suffix.lstrip(".")
    file_data = path.read_bytes()
    file_size = len(file_data)

    filename_83 = make_filename_83(short_name, ext)
    total_chunks = max(1, (file_size + CHUNK_SIZE - 1) // CHUNK_SIZE)

    _log(
        f"File: {path.name}  |  {file_size} bytes  |  {total_chunks} chunk(s)  "
        f"→  {machine_ip}:{MACHINE_PORT}"
    )

    for chunk_num in range(1, total_chunks + 1):
        # Cancel check between chunks (the only safe point to abort cleanly).
        if cancel_event and cancel_event.is_set():
            raise RuntimeError("Transfer cancelled by user.")

        start = (chunk_num - 1) * CHUNK_SIZE
        chunk_payload = file_data[start : start + CHUNK_SIZE]
        is_last = chunk_num == total_chunks

        header = build_packet_header(
            file_size=file_size,
            chunk_num=chunk_num,
            is_last=is_last,
            filename_83=filename_83,
            chunk_payload_size=len(chunk_payload),
        )

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            # connect() blocks for up to CONNECT_TIMEOUT seconds.
            # It cannot be interrupted by cancel_event, but once it returns
            # (success or timeout) the cancel check above will fire on the
            # next iteration – or the exception propagates immediately.
            sock.settimeout(CONNECT_TIMEOUT)
            sock.connect((machine_ip, MACHINE_PORT))

            # The machine sometimes sends a 6-byte greeting piggy-backed on
            # the SYN-ACK.  Use a short timeout and silently ignore failure:
            # not all OS TCP stacks surface TCP Fast Open data this way.
            sock.settimeout(GREETING_TIMEOUT)
            try:
                sock.recv(GREETING_SIZE)
            except (socket.timeout, OSError):
                pass

            # Send header + chunk payload (fast, no cancel needed here).
            sock.sendall(header + chunk_payload)

            # Read 50-byte ACK – polls every 1 second so cancel is noticed.
            _recv_cancellable(sock, ACK_SIZE, cancel_event, CONNECT_TIMEOUT)

        _log(f"  Chunk {chunk_num}/{total_chunks} ({len(chunk_payload)} B) — OK")
        if progress_cb:
            progress_cb(chunk_num, total_chunks)

    _log("Transfer complete.")
