"""打包产物的身份测试：任务管理器里得显示成本软件，不是 Python。

为什么单独测这个：图标和名字有三条互不相干的路——
exe 内嵌图标、版本信息资源、快捷方式指向谁。
之前就栽在第三条上：快捷方式自己的图标是对的，但它指向 pythonw.exe，
于是跑起来的进程是 Python，任务管理器里根本看不出是这个软件。
只验前两条会让人误以为已经改好了。

需要先跑过 `python build.py`，否则整个文件跳过。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

EXE = ROOT / "dist" / "CourseMate" / "CourseMate.exe"
LNK = ROOT / "CourseMate 刷课助手.lnk"
DISPLAY = "CourseMate 刷课助手"

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail and not cond else ""))


def ps(script: str) -> str:
    # 输出里有中文，两边都必须钉死 UTF-8：
    # 不指定的话 Python 按系统的 GBK 去解，直接抛 UnicodeDecodeError
    script = "[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + script
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                           capture_output=True, text=True, timeout=60,
                           encoding="utf-8", errors="replace")
        return (r.stdout or "").strip()
    except Exception:
        return ""


def main() -> int:
    print("CourseMate 打包产物测试")
    if sys.platform != "win32":
        print("  [SKIP] 只在 Windows 上有意义")
        return 0
    if not EXE.exists():
        print(f"  [SKIP] 还没打包（{EXE} 不存在），先跑 python build.py")
        return 0

    print("\n== 版本信息（任务管理器的「名称」列读这里）==")
    info = ps(f"$fi=(Get-Item -LiteralPath '{EXE}').VersionInfo; "
              "\"$($fi.FileDescription)|$($fi.ProductName)|"
              "$($fi.FileVersion)|$($fi.CompanyName)\"")
    parts = info.split("|") if info else []
    desc = parts[0] if parts else ""
    check("FileDescription 不是空的", bool(desc),
          "空的话任务管理器只能显示文件名，跟一堆没名字的进程混在一起")
    check("FileDescription 就是软件名", desc == DISPLAY, repr(desc))
    check("ProductName 也填了", len(parts) > 1 and parts[1] == DISPLAY,
          repr(parts[1] if len(parts) > 1 else ""))
    check("版本号不是空的", len(parts) > 2 and bool(parts[2]), repr(parts[2:3]))

    print("\n== exe 内嵌图标 ==")
    ico = ROOT / "assets" / "app.ico"
    check("图标素材在", ico.exists())
    if ico.exists():
        raw = ico.read_bytes()
        blob = EXE.read_bytes()
        needle = raw[len(raw) // 2: len(raw) // 2 + 64]
        check("图标数据确实打进了 exe", needle in blob,
              "exe 里找不到当前 assets/app.ico 的数据，可能打包用的是旧图标")

    print("\n== 快捷方式指向谁 ==")
    check("根目录有快捷方式", LNK.exists(), str(LNK))
    if LNK.exists():
        target = ps("$sh=New-Object -ComObject WScript.Shell; "
                    f"$sh.CreateShortcut('{LNK}').TargetPath")
        check("快捷方式指向 exe，不是 pythonw",
              target.lower().endswith("coursemate.exe"),
              f"指向了 {target}——这样跑起来的进程是 Python，"
              f"任务管理器里显示的就是 Python 和它的图标")
        check("指向的 exe 确实存在", Path(target).exists() if target else False, target)

    print("\n== 启动脚本 ==")
    bat = ROOT / "启动.bat"
    if bat.exists():
        # 只看真正会执行的 start 行。注释里也会提到 pythonw，
        # 拿整段文本比位置的话，会被注释带偏
        starts = [ln.strip() for ln in
                  bat.read_text(encoding="utf-8", errors="replace").splitlines()
                  if ln.strip().lower().startswith("start ")]
        exe_at = next((i for i, ln in enumerate(starts) if "CourseMate.exe" in ln), -1)
        py_at = next((i for i, ln in enumerate(starts) if "pythonw" in ln), 99)
        check("启动.bat 里 exe 排在 pythonw 前面", exe_at >= 0 and exe_at < py_at,
              f"exe 在第 {exe_at} 条 start，pythonw 在第 {py_at} 条——"
              f"先跑 pythonw 的话任务管理器里还是显示 Python")

    print(f"\n通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        print("失败项：" + ", ".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
