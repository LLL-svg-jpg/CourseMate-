"""CourseMate 入口。

用法：
    python main.py                  # 使用 config.toml
    python main.py -c my.toml       # 指定配置文件
    python main.py --check          # 只做环境与配置自检，不启动浏览器
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from coursemate import __version__
from coursemate.config import Config, ConfigError
from coursemate.logger import Logger

BANNER = r"""
   ____                          __  __       _
  / ___|___  _   _ _ __ ___  ___|  \/  | __ _| |_ ___
 | |   / _ \| | | | '__/ __|/ _ \ |\/| |/ _` | __/ _ \
 | |__| (_) | |_| | |  \__ \  __/ |  | | (_| | ||  __/
  \____\___/ \__,_|_|  |___/\___|_|  |_|\__,_|\__\___|
"""


def check_environment() -> list[str]:
    """启动前自检，把"跑起来才报错"提前成"启动就说清楚"。"""
    problems: list[str] = []
    if sys.version_info < (3, 11):
        problems.append(
            f"需要 Python 3.11 或更高版本（当前 {sys.version_info.major}.{sys.version_info.minor}），"
            "本项目使用标准库 tomllib 解析配置。"
        )
    try:
        import playwright  # noqa: F401
    except ImportError:
        problems.append("缺少 playwright，请执行：pip install -r requirements.txt")
    else:
        # 装了库不等于装了浏览器内核，后者要单独下载
        from pathlib import Path as _Path

        import os

        cache = _Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")) if os.environ.get(
            "PLAYWRIGHT_BROWSERS_PATH"
        ) else _Path.home() / "AppData" / "Local" / "ms-playwright"
        if not cache.exists():
            problems.append(
                "未检测到 Playwright 浏览器内核。若使用系统已装的 Chrome/Edge 可忽略；"
                "否则执行：playwright install chromium"
            )
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="coursemate", description="自动播放课程、遇题作答、继续播放的陪伴程序"
    )
    parser.add_argument("-c", "--config", default="config.toml", help="配置文件路径")
    parser.add_argument("--check", action="store_true", help="只做自检，不启动浏览器")
    parser.add_argument("-v", "--version", action="version", version=f"CourseMate {__version__}")
    args = parser.parse_args()

    print(BANNER)
    print(f"  CourseMate v{__version__}  —— 自动刷课 + AI 答题\n")

    logger = Logger()
    logger.info("正在自检运行环境...")
    for problem in check_environment():
        logger.warn(problem)

    try:
        config = Config(args.config)
    except ConfigError as exc:
        logger.error(str(exc))
        if not Path(args.config).exists() and Path("config.example.toml").exists():
            logger.info("提示：复制 config.example.toml 为 config.toml 后填写即可。")
        return 1

    logger.set_level(config.log_level)
    problems = config.validate()
    for problem in problems:
        logger.warn(problem)
    if not config.course_urls:
        logger.error("没有可学习的课程，退出。")
        return 1

    logger.info(f"已载入 {len(config.course_urls)} 门课程，倍速 {config.speed}x。")

    if args.check:
        logger.info("自检完成（--check 模式不启动浏览器）。")
        logger.save()
        return 0

    from coursemate.runner import run

    exit_code = 0
    try:
        asyncio.run(run(config))
    except KeyboardInterrupt:
        logger.info("已手动中断。", shift=True)
    except Exception as exc:
        logger.log_exception("程序运行出现未处理异常。", exc, shift=True)
        logger.error(f"详细堆栈已写入 {logger.log_file}")
        exit_code = 1
    finally:
        logger.save()
    return exit_code


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        # 打包成 exe 双击运行时，不留窗口会看不到任何输出
        if sys.stdout.isatty():
            try:
                input("\n按 Enter 退出...")
            except (EOFError, KeyboardInterrupt):
                pass
