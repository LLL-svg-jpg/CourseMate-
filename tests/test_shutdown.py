"""退出清理测试：关掉软件之后不能留下任何还在跑的东西。

这件事没法在同一个进程里断言——必须真的起一个进程、真的拉起浏览器、
真的走退出流程，再去系统里数进程。所以这个文件会起真实的 Edge/Chrome，
比其他测试慢（约半分钟），单独成文件方便按需跑。

为什么值得这么测：Playwright 会拉起 node driver 和一串浏览器进程，
它们都是本进程的子进程；而干活的 worker 是 daemon 线程，
被强杀时 `async with async_playwright()` 的清理根本不会执行。
真那样的话，用户关掉软件后任务管理器里还躺着十来个进程。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail and not cond else ""))


def descendants(pid: int) -> list[tuple[str, int]]:
    """列出某个进程的所有后代进程。

    用 CIM 而不是 wmic：wmic 在 Win11 上已弃用，一次调用要好几秒，
    这里要查好几次，用它整个测试会慢到没法跑。
    """
    ps = ("Get-CimInstance Win32_Process | "
          "ForEach-Object { \"$($_.Name),$($_.ParentProcessId),$($_.ProcessId)\" }")
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, timeout=60).stdout
    except Exception:
        return []
    rows = []
    for line in out.splitlines():
        parts = line.strip().split(",")
        if len(parts) != 3:
            continue
        try:
            rows.append((parts[0], int(parts[1]), int(parts[2])))
        except ValueError:
            continue
    kids: list[tuple[str, int]] = []
    frontier = {pid}
    for _ in range(6):
        nxt = set()
        for name, ppid, cpid in rows:
            if ppid in frontier and cpid not in [k[1] for k in kids]:
                kids.append((name, cpid))
                nxt.add(cpid)
        if not nxt:
            break
        frontier = nxt
    return kids


PROBE = r'''
import asyncio, os, sys, threading, time, tkinter as tk
import tkinter.messagebox as mb
sys.path.insert(0, r"{root}")
# 刷课进行中退出会先弹确认框。这里就是要测"确认之后清理干净没有"，
# 无人值守下不替它按掉的话，进程会永远停在那个弹窗上
mb.askokcancel = lambda *a, **k: True
from coursemate.gui import CourseMateGUI

root = tk.Tk()
app = CourseMateGUI(root)
root.update_idletasks(); root.update()
print("TRAY:", app.tray is not None, flush=True)

def work():
    async def run():
        from playwright.async_api import async_playwright
        from coursemate.config import detect_browser
        channel, exe = detect_browser()
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, channel=channel)
            page = await browser.new_page()
            await page.goto("about:blank")
            print("BROWSER_UP", flush=True)
            while not app.stop_event.is_set():
                await asyncio.sleep(0.1)
            await browser.close()
            print("BROWSER_CLOSED", flush=True)
    try:
        asyncio.run(run())
    except Exception as exc:
        print("BROWSER_FAIL:", exc, flush=True)

app.worker = threading.Thread(target=work, daemon=True, name="coursemate-worker")
app.worker.start()
for _ in range(300):
    root.update()
    if not app.worker.is_alive():
        break
    time.sleep(0.05)
print("PID:", os.getpid(), flush=True)
time.sleep(2)
root.update()

# 先验证点 ✕ 只是收进托盘，不能退出——刷课挂着的时候误点一下就前功尽弃了
app.on_close()
root.update()
print("AFTER_X_STATE:", root.state(), flush=True)
print("AFTER_X_WORKER_ALIVE:", app.worker.is_alive(), flush=True)
print("AFTER_X_CLOSING:", getattr(app, "_closing", False), flush=True)

# 从托盘叫回来
app._show_window()
root.update()
print("REOPENED:", root.state(), flush=True)

# 托盘菜单的「退出」才是真退出
app.quit_app()
print("CLOSED", flush=True)
'''


def main() -> int:
    print("CourseMate 退出清理测试")
    if os.name != "nt":
        print("  [SKIP] 只在 Windows 上有意义")
        return 0

    probe = Path(os.environ.get("TEMP", ".")) / "cm_shutdown_probe.py"
    probe.write_text(PROBE.format(root=str(ROOT)), encoding="utf-8")

    print("\n== 起真实浏览器，然后走退出流程 ==")
    proc = subprocess.Popen([sys.executable, "-u", str(probe)],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace")
    seen: list[str] = []
    during: list[tuple[str, int]] = []
    start = time.time()
    while time.time() - start < 120:
        line = proc.stdout.readline()
        if not line:
            break
        line = line.strip()
        seen.append(line)
        if line == "BROWSER_UP":
            time.sleep(1)
            during = descendants(proc.pid)
            print(f"    运行中拉起了 {len(during)} 个子进程："
                  f"{sorted({n for n, _ in during})}")
        if line == "CLOSED":
            break

    check("托盘图标建立起来了", any("TRAY: True" in s for s in seen),
          [s for s in seen if s.startswith("TRAY")])

    def value_of(prefix):
        for s in seen:
            if s.startswith(prefix):
                return s[len(prefix):].strip()
        return ""

    # 点 ✕ 只收进托盘，不能退出：刷课挂一两个小时，误点一下就前功尽弃
    check("点 ✕ 之后窗口收进了托盘（withdrawn）",
          value_of("AFTER_X_STATE:") == "withdrawn", value_of("AFTER_X_STATE:"))
    check("点 ✕ 之后刷课还在继续跑",
          value_of("AFTER_X_WORKER_ALIVE:") == "True",
          value_of("AFTER_X_WORKER_ALIVE:"))
    check("点 ✕ 没有进入退出流程",
          value_of("AFTER_X_CLOSING:") == "False", value_of("AFTER_X_CLOSING:"))
    check("能从托盘把窗口叫回来",
          value_of("REOPENED:") == "normal", value_of("REOPENED:"))
    if any(s.startswith("BROWSER_FAIL") for s in seen):
        print("  [SKIP] 本机起不了浏览器，跳过残留检查：",
              [s for s in seen if s.startswith("BROWSER_FAIL")][0][:120])
        proc.kill()
        return 1 if FAIL else 0

    check("浏览器真的起来了", "BROWSER_UP" in seen, str(seen[-3:]))
    check("运行时确实有子进程（否则这测试没意义）", len(during) >= 2,
          str(during))
    check("收到停止后浏览器是正常关闭的，不是被强杀",
          "BROWSER_CLOSED" in seen, str(seen[-3:]))

    try:
        proc.wait(timeout=45)
        exited = True
    except subprocess.TimeoutExpired:
        exited = False
        proc.kill()
    check("主进程自己退出了，没有卡住", exited)

    time.sleep(3)
    left = descendants(proc.pid)
    check("退出后一个子进程都不剩",
          not left, f"还剩 {[n for n, _ in left]}")
    check("主进程也确实没了", proc.poll() is not None)

    print(f"\n通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        print("失败项：" + ", ".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
