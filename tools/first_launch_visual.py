"""在隔离副本里对打包 EXE 做首次/再次启动截图检查。

截图只留在被 .gitignore 忽略的 build/ 下，仍需人工核对任务栏和 Alt-Tab 图标。
"""
from __future__ import annotations

import argparse
import ctypes
import json
import shutil
import subprocess
import sys
import time
import tomllib
from pathlib import Path

from PIL import Image, ImageChops, ImageGrab


TITLE = "Online Course Assistant"
EXE_NAME = "OnlineCourseAssistant.exe"
user32 = ctypes.windll.user32
user32.SetProcessDPIAware()
user32.FindWindowW.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p)
user32.FindWindowW.restype = ctypes.c_void_p
user32.GetForegroundWindow.restype = ctypes.c_void_p
user32.GetWindowTextW.argtypes = (ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int)
user32.GetWindowThreadProcessId.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong))
user32.GetWindowRect.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
user32.MoveWindow.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                              ctypes.c_int, ctypes.c_int, ctypes.c_int)
user32.SetForegroundWindow.argtypes = (ctypes.c_void_p,)
user32.ShowWindow.argtypes = (ctypes.c_void_p, ctypes.c_int)
user32.SendMessageW.argtypes = (ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t,
                                ctypes.c_ssize_t)
user32.SendMessageW.restype = ctypes.c_void_p


class Rect(ctypes.Structure):
    _fields_ = [(name, ctypes.c_long) for name in ("left", "top", "right", "bottom")]


def window_for(pid: int) -> int:
    until = time.monotonic() + 25
    while time.monotonic() < until:
        hwnd = user32.FindWindowW(None, TITLE)
        owner = ctypes.c_ulong()
        if hwnd:
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value == pid:
                return hwnd
        time.sleep(0.25)
    raise RuntimeError("EXE 启动后未出现 Online Course Assistant 窗口")


def rect_of(hwnd: int) -> Rect:
    rect = Rect()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        raise RuntimeError("无法读取窗口坐标")
    return rect


def click(x: int, y: int) -> None:
    user32.SetCursorPos(x, y)
    user32.mouse_event(0x0002, 0, 0, 0, 0)
    user32.mouse_event(0x0004, 0, 0, 0, 0)
    user32.SetCursorPos(0, 0)


def activate(hwnd: int) -> None:
    for _ in range(3):
        user32.keybd_event(0x12, 0, 0, 0)
        user32.keybd_event(0x12, 0, 2, 0)
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.3)
        if user32.GetForegroundWindow() == hwnd:
            return
    foreground = user32.GetForegroundWindow()
    title = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(foreground, title, len(title))
    raise RuntimeError(f"截图时 Online Course Assistant 不是前台窗口；当前前台：{title.value!r}")


def capture_round(exe: Path, output: Path, label: str) -> tuple[int, dict[str, Image.Image]]:
    if any(user32.FindWindowW(None, title) for title in (TITLE, "CourseMate 刷课助手")):
        raise RuntimeError("已有网课助手窗口；请先关闭，避免误操作现有程序")
    proc = subprocess.Popen([str(exe)], cwd=exe.parent)
    try:
        hwnd = window_for(proc.pid)
        width = min(1320, user32.GetSystemMetrics(0) - 120)
        height = min(1000, user32.GetSystemMetrics(1) - 120)
        user32.ShowWindow(hwnd, 9)
        user32.MoveWindow(hwnd, 70, 55, width, height, True)
        activate(hwnd)
        time.sleep(1.2)
        dpi = user32.GetDpiForWindow(hwnd)
        if dpi < 96:
            raise RuntimeError(f"异常窗口 DPI：{dpi}")
        rect = rect_of(hwnd)
        images = {}
        for name, tab_x in (("course", 90), ("ai", 220), ("settings", 345)):
            click(rect.left + tab_x, rect.top + 120)
            activate(hwnd)
            time.sleep(0.2)
            image = ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom))
            if name == "ai" and not ai_labels_visible(image):
                click(rect.left + tab_x, rect.top + 120)
                activate(hwnd)
                time.sleep(0.2)
                image = ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom))
            image.save(output / f"{label}_{name}.png")
            images[name] = image
        click(rect.left + 70, rect.top + 485)
        activate(hwnd)
        time.sleep(0.2)
        images["about"] = ImageGrab.grab(
            bbox=(rect.left, rect.top, rect.right, rect.bottom))
        images["about"].save(output / f"{label}_about.png")
        activate(hwnd)
        time.sleep(0.3)
        screen_w, screen_h = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        ImageGrab.grab(bbox=(0, screen_h - 160, screen_w, screen_h)).save(
            output / f"{label}_taskbar.png")
        user32.keybd_event(0x12, 0, 0, 0)  # Alt held while the task switcher is visible.
        try:
            user32.keybd_event(0x09, 0, 0, 0)
            user32.keybd_event(0x09, 0, 2, 0)
            time.sleep(0.6)
            ImageGrab.grab(bbox=(0, screen_h // 5,
                                 screen_w, screen_h * 4 // 5)).save(
                output / f"{label}_alt_tab.png")
        finally:
            user32.keybd_event(0x12, 0, 2, 0)
        return dpi, images
    finally:
        hwnd = user32.FindWindowW(None, TITLE)
        if hwnd:
            owner = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value == proc.pid:
                user32.SendMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.terminate()
            proc.wait(timeout=5)


def changed_fraction(a: Image.Image, b: Image.Image) -> float:
    if a.size != b.size:
        return 1.0
    # 日志时间戳每次不同，只比较标题、标签页和主要设置区。
    box = (0, 0, a.width, round(a.height * 0.73))
    delta = ImageChops.difference(a.crop(box).convert("L"), b.crop(box).convert("L"))
    return sum(count for shade, count in enumerate(delta.histogram()) if shade > 40) / (
        delta.width * delta.height)


def ai_labels_visible(image: Image.Image) -> bool:
    # 四个左列字段名不会被输入框覆盖；空白截图曾漏过整列标签。
    if image.width < 118 or image.height < 470:
        return False
    boxes = ((35, 220, 118, 270), (35, 285, 118, 340),
             (35, 365, 118, 400), (35, 430, 118, 470))
    for box in boxes:
        histogram = image.crop(box).convert("L").histogram()
        if sum(histogram[:110]) < 20:
            return False
    return True


def copy_program(source: Path, output: Path) -> Path:
    if not (source / EXE_NAME).is_file() or not (source / "_internal").is_dir():
        raise ValueError(f"源目录需要包含 {EXE_NAME} 和 _internal/")
    if (source / EXE_NAME).is_symlink() or any(
           p.is_symlink() or
           p.name.lower() in {"config.toml", "runtime", "cookies.json"} or
           p.suffix.lower() in {".db", ".log", ".lnk"}
           for p in (source / "_internal").rglob("*")):
        raise RuntimeError("依赖目录中发现疑似个人数据，拒绝复制")
    output.mkdir(parents=True)
    copy = output / "OnlineCourseAssistant"
    copy.mkdir()
    shutil.copy2(source / EXE_NAME, copy / EXE_NAME)
    shutil.copytree(source / "_internal", copy / "_internal")
    return copy


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("仅支持 Windows 交互桌面")
    source = args.package_dir.resolve()
    output = args.output_dir.resolve()
    build = Path(__file__).resolve().parents[1] / "build"
    if output == build or build not in output.parents or output.exists():
        parser.error("--output-dir 必须是 build/ 下全新的子目录")
    copy = copy_program(source, output)
    first_dpi, first = capture_round(copy / EXE_NAME, output, "first")
    generated_config = copy / "config.toml"
    if generated_config.exists():
        config = tomllib.loads(generated_config.read_text(encoding="utf-8"))
        if (config.get("account", {}).get("username") or
                config.get("account", {}).get("password") or
                config.get("answer", {}).get("api_key") or
                config.get("course", {}).get("urls") or
                config.get("course", {}).get("list")):
            raise RuntimeError("测试副本的自动保存配置含有非空个人字段")
    second_dpi, second = capture_round(copy / EXE_NAME, output, "second")
    if not all(ai_labels_visible(images["ai"]) for images in (first, second)):
        raise RuntimeError("AI 页字段标签未完整显示，首次启动视觉验收失败")
    for images in (first, second):
        if (changed_fraction(images["course"], images["ai"]) < 0.02 or
                changed_fraction(images["ai"], images["settings"]) < 0.02 or
                changed_fraction(images["settings"], images["about"]) < 0.005):
            raise RuntimeError("页面截图未明显变化，可能未成功切换页面")
    difference = {name: round(changed_fraction(first[name], second[name]), 4)
                  for name in first}
    sheet = Image.new("RGB", (first["course"].width * 2,
                              first["course"].height * 4), "white")
    for row, name in enumerate(("course", "ai", "settings", "about")):
        sheet.paste(first[name], (0, row * first[name].height))
        sheet.paste(second[name], (first[name].width, row * first[name].height))
    sheet.save(output / "first_vs_second.png")
    result = {"first_dpi": first_dpi, "second_dpi": second_dpi,
              "default_config_created_after_first": generated_config.exists(),
              "changed_fraction": difference,
              "visual_status": "截图已保存，任务栏和 Alt-Tab 图标仍需人工核对"}
    (output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    print(f"截图目录：{output}")
    return 0 if first_dpi == second_dpi and all(x < 0.03 for x in difference.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
