"""多账号绑定、会话隔离、登录超时后继续和停止回归；不访问真实课程。"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from coursemate import browser, runner
from coursemate.config import Config
from coursemate.config_writer import save_config


def account(key, username="test-user", password="test-password"):
    return {"id": key, "name": key, "platform": "", "username": username, "password": password}


def test_config():
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "config.toml"
        save_config(path, {"username": "legacy-user", "password": "legacy-password",
                           "accounts": [account("a"), account("b", password='p"\\x')],
                           "items": [{"url": "https://example.invalid/1", "note": "甲", "account_id": "b"},
                                     {"url": "https://example.invalid/2", "note": "乙"}],
                           "login_timeout_seconds": 17})
        cfg = Config(path)
        assert cfg.accounts["default"]["username"] == "legacy-user"
        assert cfg.accounts["b"]["password"] == 'p"\\x'
        assert cfg.course_items[0]["account_id"] == "b"
        assert cfg.course_items[1].get("account_id", "default") == "default"
        assert cfg.login_timeout_seconds == 17
        save_config(path, {"login_timeout_seconds": 3})
        assert cfg.login_timeout_seconds == 3
        for invalid in (0, -1, float("nan"), float("inf")):
            save_config(path, {"login_timeout_seconds": invalid})
            assert cfg.login_timeout_seconds == 120


async def test_storage():
    with tempfile.TemporaryDirectory() as temp, patch("coursemate.browser.app_dir", lambda: Path(temp)):
        a, b = account("a"), account("b")
        assert browser.account_storage_path(a) != browser.account_storage_path(b)
        assert browser.account_storage_path(a) != browser.account_storage_path(account("a", password="changed"))
        assert browser.account_storage_path(account("../outside")).parent == Path(temp) / "runtime" / "accounts"

        class Context:
            async def storage_state(self):
                return {"cookies": [], "origins": [{"origin": "https://example.invalid", "localStorage": []}]}

        await browser.persist_login(Context(), a)
        assert browser.account_storage_path(a).exists()
        assert not browser.account_storage_path(b).exists()
        passed = []

        class FakeBrowser:
            async def new_context(self, **kwargs):
                passed.append(kwargs)
                class NewContext:
                    async def add_init_script(self, script): pass
                    async def new_page(self):
                        return SimpleNamespace(set_default_timeout=lambda value: None)
                return NewContext()

        cfg = SimpleNamespace(window_size=(800, 600), headless=True)
        await browser.open_context(FakeBrowser(), cfg, a)
        await browser.open_context(FakeBrowser(), cfg, b)
        assert "storage_state" in passed[0] and "storage_state" not in passed[1]


async def test_login_timeout():
    cancelled = []
    class Adapter:
        name = "测试平台"
        async def is_logged_in(self, page): return False
        async def detect_captcha(self, page): return False
        async def login(self, page, context, username, password):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append((username, password))

    cfg = SimpleNamespace(username="wrong", password="wrong", login_timeout_seconds=0.06)
    with patch("coursemate.runner.has_verification", return_value=False):
        try:
            await runner.ensure_login(None, None, Adapter(), cfg, account=account("a"))
        except runner.LoginTimeout:
            pass
        else:
            raise AssertionError("登录等待没有超时")
    assert cancelled == [("test-user", "test-password")]


async def test_stop():
    stop = False
    cancelled = []
    async def course():
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)
    verification = asyncio.create_task(asyncio.Event().wait())
    async def request_stop():
        nonlocal stop
        await asyncio.sleep(0.03)
        stop = True
    stopper = asyncio.create_task(request_stop())
    try:
        await asyncio.wait_for(runner.watch_address(course(), verification, lambda: stop), timeout=1)
    except runner.StopRequested:
        pass
    else:
        raise AssertionError("停止必须取消当前地址")
    finally:
        verification.cancel()
        await asyncio.gather(verification, stopper, return_exceptions=True)
    assert cancelled == [True]

    async def timeout(): raise runner.VerificationTimeout
    verification = asyncio.create_task(timeout())
    await asyncio.gather(verification, return_exceptions=True)
    try:
        await asyncio.wait_for(runner.watch_address(course(), verification, lambda: False), timeout=1)
    except runner.VerificationTimeout:
        pass
    else:
        raise AssertionError("已经超时的验证任务也必须阻止当前地址继续运行")


def test_gui_accounts():
    import tkinter as tk
    from coursemate.gui import CourseMateGUI, _AccountDialog
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "config.toml"
        root = tk.Tk()
        with (patch("coursemate.gui.CONFIG_PATH", path),
              patch("coursemate.gui.app_dir", lambda: Path(temp)),
              patch.object(CourseMateGUI, "_start_tray")):
            app = CourseMateGUI(root)
            try:
                assert app.username_var.get() == app.password_var.get() == ""
                assert not hasattr(app, "account_platform_var")
                dialogs = []
                def finish_dialog(values=None):
                    dialog = next(child for child in root.winfo_children()
                                  if isinstance(child, _AccountDialog))
                    dialog.update_idletasks()
                    dialogs.append((dialog.winfo_width(), dialog.winfo_height(),
                                    set(dialog.entries),
                                    str(dialog.entries["password"].cget("show"))
                                    if "password" in dialog.entries else None))
                    if values is None:
                        dialog.cancel()
                    else:
                        for key, value in values.items():
                            dialog.entries[key].delete(0, "end")
                            dialog.entries[key].insert(0, value)
                        dialog.ok()
                app.username_var.set("legacy-user")
                app.password_var.set("legacy-password")
                root.after(100, finish_dialog)
                app._add_account()
                assert list(app._accounts) == ["default"]
                assert app.username_var.get() == "legacy-user"
                password = 'other"\\password'
                root.after(100, lambda: finish_dialog({"name": "测试账号", "username": " other-user ",
                                                      "password": password}))
                app._add_account()
                key = app._active_account_id
                assert app.username_var.get() == "other-user"
                assert app.password_var.get() == password
                assert app._accounts[key]["password"] == password
                assert dialogs[-1][2] == {"name", "username", "password"} and dialogs[-1][3] == "●"
                with patch("coursemate.gui.messagebox.showwarning") as warning:
                    root.after(100, lambda: finish_dialog({"name": "默认账号"}))
                    app._add_account()
                assert warning.called and len(app._accounts) == 2 and app._active_account_id == key
                app.url_list.set_items([{"url": "https://example.invalid/1", "account_id": key}])
                app.font_size_var.set(20)
                app._apply_font_size()
                app.url_list.add_row("https://example.invalid/2", account_id=key)
                app.url_list.add_row("https://example.invalid/3")
                from tkinter import font as tkfont
                for added in app.url_list.rows:
                    combo = added["account_combo"]
                    popdown = combo.tk.call("ttk::combobox::PopdownWindow", combo)
                    dropdown_font = combo.tk.call(popdown + ".f.l", "cget", "-font")
                    assert tkfont.Font(root=root, font=dropdown_font).actual("size") == 20
                    assert tkfont.Font(root=root, font=combo.cget("font")).actual("size") == 20
                    combo.focus_force()
                    combo.selection_range(0, "end")
                    combo.event_generate("<<ComboboxSelected>>")
                    root.after(80, root.quit)
                    root.mainloop()
                    assert not combo.selection_present()
                app.font_size_var.set(15)
                app._apply_font_size()
                root.update_idletasks()
                row = app.url_list.rows[0]
                assert row["account_combo"].winfo_reqheight() == row["entry"].winfo_reqheight()
                app.login_timeout_var.set("17")
                assert app.save(silent=True)
                cfg = Config(path)
                assert cfg.accounts[key]["username"] == "other-user"
                assert cfg.accounts[key]["password"] == password
                assert cfg.accounts["default"]["username"] == "legacy-user"
                assert cfg.course_items[0]["account_id"] == key
                app.account_combo.current(0)
                app._switch_account()
                assert app.username_var.get() == "legacy-user"
                app.account_combo.current(1)
                app._switch_account()
                assert app.password_var.get() == password
                root.after(100, lambda: finish_dialog({"name": "改名账号"}))
                app._rename_account()
                assert dialogs[-1][2] == {"name"}
                assert all(width >= 520 and height >= 180 for width, height, *_ in dialogs)
                assert app.url_list.rows[0]["account_id"] == key
                assert app.url_list.rows[0]["account_var"].get() == "改名账号"
                assert app.password_var.get() == password
                with patch("coursemate.gui.messagebox.showwarning") as warning:
                    app._remove_account()
                assert warning.called and key in app._accounts
                data = app._collect()
                data["accounts"] = [dict(value, platform="旧平台限制") for value in data["accounts"]]
                save_config(path, data)
                app.load_config()
                assert all(value["platform"] == "" for value in app._accounts.values())
                assert app.save(silent=True)
                assert all(value["platform"] == "" for value in Config(path).accounts.values())
                assert app.url_list.rows[0]["account_id"] == key
                assert app.login_timeout_var.get() == "17"
                app.url_list.set_items([{"url": "https://example.invalid/2", "account_id": "missing"}])
                assert app.url_list.rows[0]["account_var"].get() == "账号已移除"
                assert app._collect()["items"][0]["account_id"] == "missing"
                for invalid in ("0", "nan", "inf", "abc"):
                    app.login_timeout_var.set(invalid)
                    with patch("coursemate.gui.messagebox.showerror") as error:
                        assert not app.save(silent=True)
                        assert error.called
            finally:
                root.destroy()


async def test_queue(fail_kind):
    visited, identities, closed = [], [], []
    class Playwright:
        async def __aenter__(self): return object()
        async def __aexit__(self, *args): pass
    class Cache:
        def __init__(self, enabled): pass
        def close(self): pass
    class Adapter:
        name = "测试平台"
        confirm_catalog_progress = False
    async def open_session(*args):
        profile = args[-1]
        identities.append(profile["id"])
        class Context:
            browser = object()
            async def close(self): closed.append(profile["id"])
        return object(), Context()
    async def login(page, context, adapter, config, stop, profile):
        if profile["id"] == "a" and fail_kind == "login":
            raise runner.LoginTimeout
    async def course(page, adapter, url, *args):
        visited.append(url)
        if url == "first" and fail_kind == "verification":
            await asyncio.Event().wait()
        return True
    async def verification(*args):
        if identities[-1] == "a" and fail_kind == "verification":
            await asyncio.sleep(0.03)
            raise runner.VerificationTimeout
        await asyncio.Event().wait()
    async def idle(*args): await asyncio.Event().wait()
    async def persist(*args): pass
    cfg = SimpleNamespace(course_items=[{"url": "first", "account_id": "a"},
                                        {"url": "second", "account_id": "b"},
                                        {"url": "third", "account_id": "a"}],
                          accounts={"a": account("a"), "b": account("b")},
                          answer_cache=False, answer_enabled=False, retry_until_correct=False,
                          auto_submit=False, keep_browser_open=True, headless=False)
    with (patch("coursemate.runner.async_playwright", Playwright),
          patch("coursemate.runner.AnswerCache", Cache),
          patch("coursemate.runner.build_provider", return_value=None),
          patch("coursemate.runner.resolve", side_effect=lambda url: Adapter()),
          patch("coursemate.runner.launch", open_session),
          patch("coursemate.runner.open_context", open_session),
          patch("coursemate.runner.ensure_login", login),
          patch("coursemate.runner.persist_login", persist),
          patch("coursemate.runner.study_course", course),
          patch("coursemate.runner.playback_worker", idle),
          patch("coursemate.runner.tuning_worker", idle),
          patch("coursemate.runner.captcha_worker", verification),
          patch("coursemate.runner.question_worker", idle),
          patch("coursemate.runner.task_monitor", idle)):
        result = await asyncio.wait_for(runner.run(cfg), timeout=2)
    assert result is (fail_kind is None)
    assert identities == ["a", "b", "a"]
    assert visited == (["second"] if fail_kind == "login" else ["first", "second", "third"])
    assert "a" in closed


async def main():
    await test_storage()
    await test_login_timeout()
    await test_stop()
    for kind in (None, "login", "verification"):
        await test_queue(kind)


if __name__ == "__main__":
    test_config()
    test_gui_accounts()
    asyncio.run(main())
    print("多账号配置、会话隔离、凭据变更、登录与页面验证超时后继续：通过")
