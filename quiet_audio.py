import ctypes
import math
import os
import struct
from ctypes import wintypes


SAMPLE_RATE = 16000
BUFFER_SECONDS = 0.25
BUFFER_BYTES = int(SAMPLE_RATE * BUFFER_SECONDS * 2)
QUIET_THRESHOLD = 0.012


class _WaveFormat(ctypes.Structure):
    _fields_ = [
        ("format_tag", wintypes.WORD),
        ("channels", wintypes.WORD),
        ("samples_per_second", wintypes.DWORD),
        ("average_bytes_per_second", wintypes.DWORD),
        ("block_align", wintypes.WORD),
        ("bits_per_sample", wintypes.WORD),
        ("extra_size", wintypes.WORD),
    ]


class _WaveHeader(ctypes.Structure):
    _fields_ = [
        ("data", ctypes.c_void_p),
        ("buffer_length", wintypes.DWORD),
        ("bytes_recorded", wintypes.DWORD),
        ("user", ctypes.c_size_t),
        ("flags", wintypes.DWORD),
        ("loops", wintypes.DWORD),
        ("next", ctypes.c_void_p),
        ("reserved", ctypes.c_size_t),
    ]


def rms_level(pcm):
    usable = len(pcm) - len(pcm) % 2
    if usable == 0:
        return 0.0
    count = usable // 2
    squares = sum(sample * sample for (sample,) in struct.iter_unpack("<h", pcm[:usable]))
    return math.sqrt(squares / count) / 32768.0


def is_quiet(level, threshold=QUIET_THRESHOLD):
    return isinstance(level, (int, float)) and 0 <= level <= threshold


class WindowsQuietDetector:
    def __init__(self, events):
        self.events = events
        self.running = False
        self._device = None
        self._buffers = []
        self._prepared = []
        self._callback = None
        self._winmm = None

    @staticmethod
    def _check(result, action):
        if result:
            raise OSError(result, f"Windows microphone {action} failed with code {result}.")

    def start(self):
        if os.name != "nt":
            raise OSError("Microphone quiet detection is available only on Windows.")
        if self.running:
            return
        self._winmm = ctypes.WinDLL("winmm", use_last_error=True)
        callback_type = ctypes.WINFUNCTYPE(
            None, wintypes.HANDLE, wintypes.UINT, ctypes.c_size_t, ctypes.c_size_t, ctypes.c_size_t
        )
        self._callback = callback_type(self._on_message)
        self._winmm.waveInOpen.argtypes = [
            ctypes.POINTER(wintypes.HANDLE), wintypes.UINT, ctypes.POINTER(_WaveFormat),
            callback_type, ctypes.c_size_t, wintypes.DWORD,
        ]
        self._winmm.waveInOpen.restype = wintypes.UINT
        self._winmm.waveInPrepareHeader.argtypes = [wintypes.HANDLE, ctypes.POINTER(_WaveHeader), wintypes.UINT]
        self._winmm.waveInPrepareHeader.restype = wintypes.UINT
        self._winmm.waveInAddBuffer.argtypes = [wintypes.HANDLE, ctypes.POINTER(_WaveHeader), wintypes.UINT]
        self._winmm.waveInAddBuffer.restype = wintypes.UINT
        self._winmm.waveInStart.argtypes = [wintypes.HANDLE]
        self._winmm.waveInStart.restype = wintypes.UINT
        self._winmm.waveInStop.argtypes = [wintypes.HANDLE]
        self._winmm.waveInStop.restype = wintypes.UINT
        self._winmm.waveInReset.argtypes = [wintypes.HANDLE]
        self._winmm.waveInReset.restype = wintypes.UINT
        self._winmm.waveInUnprepareHeader.argtypes = [wintypes.HANDLE, ctypes.POINTER(_WaveHeader), wintypes.UINT]
        self._winmm.waveInUnprepareHeader.restype = wintypes.UINT
        self._winmm.waveInClose.argtypes = [wintypes.HANDLE]
        self._winmm.waveInClose.restype = wintypes.UINT

        wave_format = _WaveFormat(1, 1, SAMPLE_RATE, SAMPLE_RATE * 2, 2, 16, 0)
        device = wintypes.HANDLE()
        self._check(self._winmm.waveInOpen(
            ctypes.byref(device), 0xFFFFFFFF, ctypes.byref(wave_format), self._callback, 0, 0x00030000
        ), "open")
        self._device = device
        self.running = True
        try:
            for _ in range(3):
                data = ctypes.create_string_buffer(BUFFER_BYTES)
                header = _WaveHeader()
                header.data = ctypes.cast(data, ctypes.c_void_p)
                header.buffer_length = BUFFER_BYTES
                self._buffers.append((data, header))
                pointer = ctypes.pointer(header)
                self._check(self._winmm.waveInPrepareHeader(device, pointer, ctypes.sizeof(header)), "prepare")
                self._prepared.append(header)
                self._check(self._winmm.waveInAddBuffer(device, pointer, ctypes.sizeof(header)), "queue")
            self._check(self._winmm.waveInStart(device), "start")
        except Exception:
            self.stop()
            raise

    def _on_message(self, device, message, instance, parameter, _):
        if message != 0x03C0 or not self.running:
            return
        try:
            pointer = ctypes.cast(parameter, ctypes.POINTER(_WaveHeader))
            header = pointer.contents
            count = min(int(header.bytes_recorded), int(header.buffer_length))
            level = rms_level(ctypes.string_at(header.data, count))
            self.events.put(("level", level))
            if self.running:
                result = self._winmm.waveInAddBuffer(device, pointer, ctypes.sizeof(_WaveHeader))
                if result:
                    self.events.put(("error", f"Windows microphone capture stopped with code {result}."))
                    self.running = False
        except Exception as error:
            self.events.put(("error", f"Windows microphone capture failed: {error}"))
            self.running = False

    def stop(self):
        device = self._device
        self.running = False
        if device is not None and self._winmm is not None:
            self._winmm.waveInStop(device)
            self._winmm.waveInReset(device)
            for header in self._prepared:
                self._winmm.waveInUnprepareHeader(device, ctypes.byref(header), ctypes.sizeof(header))
            self._winmm.waveInClose(device)
        self._device = None
        self._buffers.clear()
        self._prepared.clear()
        self._callback = None
