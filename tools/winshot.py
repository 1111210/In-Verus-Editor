"""Windows 窗口截图工具（纯标准库）。

* ``capture_client(hwnd)`` 用 GDI ``PrintWindow`` 抓取窗口客户区像素（BGRA）。
* ``write_png(path, w, h, rgb)`` 手写 PNG 编码（zlib + CRC），无需 Pillow。

只在 Windows 上有意义；其它平台调用会抛出 ``RuntimeError``。
"""

from __future__ import annotations

import ctypes
import struct
import sys
import zlib
from ctypes import wintypes
from typing import Optional, Tuple


def _require_windows() -> None:
    if sys.platform != "win32":
        raise RuntimeError("winshot 仅支持 Windows")


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


def find_window(title: str) -> Optional[int]:
    _require_windows()
    hwnd = ctypes.windll.user32.FindWindowW(None, title)
    return int(hwnd) if hwnd else None


def capture_client(hwnd: int) -> Tuple[int, int, bytes]:
    """返回 (width, height, RGB 像素字节)，RGB 为逐行 3 字节。"""
    _require_windows()
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    rect = wintypes.RECT()
    if not user32.GetClientRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
        raise RuntimeError("GetClientRect 失败")
    width = int(rect.right - rect.left)
    height = int(rect.bottom - rect.top)
    if width <= 0 or height <= 0:
        raise RuntimeError(f"窗口尺寸无效：{width}x{height}")

    hdc = user32.GetDC(wintypes.HWND(hwnd))
    memdc = gdi32.CreateCompatibleDC(hdc)
    bitmap = gdi32.CreateCompatibleBitmap(hdc, width, height)
    gdi32.SelectObject(memdc, bitmap)
    try:
        # PW_CLIENTONLY = 1，PW_RENDERFULLCONTENT = 2
        if not user32.PrintWindow(wintypes.HWND(hwnd), memdc, 1):
            user32.PrintWindow(wintypes.HWND(hwnd), memdc, 2)

        info = _BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height  # 自上而下
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = 0  # BI_RGB
        buffer = ctypes.create_string_buffer(width * height * 4)
        gdi32.GetDIBits(memdc, bitmap, 0, height, buffer, ctypes.byref(info), 0)
    finally:
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memdc)
        user32.ReleaseDC(wintypes.HWND(hwnd), hdc)

    raw = buffer.raw
    rgb = bytearray(width * height * 3)
    source = 0
    target = 0
    for _ in range(width * height):
        rgb[target] = raw[source + 2]
        rgb[target + 1] = raw[source + 1]
        rgb[target + 2] = raw[source]
        source += 4
        target += 3
    return width, height, bytes(rgb)


def write_png(path: str, width: int, height: int, rgb: bytes) -> None:
    """把 RGB 像素写成 PNG（真彩色，无 alpha）。"""
    stride = width * 3
    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter: none
        raw.extend(rgb[y * stride:(y + 1) * stride])

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    payload = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
        + chunk(b"IEND", b"")
    )
    with open(path, "wb") as handle:
        handle.write(payload)


def capture_to_png(path: str, hwnd: int) -> Tuple[int, int]:
    width, height, rgb = capture_client(hwnd)
    write_png(path, width, height, rgb)
    return width, height


def capture_screen_area(x: int, y: int, width: int, height: int) -> Tuple[int, int, bytes]:
    """从屏幕上直接拷贝一块区域（窗口被遮挡时也能拿到屏幕内容）。"""
    _require_windows()
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    screen_dc = user32.GetDC(0)
    memdc = gdi32.CreateCompatibleDC(screen_dc)
    bitmap = gdi32.CreateCompatibleBitmap(screen_dc, width, height)
    gdi32.SelectObject(memdc, bitmap)
    try:
        gdi32.BitBlt(memdc, 0, 0, width, height, screen_dc, int(x), int(y), 0x00CC0020)  # SRCCOPY
        info = _BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = 0
        buffer = ctypes.create_string_buffer(width * height * 4)
        gdi32.GetDIBits(memdc, bitmap, 0, height, buffer, ctypes.byref(info), 0)
    finally:
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memdc)
        user32.ReleaseDC(0, screen_dc)
    raw = buffer.raw
    rgb = bytearray(width * height * 3)
    source = 0
    target = 0
    for _ in range(width * height):
        rgb[target] = raw[source + 2]
        rgb[target + 1] = raw[source + 1]
        rgb[target + 2] = raw[source]
        source += 4
        target += 3
    return width, height, bytes(rgb)


def is_blank(rgb: bytes, sample_step: int = 97) -> bool:
    """粗略判断图像是否几乎纯色（PrintWindow 偶尔会返回全黑内容）。"""
    stride = 3
    values = set()
    for index in range(0, len(rgb) - stride, sample_step * stride):
        values.add(rgb[index:index + stride])
        if len(values) > 4:
            return False
    return len(values) <= 4


def capture_window_rect(hwnd: int) -> Tuple[int, int, int, int]:
    _require_windows()
    rect = wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect))
    return int(rect.left), int(rect.top), int(rect.right - rect.left), int(rect.bottom - rect.top)


def capture_to_png_smart(path: str, hwnd: int) -> Tuple[int, int, str]:
    """尽量拿到完整的窗口图像，返回 (w, h, 方式)。

    顺序：父窗口 PrintWindow(完整内容) → 子窗口 PrintWindow(客户区)
    → 屏幕区域拷贝（窗口被遮挡时的兜底）。
    """
    user32 = ctypes.windll.user32
    parent = int(user32.GetParent(wintypes.HWND(hwnd)) or 0)
    attempts = []
    if parent:
        attempts.append(("PrintWindow(parent,full)", lambda: capture_printwindow(parent, 2)))
    attempts.append(("PrintWindow(client)", lambda: capture_printwindow(hwnd, 3)))

    for method, capture in attempts:
        try:
            width, height, rgb = capture()
        except Exception:
            continue
        if width > 0 and height > 0 and not is_blank(rgb):
            write_png(path, width, height, rgb)
            return width, height, method

    target = parent or hwnd
    x, y, win_w, win_h = capture_window_rect(target)
    if win_w <= 0 or win_h <= 0:
        x, y, win_w, win_h = capture_window_rect(hwnd)
    width, height, rgb = capture_screen_area(x, y, win_w, win_h)
    write_png(path, width, height, rgb)
    return width, height, "screen"


def capture_printwindow(hwnd: int, flags: int = 3) -> Tuple[int, int, bytes]:
    """用 PrintWindow 抓取窗口（flags: 1=仅客户区, 2=完整内容, 3=两者）。"""
    _require_windows()
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    x, y, width, height = capture_window_rect(hwnd)
    if width <= 0 or height <= 0:
        raise RuntimeError("窗口矩形无效")
    hdc = user32.GetWindowDC(wintypes.HWND(hwnd))
    memdc = gdi32.CreateCompatibleDC(hdc)
    bitmap = gdi32.CreateCompatibleBitmap(hdc, width, height)
    gdi32.SelectObject(memdc, bitmap)
    try:
        ok = user32.PrintWindow(wintypes.HWND(hwnd), memdc, flags)
        info = _BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = 0
        buffer = ctypes.create_string_buffer(width * height * 4)
        gdi32.GetDIBits(memdc, bitmap, 0, height, buffer, ctypes.byref(info), 0)
    finally:
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memdc)
        user32.ReleaseDC(wintypes.HWND(hwnd), hdc)
    if not ok:
        raise RuntimeError("PrintWindow 失败")
    raw = buffer.raw
    rgb = bytearray(width * height * 3)
    source = 0
    target = 0
    for _ in range(width * height):
        rgb[target] = raw[source + 2]
        rgb[target + 1] = raw[source + 1]
        rgb[target + 2] = raw[source]
        source += 4
        target += 3
    return width, height, bytes(rgb)
