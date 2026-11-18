"""Microphone capture.

A single input stream feeds the whole daemon. Blocks of 16-bit mono PCM arrive
on a bounded queue; if a consumer stalls, the oldest audio is dropped rather
than letting latency grow without bound.

`mute()` exists for a specific reason: when ODIN speaks, its own voice reaches
the microphone and the recogniser would happily transcribe it. Muting drops
captured blocks for the duration, and `drain()` discards whatever was buffered
so the recogniser never sees ODIN talking to itself.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Iterator

import numpy as np

log = logging.getLogger("odin.audio")


def rms(block: bytes) -> float:
    """Root-mean-square level of an int16 PCM block (0 .. 32767)."""
    if not block:
        return 0.0
    samples = np.frombuffer(block, dtype=np.int16)
    if samples.size == 0:
        return 0.0
    # float64 accumulation: squaring int16 overflows a 16-bit type.
    return float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))


class MicrophoneError(RuntimeError):
    pass


class Microphone:
    def __init__(
        self,
        device: str | int | None = None,
        sample_rate: int = 16000,
        block_ms: int = 125,
        queue_blocks: int = 32,
    ):
        self.sample_rate = sample_rate
        self.block_frames = max(1, int(sample_rate * block_ms / 1000))
        self.device = device if device not in ("", None) else None
        self._queue: queue.Queue[bytes] = queue.Queue(maxsize=queue_blocks)
        self._stream = None
        self._muted = threading.Event()
        self._dropped = 0

    # -- lifecycle --------------------------------------------------------
    def start(self) -> "Microphone":
        # Imported here, not at module scope: PortAudio is a system library and
        # everything that does not touch the microphone (checks, intent
        # matching, actions) must keep working without it.
        import sounddevice as sd

        def callback(indata, frames, time_info, status):  # noqa: ANN001
            if status:
                log.debug("input stream status: %s", status)
            if self._muted.is_set():
                return
            try:
                self._queue.put_nowait(bytes(indata))
            except queue.Full:
                self._dropped += 1
                try:  # make room by discarding the oldest block
                    self._queue.get_nowait()
                    self._queue.put_nowait(bytes(indata))
                except (queue.Empty, queue.Full):
                    pass

        try:
            self._stream = sd.RawInputStream(
                samplerate=self.sample_rate,
                blocksize=self.block_frames,
                device=self.device,
                dtype="int16",
                channels=1,
                callback=callback,
            )
            self._stream.start()
        except Exception as exc:  # sounddevice raises a variety of types
            raise MicrophoneError(f"could not open microphone: {exc}") from exc
        log.info(
            "microphone open (%d Hz, %d-frame blocks, device=%s)",
            self.sample_rate, self.block_frames, self.device or "default",
        )
        return self

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        if self._dropped:
            log.debug("dropped %d audio blocks over the session", self._dropped)

    def __enter__(self) -> "Microphone":
        return self.start()

    def __exit__(self, *exc_info) -> None:
        self.stop()

    # -- muting -----------------------------------------------------------
    def mute(self) -> None:
        self._muted.set()

    def unmute(self) -> None:
        self._muted.clear()
        self.drain()

    def discard_for(self, seconds: float) -> None:
        """Consume and throw away audio for `seconds`.

        Used straight after the wake word fires. Detection happens on a
        *partial* result, i.e. part-way through "hey odin", so the tail of the
        wake word is still arriving; left alone it leaks into the command
        recogniser and can end the command window before the speaker starts.
        """
        if seconds <= 0:
            self.drain()
            return
        deadline = time.monotonic() + seconds
        self.drain()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            self.read(timeout=min(0.25, max(0.01, remaining)))
        self.drain()

    def drain(self) -> None:
        """Discard everything currently buffered."""
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return

    # -- consumption ------------------------------------------------------
    def read(self, timeout: float = 1.0) -> bytes | None:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def blocks(self, timeout: float = 1.0) -> Iterator[bytes]:
        while True:
            block = self.read(timeout=timeout)
            if block is not None:
                yield block
