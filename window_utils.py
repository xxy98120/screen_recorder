"""Windows 窗口枚举工具（使用 ctypes 调用 Win32 API）

宽松过滤，尽可能展示所有可录制的应用窗口。
"""

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path


@dataclass
class WindowInfo:
    """窗口信息"""
    hwnd: int
    title: str
    class_name: str
    process_name: str
    left: int
    top: int
    right: int
    bottom: int
    visible: bool
    is_app_window: bool   # 是否具有 WS_EX_APPWINDOW 样式

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    @property
    def display_name(self) -> str:
        name = self.title.strip()
        if not name:
            name = f"[{self.class_name}]"
        parts = [name, f"{self.width}x{self.height}"]
        if self.process_name:
            parts.append(self.process_name)
        parts.append(f"HWND:0x{self.hwnd:X}")
        return " | ".join(parts)


# Win32 API
_EnumWindows = ctypes.windll.user32.EnumWindows
_EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
_GetWindowTextW = ctypes.windll.user32.GetWindowTextW
_GetWindowTextLengthW = ctypes.windll.user32.GetWindowTextLengthW
_IsWindowVisible = ctypes.windll.user32.IsWindowVisible
_GetWindowRect = ctypes.windll.user32.GetWindowRect
_GetDesktopWindow = ctypes.windll.user32.GetDesktopWindow
_GetClassNameW = ctypes.windll.user32.GetClassNameW
_GetWindowLongW = ctypes.windll.user32.GetWindowLongW
_GetWindow = ctypes.windll.user32.GetWindow
_GetAncestor = ctypes.windll.user32.GetAncestor
_GetLastActivePopup = ctypes.windll.user32.GetLastActivePopup
_IsIconic = ctypes.windll.user32.IsIconic  # 是否最小化
_GetWindowThreadProcessId = ctypes.windll.user32.GetWindowThreadProcessId
_OpenProcess = ctypes.windll.kernel32.OpenProcess
_QueryFullProcessImageNameW = ctypes.windll.kernel32.QueryFullProcessImageNameW
_CloseHandle = ctypes.windll.kernel32.CloseHandle
_DwmGetWindowAttribute = ctypes.windll.dwmapi.DwmGetWindowAttribute

GWL_EXSTYLE = -20
GW_OWNER = 4
GA_ROOTOWNER = 3
DWMWA_CLOAKED = 14
WS_EX_APPWINDOW = 0x00040000
WS_EX_TOOLWINDOW = 0x00000080
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

# 已知的系统/后台窗口类名（过滤掉）
SYSTEM_CLASSES = {
    "Shell_TrayWnd",                 # 任务栏
    "Shell_SecondaryTrayWnd",
    "NotifyIconOverflowWindow",      # 通知区域溢出
    "SysListView32",                 # 系统列表视图（桌面图标）
    "#32770",                        # 对话框
    "Button",                        # 按钮
    "ComboBox",                      # 组合框
    "Edit",                          # 编辑框
    "Static",                        # 静态文本
    "MSTaskListWClass",              # 任务栏按钮
    "ReBarWindow32",                 # 任务栏 Rebar
    "MSTaskSwWClass",                # 任务切换
    "WorkerW",                       # 桌面背景
    "Progman",                       # 程序管理器
    "SysTabControl32",               # 标签控件
    "ToolbarWindow32",               # 工具栏
    "msctls_statusbar32",            # 状态栏
    "ScrollBar",                     # 滚动条
    "IME",                           # 输入法
    "Windows.UI.Composition.DesktopWindowContentBridge",
}

# 已知系统窗口标题（过滤掉）
SYSTEM_TITLES = {
    "Program Manager",
    "Microsoft Text Input Application",
    "Windows Input Experience",
    "Narrator",
}

FRAME_HOST_PROCESS = "ApplicationFrameHost.exe"
NOISE_TITLE_PREFIXES = (
    "imestatuspop_wndname",
)


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


def _get_window_title(hwnd: int) -> str:
    """获取窗口标题"""
    length = _GetWindowTextLengthW(hwnd) + 1
    buffer = ctypes.create_unicode_buffer(max(length, 2))
    _GetWindowTextW(hwnd, buffer, length)
    return buffer.value


def _get_window_class(hwnd: int) -> str:
    """获取窗口类名"""
    buffer = ctypes.create_unicode_buffer(256)
    _GetClassNameW(hwnd, buffer, 256)
    return buffer.value


def _has_style(hwnd: int, style_flag: int) -> bool:
    """检查窗口是否具有指定扩展样式"""
    style = _GetWindowLongW(hwnd, GWL_EXSTYLE)
    return bool(style & style_flag)


def _has_owner(hwnd: int) -> bool:
    """检查窗口是否有所有者窗口。"""
    return bool(_GetWindow(hwnd, GW_OWNER))


def _is_cloaked(hwnd: int) -> bool:
    """检查窗口是否被 DWM 隐藏。"""
    cloaked = wintypes.DWORD()
    try:
        result = _DwmGetWindowAttribute(
            hwnd,
            DWMWA_CLOAKED,
            ctypes.byref(cloaked),
            ctypes.sizeof(cloaked),
        )
        return result == 0 and cloaked.value != 0
    except Exception:
        return False


def _is_taskbar_like_window(hwnd: int) -> bool:
    """筛选任务栏里常见的顶层应用窗口。"""
    is_visible = bool(_IsWindowVisible(hwnd))
    is_minimized = bool(_IsIconic(hwnd))
    if not (is_visible or is_minimized):
        return False
    if _has_style(hwnd, WS_EX_TOOLWINDOW):
        return False
    if _is_cloaked(hwnd) and not is_minimized:
        return False
    if _has_style(hwnd, WS_EX_APPWINDOW):
        return True
    return not _has_owner(hwnd)


def _get_process_name(hwnd: int) -> str:
    """根据窗口句柄获取进程可执行文件名。"""
    pid = wintypes.DWORD()
    _GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return ""

    handle = _OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        return ""

    try:
        buf = ctypes.create_unicode_buffer(260)
        size = wintypes.DWORD(len(buf))
        if _QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return Path(buf.value).name
    except Exception:
        pass
    finally:
        _CloseHandle(handle)
    return ""


def get_visible_windows() -> list[WindowInfo]:
    """获取任务栏中通常可见的应用窗口列表。"""
    windows: list[WindowInfo] = []
    desktop_hwnd = _GetDesktopWindow()
    seen_hwnds = set()

    def callback(hwnd: int, _lparam: int) -> bool:
        # 跳过桌面
        if hwnd == desktop_hwnd or hwnd in seen_hwnds:
            return True
        seen_hwnds.add(hwnd)

        title = _get_window_title(hwnd)
        class_name = _get_window_class(hwnd)
        process_name = _get_process_name(hwnd)
        visible = bool(_IsWindowVisible(hwnd))
        is_iconic = bool(_IsIconic(hwnd))
        is_app = _has_style(hwnd, WS_EX_APPWINDOW)
        rect = RECT()
        _GetWindowRect(hwnd, ctypes.byref(rect))

        win = WindowInfo(
            hwnd=hwnd,
            title=title,
            class_name=class_name,
            process_name=process_name,
            left=rect.left,
            top=rect.top,
            right=rect.right,
            bottom=rect.bottom,
            visible=visible,
            is_app_window=is_app,
        )
        windows.append(win)
        return True

    proc = _EnumWindowsProc(callback)
    _EnumWindows(proc, 0)

    # 过滤规则（宽松版）
    filtered = []
    for w in windows:
        # 跳过已知的系统窗口类
        if w.class_name in SYSTEM_CLASSES:
            continue
        # 跳过已知的系统标题
        title = w.title.strip()
        if title in SYSTEM_TITLES:
            continue
        if any(title.startswith(prefix) for prefix in NOISE_TITLE_PREFIXES):
            continue
        if not title:
            continue
        if w.width < 50 or w.height < 50:
            continue
        if not _is_taskbar_like_window(w.hwnd):
            continue

        filtered.append(w)

    # UWP 应用可能同时暴露 ApplicationFrameHost 和真实进程，优先保留真实进程。
    deduped: dict[str, WindowInfo] = {}
    for w in filtered:
        key = w.title.strip().lower()
        old = deduped.get(key)
        if old is None:
            deduped[key] = w
            continue
        if old.process_name == FRAME_HOST_PROCESS and w.process_name != FRAME_HOST_PROCESS:
            deduped[key] = w
        elif w.process_name != FRAME_HOST_PROCESS and old.process_name != FRAME_HOST_PROCESS:
            # 同标题多窗口时保留尺寸更大的主窗口。
            old_area = old.width * old.height
            new_area = w.width * w.height
            if new_area > old_area:
                deduped[key] = w

    result = list(deduped.values())
    result.sort(key=lambda w: (w.title.lower(), w.process_name.lower()))
    return result


def get_window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    """获取窗口位置 (left, top, right, bottom)，窗口关闭时返回 None"""
    try:
        if not ctypes.windll.user32.IsWindow(hwnd):
            return None
        rect = RECT()
        if not _GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        return (rect.left, rect.top, rect.right, rect.bottom)
    except Exception:
        return None


def is_window_minimized(hwnd: int) -> bool:
    """窗口是否已最小化。"""
    try:
        return bool(_IsIconic(hwnd))
    except Exception:
        return False
