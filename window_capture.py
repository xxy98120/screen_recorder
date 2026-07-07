"""仅捕获指定窗口自身画面（不受其他窗口遮挡影响）

使用 Windows PrintWindow API 直接渲染窗口内容到内存画布，
只输出该窗口本身的像素，不包含其他重叠窗口。
"""

import ctypes
from ctypes import wintypes

import cv2
import numpy as np


# ── Win32 API ──────────────────────────────────────────
user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

# 常量
PW_CLIENTONLY = 0x1
PW_RENDERFULLCONTENT = 0x2
SRCCOPY = 0x00CC0020
BI_RGB = 0
DIB_RGB_COLORS = 0


# RECT 结构
class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


# BITMAPINFOHEADER 结构
class BITMAPINFOHEADER(ctypes.Structure):
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


# BITMAPINFO 结构（32位不需要调色板）
class BITMAPINFO(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", BITMAPINFOHEADER),
    ]


# ── 工具函数 ──────────────────────────────────────────

def capture_window_content(hwnd: int, client_only: bool = False) -> np.ndarray | None:
    """
    捕获指定窗口的内容（BGR 格式 numpy 数组）。
    使用 PrintWindow API，不受其他窗口遮挡影响。

    Args:
        hwnd: 窗口句柄
        client_only: True=只捕获客户区（不含标题栏边框），False=包含窗口框架

    Returns:
        BGR 格式 numpy 数组，窗口无效返回 None
    """
    # 获取窗口矩形
    rect = RECT()
    if client_only:
        user32.GetClientRect(hwnd, ctypes.byref(rect))
        # GetClientRect 返回的 left,top 始终为 0
        width = rect.right
        height = rect.bottom
        # 需要转换成屏幕坐标来获取窗口位置偏移
        pt = (wintypes.LONG * 2)(0, 0)
        user32.ClientToScreen(hwnd, ctypes.byref(pt))
        offset_x, offset_y = pt[0], pt[1]
    else:
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        offset_x, offset_y = rect.left, rect.top

    if width <= 0 or height <= 0 or width > 4000 or height > 4000:
        return None

    # 创建设备上下文
    hdc_screen = user32.GetDC(0)
    hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
    hbmp = gdi32.CreateCompatibleBitmap(hdc_screen, width, height)

    if not hbmp:
        user32.ReleaseDC(0, hdc_screen)
        return None

    gdi32.SelectObject(hdc_mem, hbmp)

    # PrintWindow 捕获窗口内容到内存 DC。现代应用优先使用 PW_RENDERFULLCONTENT。
    flags = PW_CLIENTONLY if client_only else PW_RENDERFULLCONTENT
    result = user32.PrintWindow(hwnd, hdc_mem, flags)
    if not result and not client_only:
        result = user32.PrintWindow(hwnd, hdc_mem, 0)

    if not result:
        gdi32.DeleteObject(hbmp)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(0, hdc_screen)
        return None

    # 准备 BITMAPINFO
    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = width
    bmi.bmiHeader.biHeight = -height  # top-down 位图
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = BI_RGB

    # 读取像素数据
    pixel_data = (ctypes.c_ubyte * (width * height * 4))()
    gdi32.GetDIBits(hdc_mem, hbmp, 0, height, pixel_data, ctypes.byref(bmi), DIB_RGB_COLORS)

    # 清理 GDI 资源
    gdi32.DeleteObject(hbmp)
    gdi32.DeleteDC(hdc_mem)
    user32.ReleaseDC(0, hdc_screen)

    # 转换为 numpy 数组 (BGRA -> BGR)
    try:
        img = np.frombuffer(pixel_data, dtype=np.uint8).reshape(height, width, 4)
        img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
        return img
    except Exception:
        return None


def capture_window_region(hwnd: int) -> tuple[int, int, int, int] | None:
    """获取窗口的区域 (left, top, right, bottom)"""
    try:
        if not user32.IsWindow(hwnd):
            return None
        rect = RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        return (rect.left, rect.top, rect.right, rect.bottom)
    except Exception:
        return None
