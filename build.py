"""把 CourseMate 打包成带图标的 exe。

用法：
    python build.py            # 目录模式（默认）
    python build.py --onefile  # 单文件 exe（不推荐，见下）

默认用目录模式而不是单文件，是实测的结果：单文件版在本机
--windowed 下 bootloader 会卡住，进程活着但界面永远不出来，
且没有任何日志——这种失败最难排查。目录模式启动也快得多
（单文件每次运行都要把 60+MB 解压到临时目录）。

产物在 dist/ 下。换图标只需替换 assets/app.ico 后重新运行本脚本。

为什么要打包：.pyw 在资源管理器里显示的是 Python 的图标，那由文件关联决定，
改不了；只有 exe 能把图标嵌进文件本身，让它在文件夹里显示成羽毛。
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
BUILD = ROOT / "build"
APP_NAME = "CourseMate"
DISPLAY_NAME = "CourseMate 刷课助手"
ICON = ROOT / "assets" / "app.ico"


def _version() -> tuple[int, int, int, int]:
    text = (ROOT / "coursemate" / "__init__.py").read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("__version__"):
            raw = line.split("=", 1)[1].strip().strip('"').strip("'")
            parts = [int(x) for x in raw.split(".")[:3]]
            while len(parts) < 4:
                parts.append(0)
            return tuple(parts)  # type: ignore[return-value]
    return (0, 1, 0, 0)


def write_version_file(path: Path) -> Path:
    """写 Windows 版本信息资源。

    任务管理器的「名称」列读的是 FileDescription，不是文件名。
    不写这个的话，那一栏只能显示 CourseMate.exe，
    跟一堆没名字的进程混在一起，看不出是哪个软件。
    详情页的「说明」「产品名称」也都来自这里。
    """
    v = _version()
    # 0804 = 简体中文，04B0 = Unicode 代码页
    path.write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={v}, prodvers={v},
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('080404B0', [
        StringStruct('CompanyName', '{DISPLAY_NAME}'),
        StringStruct('FileDescription', '{DISPLAY_NAME}'),
        StringStruct('FileVersion', '{".".join(str(x) for x in v)}'),
        StringStruct('InternalName', '{APP_NAME}'),
        StringStruct('OriginalFilename', '{APP_NAME}.exe'),
        StringStruct('ProductName', '{DISPLAY_NAME}'),
        StringStruct('ProductVersion', '{".".join(str(x) for x in v)}'),
        StringStruct('LegalCopyright', '仅供学习研究使用'),
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [0x804, 1200])])
  ]
)
""", encoding="utf-8")
    return path

# 随程序分发、用户不改的东西
DATA_FILES = [
    ("assets", "assets"),
]
# 打包器有时看不出这些是被用到的（延迟 import / 动态调用）
HIDDEN_IMPORTS = [
    "anthropic",
    "httpx",
    "playwright",
    "playwright.async_api",
    "tkinter",
    "tkinter.ttk",
    "tkinter.filedialog",
    "tkinter.messagebox",
]
# playwright 自带 node 驱动，必须整包带上，否则打包后启动不了浏览器。
# pystray 用来在系统托盘放图标，它的 Windows 后端是运行时按平台挑的，
# PyInstaller 静态分析看不出来，所以也得整包收
COLLECT_ALL = ["playwright", "pystray", "PIL"]


def check_prerequisites() -> list[str]:
    problems = []
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        problems.append(
            "缺少 PyInstaller，请执行：\n"
            "  pip install pyinstaller --index-url https://pypi.org/simple"
        )
    for mod in ("playwright", "anthropic", "httpx"):
        try:
            __import__(mod)
        except ImportError:
            problems.append(f"缺少 {mod}，请先双击「安装依赖.bat」")
    if not ICON.exists():
        problems.append(f"找不到图标 {ICON}，请先放好 assets/app.ico")
    return problems


def build(onefile: bool = True) -> int:
    for path in (DIST, BUILD):
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name", APP_NAME,
        "--noconfirm",
        "--clean",
        # 图标嵌进 exe，资源管理器里才会显示成自己的图标
        "--icon", str(ICON),
        # 版本信息资源：任务管理器的「名称」列就是读这里的 FileDescription。
        # 不能放 build/ 下——上面刚把那个目录整个删了
        "--version-file", str(write_version_file(ROOT / "version_info.txt")),
        # GUI 程序：不要弹出黑色控制台窗口
        "--windowed",
        "--onefile" if onefile else "--onedir",
    ]
    for src, dst in DATA_FILES:
        cmd += ["--add-data", f"{ROOT / src}{';' if sys.platform == 'win32' else ':'}{dst}"]
    for mod in HIDDEN_IMPORTS:
        cmd += ["--hidden-import", mod]
    for pkg in COLLECT_ALL:
        cmd += ["--collect-all", pkg]
    cmd.append(str(ROOT / "CourseMate.pyw"))

    print("正在打包，首次可能要几分钟...\n")
    print(" ".join(cmd[:8]), "...\n")
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode != 0:
        print("\n打包失败。上面的输出里通常能看到缺了什么模块。")
        return result.returncode

    # 把用户需要的附属文件放到 exe 旁边
    target = DIST if onefile else DIST / APP_NAME
    for name in ("config.example.toml", "README.md"):
        src = ROOT / name
        if src.exists():
            shutil.copy2(src, target / name)

    # 把依赖目录设为隐藏：用户打开文件夹时应该只看到那个羽毛图标的 exe，
    # 而不是一堆看不懂的 dll。隐藏不影响程序读取它。
    internal = target / "_internal"
    if internal.exists() and sys.platform == "win32":
        try:
            import ctypes

            FILE_ATTRIBUTE_HIDDEN = 0x02
            ctypes.windll.kernel32.SetFileAttributesW(str(internal), FILE_ATTRIBUTE_HIDDEN)
            print("已隐藏 _internal 依赖目录")
        except Exception:
            pass

    exe = target / f"{APP_NAME}.exe"
    if exe.exists():
        size = exe.stat().st_size / 1024 / 1024
        point_shortcut_at_exe(exe)
        print(f"\n打包完成：{exe}")
        print(f"体积：{size:.1f} MB")
        print(f"\n把 {target} 里的内容整个拷走就能在别的电脑上用"
              f"（对方仍需装 Chrome 或 Edge）。")
    else:
        print(f"\n打包命令成功了，但没找到 {exe}，请检查 dist/ 目录。")
        return 1
    return 0


def point_shortcut_at_exe(exe: Path) -> None:
    """把根目录那个快捷方式改成指向 exe。

    以前它指向 pythonw.exe（.pyw 只是参数）。快捷方式自己的图标是对的，
    但**跑起来的进程是 pythonw.exe**——任务管理器里就显示成 Python
    和 Python 的图标，看不出是这个软件。指向 exe 才对得上。
    """
    if sys.platform != "win32":
        return
    link = ROOT / f"{DISPLAY_NAME}.lnk"
    ps = f"""
$sh = New-Object -ComObject WScript.Shell
$lnk = $sh.CreateShortcut('{link}')
$lnk.TargetPath = '{exe}'
$lnk.WorkingDirectory = '{exe.parent}'
$lnk.IconLocation = '{exe},0'
$lnk.Description = '{DISPLAY_NAME}'
$lnk.Save()
"""
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                           capture_output=True, text=True, timeout=60)
        if r.returncode == 0:
            print(f"\n已把「{link.name}」指向打包好的 exe")
        else:
            print(f"\n提示：快捷方式没能更新（{r.stderr.strip()[:80]}），"
                  f"请直接运行 {exe}")
    except Exception as exc:
        print(f"\n提示：快捷方式没能更新（{exc}），请直接运行 {exe}")


def main() -> int:
    problems = check_prerequisites()
    if problems:
        print("无法打包：\n")
        for p in problems:
            print(f"  · {p}")
        return 1
    return build(onefile="--onefile" in sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
