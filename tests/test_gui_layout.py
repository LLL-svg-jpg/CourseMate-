"""设置页布局与滚动测试。

设置页改成了 Windows 设置那样的左分类右内容，**没有滚动**。
这不是审美选择，是根治残影：tkinter 在 Windows 上滚动时，
窗口先把屏幕像素搬过去、再等下一条消息重绘，中间那一瞬间必然拖影，
Python 层拦不住（WS_EX_COMPOSITED 试过，会让界面卡死）。
不滚动就没有那个瞬间。所以这里最要紧的一条是：
**每个分类在任何字号、任何窗口尺寸下都必须放得下**——
一旦放不下就会被裁掉一截，那比残影更糟。

课程页的地址列表仍然可滚（地址多时），滚动相关的断言都落在它上面。
"""
import sys
import time
import tkinter as tk
import tkinter.ttk as ttk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from coursemate.gui import CourseMateGUI, ScrollFrame

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail and not cond else ""))


class Wheel:
    def __init__(self, delta):
        self.delta = delta


class Press:
    def __init__(self, x, y):
        self.x, self.y = x, y


def step_once(sf):
    sf._tick()
    if sf._anim is not None:
        sf.after_cancel(sf._anim)
        sf._anim = None


def settle(sf, limit=600):
    moves, costs = [], []
    prev = sf.offset
    for _ in range(limit):
        if abs(sf.target - sf.offset) < 0.5:
            break
        step_once(sf)
        moves.append(abs(sf.offset - prev))
        costs.append(sf._frame_cost)
        prev = sf.offset
    return moves, costs


def walk(w, out):
    for c in w.winfo_children():
        out.append(c)
        walk(c, out)


root = tk.Tk()
app = CourseMateGUI(root)
root.geometry("1100x820")
root.update_idletasks()
root.update()

nb = next(c for w in root.winfo_children() for c in w.winfo_children()
          if isinstance(c, ttk.Notebook))
course_tab, settings_tab = nb.winfo_children()[0], nb.winfo_children()[2]
nb.select(2)
root.update_idletasks()
root.update()

SECTIONS = ["界面", "浏览器", "运行", "网络", "日志", "本地题库", "数据", "关于"]

# --- 1. 设置页：左分类右内容，不滚动 ---
print("\n== 设置页结构（Windows 设置式） ==")
kids = []
walk(settings_tab, kids)

# 曾经这里断言"设置页是滚动式的"。改成分类式之后那条需求作废了——
# 滚动正是残影的来源，现在整页不滚，所以反过来钉死"不许有滚动区"。
check("设置页没有滚动区（残影的来源）",
      not [k for k in kids if isinstance(k, ScrollFrame)],
      str([k for k in kids if isinstance(k, ScrollFrame)]))
check("设置页没有 Canvas", not [k for k in kids if isinstance(k, tk.Canvas)])
check("八个分类齐全", list(app.setting_pages) == SECTIONS, str(list(app.setting_pages)))
check("每个分类都有对应的导航项", list(app.nav_items) == SECTIONS)
check("分类不是折叠条（都有实际内容）",
      all(p.winfo_children() for p in app.setting_pages.values()))
check("Collapsible 类已移除",
      not hasattr(sys.modules["coursemate.gui"], "Collapsible"))

# --- 2. 分类切换 ---
print("\n== 分类切换 ==")
app._show_section("浏览器")
root.update_idletasks()
shown = [n for n, p in app.setting_pages.items() if p.winfo_ismapped()]
check("一次只显示一个分类", shown == ["浏览器"], str(shown))
check("当前分类被记下来了", app.current_section == "浏览器", app.current_section)
check("选中项高亮", app.nav_items["浏览器"]["label"]["style"] == "NavItemOn.TLabel",
      app.nav_items["浏览器"]["label"]["style"])
check("其余项不高亮",
      all(app.nav_items[n]["label"]["style"] == "NavItem.TLabel"
          for n in SECTIONS if n != "浏览器"))
check("选中项左侧亮起指示条", app.nav_items["浏览器"]["bar"].winfo_ismapped())
check("未选中项没有指示条",
      not app.nav_items["网络"]["bar"].winfo_ismapped())

for name in SECTIONS:
    app._show_section(name)
    root.update_idletasks()
    ok = ([n for n, p in app.setting_pages.items() if p.winfo_ismapped()] == [name]
          and app.nav_items[name]["bar"].winfo_ismapped())
    check(f"切到「{name}」正常", ok)

app._show_section("界面")
root.update_idletasks()
root.update()

# --- 3. 核心保证：每类都放得下，所以不需要滚动 ---
print("\n== 每类都放得下（不滚动的前提） ==")
holder = app.setting_pages["界面"].master
results = []
for size in (10, 15, 20, 24):              # 字号滑块的两个端点都要覆盖
    app.font_size_var.set(float(size))
    app._apply_font_size()                 # 这一步会把 minsize 顶上去
    root.update_idletasks()
    # 把窗口缩到该字号允许的最小尺寸——最坏情况
    mw, mh = root.minsize()
    for geom in (f"{mw}x{mh}", "1100x820"):
        root.geometry(geom)
        root.update_idletasks()
        root.update()
        avail = holder.winfo_height()
        for name in SECTIONS:
            app._show_section(name)
            root.update_idletasks()
            need = app.setting_pages[name].winfo_reqheight()
            results.append((geom, size, name, need, avail))

overflow = [r for r in results if r[3] > r[4]]
check("任何窗口尺寸、任何字号下都没有分类被裁掉",
      not overflow,
      "; ".join(f"{g} 字号{s} 「{n}」需要{need}px 只有{av}px"
                for g, s, n, need, av in overflow[:4]))
# 左边的分类栏本身也不能被裁——少一项就等于那类设置彻底进不去了
nav_box = app.nav_items["界面"]["row"].master
nav_bad = []
for size in (10, 15, 20, 24):
    app.font_size_var.set(float(size))
    app._apply_font_size()
    mw, mh = root.minsize()
    root.geometry(f"{mw}x{mh}")
    root.update_idletasks()
    root.update()
    for name, item in app.nav_items.items():
        row = item["row"]
        bottom = row.winfo_y() + row.winfo_height()
        if bottom > nav_box.winfo_height():
            nav_bad.append((size, name, bottom, nav_box.winfo_height()))
check("八个分类在导航栏里都完整可见（任何字号）", not nav_bad,
      "; ".join(f"字号{s}「{n}」底边{b}px 超出{h}px" for s, n, b, h in nav_bad[:4]))
nav_margin = nav_box.winfo_height() - max(
    i["row"].winfo_y() + i["row"].winfo_height() for i in app.nav_items.values())
check("导航栏留有余量（>=30px），换个 DPI 也不至于立刻裁掉", nav_margin >= 30,
      f"最大字号下只余 {nav_margin}px")
print(f"       （字号 24 时导航栏余 {nav_margin}px）")

# 余量太小等于没有余量：换台机器、换个 DPI 就又裁了
margins = [r[4] - r[3] for r in results]
check("最紧的一档也要留出像样的余量（>=25px）", min(margins) >= 25,
      f"最小余量 {min(margins)}px")
# 最小高度不能顶到超出屏幕，否则小屏上窗口摆不下
check("最小窗口高度没有超出屏幕",
      root.minsize()[1] <= max(700, root.winfo_screenheight() - 90),
      f"minsize={root.minsize()} 屏幕高 {root.winfo_screenheight()}")
tightest = min(results, key=lambda r: r[4] - r[3])
print(f"       （最紧的一档：{tightest[0]} 字号{tightest[1]} 「{tightest[2]}」"
      f"需要 {tightest[3]}px，可用 {tightest[4]}px，余 {tightest[4] - tightest[3]}px）")

root.geometry("1100x820")
app.font_size_var.set(15.0)
app._apply_font_size()
root.update_idletasks()
root.update()

# --- 白色输入框/下拉框的高度必须一致 ---
print("\n== 输入框与下拉框高度一致 ==")


def all_box_heights():
    """收集三个标签页（含设置页每个分类）里所有可见的输入框与下拉框高度。"""
    found = {}
    for idx in range(3):
        nb.select(idx)
        pages = list(app.setting_pages) if idx == 2 else [None]
        for pg in pages:
            if pg:
                app._show_section(pg)
            root.update_idletasks()
            root.update()
            got = []
            walk(nb.winfo_children()[idx], got)
            for k in got:
                if isinstance(k, (ttk.Entry, ttk.Combobox)) and k.winfo_ismapped():
                    found.setdefault(k.winfo_height(), []).append(
                        f"{'下拉' if isinstance(k, ttk.Combobox) else '输入'}框@"
                        f"{['课程', 'AI答题', pg or '设置'][idx]}")
    return found


prev_h = None
for size in (15, 20, 24):
    app.font_size_var.set(float(size))
    app._apply_font_size()
    hs = all_box_heights()
    check(f"字号{size}：所有白框高度一致", len(hs) == 1,
          "; ".join(f"{h}px({len(v)}个: {v[0]})" for h, v in sorted(hs.items())))
    h = min(hs)
    # 曾经这些框被 vista 主题钉死在 23px，调字号纹丝不动，比设过字体的地址框矮一截
    check(f"字号{size}：框高跟着字号走，不是钉死的 23px", h > 23, f"{h}px")
    if prev_h is not None:
        check(f"字号{size}：字号变大框也变高", h > prev_h, f"{prev_h} -> {h}")
    prev_h = h

app.font_size_var.set(15.0)
app._apply_font_size()
root.update_idletasks()

# --- 下拉列表：字体要跟着字号走，选完不能留蓝底 ---
print("\n== 下拉列表 ==")
all_combos = []
for _idx in range(3):
    nb.select(_idx)
    _pages = list(app.setting_pages) if _idx == 2 else [None]
    for _pg in _pages:
        if _pg:
            app._show_section(_pg)
        root.update_idletasks()
        _got = []
        walk(nb.winfo_children()[_idx], _got)
        for _k in _got:
            if isinstance(_k, ttk.Combobox) and _k not in all_combos:
                all_combos.append(_k)
check("找到了所有下拉框", len(all_combos) >= 5, str(len(all_combos)))


def popdown_fonts():
    out = set()
    for cb in all_combos:
        pd = cb.tk.call("ttk::combobox::PopdownWindow", cb)
        out.add(str(cb.tk.call(pd + ".f.l", "cget", "-font")))
    return out


for size in (15, 22):
    app.font_size_var.set(float(size))
    app._apply_font_size()
    root.update_idletasks()
    fonts = popdown_fonts()
    # 点开后弹出的列表是原生 Listbox，默认死用系统的 TkTextFont，
    # 所以以前无论怎么调字号，下拉出来的选项都是一样的小字
    check(f"字号{size}：下拉列表不再用系统默认字体",
          not any("TkTextFont" in f for f in fonts), str(fonts))
    check(f"字号{size}：下拉列表字号跟着走",
          all(str(size) in f for f in fonts), str(fonts))

app.font_size_var.set(15.0)
app._apply_font_size()
root.update_idletasks()

leftover = []
for cb in all_combos:
    cb.selection_range(0, "end")
    if not cb.selection_present():
        continue                       # 禁用态的框选不上，跳过
    cb.event_generate("<<ComboboxSelected>>")
    root.update_idletasks()
    if cb.selection_present():
        leftover.append(str(cb))
check("选完模型/服务商后不留蓝底（不用再点一下）", not leftover,
      f"{len(leftover)} 个仍是选中态: {leftover[:3]}")

# --- 切页/Tab 时输入框不该整条泛蓝 ---
print("\n== 焦点转移不该全选 ==")
nb.select(0)
app.url_list.set_items([
    {"url": "https://studyvideoh5.zhihuishu.com/stuStudy?recruitAndCourseId=EXAMPLE001", "note": "思修"},
    {"url": "https://studyvideoh5.zhihuishu.com/stuStudy?recruitAndCourseId=EXAMPLE002", "note": "英语"},
])
root.update_idletasks()
root.update()


def selected_rows():
    out = []
    for i, r in enumerate(app.url_list.rows):
        try:
            if r["entry"].selection_present():
                out.append(i)
        except tk.TclError:
            pass
    return out


# Tk 给 TEntry 的默认绑定是 "%W selection range 0 end"——
# 焦点一 traverse 进来就整条选中，切到课程页那条长地址就整片泛蓝
for i, r in enumerate(app.url_list.rows):
    r["entry"].event_generate("<<TraverseIn>>")
    root.update_idletasks()
check("Tab/切页进入地址框时不全选", not selected_rows(), str(selected_rows()))

for target in (1, 2, 0, 2, 0):
    nb.select(target)
    root.update_idletasks()
    root.update()
check("来回切换标签页后地址框仍不泛蓝", not selected_rows(), str(selected_rows()))

# 用类绑定而不是逐个绑，就是为了让后加的行也自动生效
app.url_list.add_row("https://studyvideoh5.zhihuishu.com/stuStudy?x=new", "新加的")
root.update_idletasks()
app.url_list.rows[-1]["entry"].event_generate("<<TraverseIn>>")
root.update_idletasks()
check("后来新增的地址行同样不全选", not selected_rows(), str(selected_rows()))

# 光标该落到末尾，方便接着往后打字
last = app.url_list.rows[-1]["entry"]
check("焦点进入后光标停在末尾", last.index("insert") == len(last.get()),
      f"{last.index('insert')} vs {len(last.get())}")

nb.select(2)
root.update_idletasks()
root.update()

# --- 右键复制粘贴 ---
print("\n== 右键编辑菜单 ==")
nb.select(0)
root.update_idletasks()
root.update()

menu = app._edit_menu
labels = [menu.entrycget(i, "label") for i in range(menu.index("end") + 1)
          if menu.type(i) == "command"]
check("菜单有剪切/复制/粘贴/全选", labels == ["剪切", "复制", "粘贴", "全选"], str(labels))

# tk.Menu 是原生菜单，不吃 ttk 的 Style，字体得单独给，
# 否则右键弹出来那一小块字比界面上别处明显小一号
menu_font = str(menu.cget("font"))
check("右键菜单有自己的字体设置（不是系统默认）", bool(menu_font), repr(menu_font))
check("右键菜单字号和界面一致", "15" in menu_font, repr(menu_font))
app.font_size_var.set(21.0)
app._apply_font_size()
check("调字号时右键菜单跟着变", "21" in str(menu.cget("font")),
      repr(str(menu.cget("font"))))
app.font_size_var.set(15.0)
app._apply_font_size()

# 类绑定：动态加出来的地址行也得有菜单
bound = [bool(root.bind_class(c, "<Button-3>"))
         for c in ("TEntry", "TCombobox", "TSpinbox", "Text")]
check("四类控件都绑了右键", all(bound), str(bound))

entry = app.url_list.rows[0]["entry"]
entry.delete(0, "end")
entry.insert(0, "https://studyvideoh5.zhihuishu.com/stuStudy?id=EXAMPLE003")
entry.selection_clear()
root.update_idletasks()

app._menu_target = entry
app._sync_edit_menu(entry)


def item_state(label):
    return str(menu.entrycget(menu.index(label), "state"))


check("没选中内容时「复制」是灰的", item_state("复制") == "disabled", item_state("复制"))
check("没选中内容时「剪切」是灰的", item_state("剪切") == "disabled")
check("框里有字时「全选」可用", item_state("全选") == "normal", item_state("全选"))

app._edit_select_all()
root.update_idletasks()
app._sync_edit_menu(entry)
check("全选之后「复制」变可用", item_state("复制") == "normal")
check("全选之后「剪切」变可用", item_state("剪切") == "normal")

# 真的走一遍复制 -> 粘贴，而不是只看菜单项状态
root.clipboard_clear()
app._edit_action("Copy")
root.update_idletasks()
check("复制真的把内容送进了剪贴板",
      root.clipboard_get() == "https://studyvideoh5.zhihuishu.com/stuStudy?id=EXAMPLE003",
      repr(root.clipboard_get()))

target = app.url_list.rows[1]["entry"]
target.delete(0, "end")
root.update_idletasks()
app._menu_target = target
app._edit_action("Paste")
root.update_idletasks()
check("粘贴真的把内容放进了另一个框",
      target.get() == "https://studyvideoh5.zhihuishu.com/stuStudy?id=EXAMPLE003",
      repr(target.get()))

# 选中一段再粘贴，本意是替换掉它，而不是插在旁边
target.delete(0, "end")
target.insert(0, "旧内容")
target.select_range(0, "end")
app._menu_target = target
app._edit_action("Paste")
root.update_idletasks()
check("选中后粘贴是替换，不是插在旁边",
      target.get() == "https://studyvideoh5.zhihuishu.com/stuStudy?id=EXAMPLE003",
      repr(target.get()))

target.delete(0, "end")
target.insert(0, "要剪掉的")
target.select_range(0, "end")
root.clipboard_clear()
app._menu_target = target
app._edit_action("Cut")
root.update_idletasks()
check("剪切把内容拿走了", target.get() == "", repr(target.get()))
check("剪切的内容进了剪贴板", root.clipboard_get() == "要剪掉的", repr(root.clipboard_get()))

# 只读的下拉框不能改，但要能复制
readonly = next(c for c in all_combos if str(c.cget("state")) == "readonly")
app._menu_target = readonly
readonly.select_range(0, "end") if hasattr(readonly, "select_range") else None
app._sync_edit_menu(readonly)
check("只读下拉框不给「粘贴」", item_state("粘贴") == "disabled", item_state("粘贴"))
check("只读下拉框不给「剪切」", item_state("剪切") == "disabled", item_state("剪切"))

# 日志区是只读的，但复制必须能用
app.log_text.tag_add("sel", "1.0", "end-1c")
app._menu_target = app.log_text
app._sync_edit_menu(app.log_text)
check("日志区选中后「复制」可用", item_state("复制") == "normal", item_state("复制"))
check("日志区不给「粘贴」（它是只读的）", item_state("粘贴") == "disabled")
app.log_text.tag_remove("sel", "1.0", "end")

app.url_list.rows[1]["entry"].delete(0, "end")
nb.select(2)                    # 切回设置页，下面要量滑块
root.update_idletasks()
root.update()

# --- 关窗行为：✕ 收托盘，托盘退出才真退 ---
print("\n== 关窗行为 ==")
real_tray, real_quit = app.tray, app.quit_app
quit_calls, hide_calls = [], []
app.quit_app = lambda: quit_calls.append(1)
app._hide_to_tray = lambda: hide_calls.append(1)

app.tray = object()                 # 假装托盘可用
app.on_close()
check("托盘可用时，点 ✕ 是收进托盘而不是退出",
      hide_calls == [1] and quit_calls == [], f"hide={hide_calls} quit={quit_calls}")

# 托盘创建失败时若还只是隐藏，窗口关了又没有托盘图标，
# 软件就彻底找不回来了，只能去任务管理器杀
hide_calls.clear()
app.tray = None
app.on_close()
check("托盘不可用时，点 ✕ 必须老实退出",
      quit_calls == [1] and hide_calls == [], f"hide={hide_calls} quit={quit_calls}")

app.tray, app.quit_app = real_tray, real_quit
del app._hide_to_tray               # 恢复成类上的真实方法
check("托盘菜单的「退出」接的是 quit_app，不是 on_close",
      app.quit_app.__func__ is type(app).quit_app)

# 收进托盘时 state 是 withdrawn，直接存下去下次就恢复出一个看不见的窗口
saved = []
real_write = Path.write_text


def spy(self, text, *a, **k):
    if self.name == "window.txt":
        saved.append(text)
    return real_write(self, text, *a, **k)


Path.write_text = spy
try:
    root.withdraw()
    app._save_geometry()
    root.deiconify()
finally:
    Path.write_text = real_write
check("收进托盘时不会把 withdrawn 存成窗口状态",
      saved and "withdrawn" not in saved[-1], str(saved[-1:]))

# --- 4. 字号滑块不该被误触 ---
print("\n== 字号滑块防误触 ==")
app._show_section("界面")
root.update_idletasks()
root.update()
scale = app.font_scale
sw, sh = scale.winfo_width(), scale.winfo_height()
slider_x = next((x for x in range(0, sw, 2)
                 if "slider" in scale.identify(x, sh // 2)), None)
check("能定位到滑块本身", slider_x is not None, str(slider_x))
if slider_x is not None:
    trough_x = 1 if slider_x > 20 else sw - 2
    check("点在滑槽上被挡掉（不会自己跑过去）",
          app._on_scale_press(Press(trough_x, sh // 2)) == "break")
    check("点在滑块上正常放行",
          app._on_scale_press(Press(slider_x, sh // 2)) is None)
    before = app.font_size_var.get()
    app._on_scale_press(Press(trough_x, sh // 2))
    check("误点滑槽后字号没变", app.font_size_var.get() == before,
          f"{before} -> {app.font_size_var.get()}")

def font_size_of(style_name):
    """取出字号。

    Tk 返回的字体可能是元组，也可能是 '{Microsoft YaHei UI} 21 bold' 这种字符串，
    加粗时字号还不在最后一段，所以挑第一个纯数字的 token。
    """
    f = ttk.Style().lookup(style_name, "font")
    if isinstance(f, (tuple, list)):
        return int(f[1])
    for token in str(f).split():
        if token.lstrip("-").isdigit():
            return int(token)
    raise ValueError(f"看不懂的字体值: {f!r}")


check("导航项字体跟着字号走", font_size_of("NavItem.TLabel") == 15,
      str(ttk.Style().lookup("NavItem.TLabel", "font")))
app.font_size_var.set(21.0)
app._apply_font_size()
check("调大字号后导航项也跟着变", font_size_of("NavItem.TLabel") == 21,
      str(ttk.Style().lookup("NavItem.TLabel", "font")))
check("选中态的导航项也跟着变", font_size_of("NavItemOn.TLabel") == 21)
app.font_size_var.set(15.0)
app._apply_font_size()
root.update_idletasks()

# --- 5. 课程页地址列表：滚动仍要跟手 ---
print("\n== 课程页地址列表的滚动 ==")
nb.select(0)
root.update_idletasks()
root.update()
ckids = []
walk(course_tab, ckids)
cscrolls = [k for k in ckids if isinstance(k, ScrollFrame)]
check("地址列表用的是自己那套滚动，不是 Canvas", len(cscrolls) == 1, str(len(cscrolls)))
check("课程页也没有 Canvas", not [k for k in ckids if isinstance(k, tk.Canvas)])

sf = cscrolls[0]
for i in range(9):                          # 塞够行数，撑出可滚范围
    app.url_list.add_row(f"https://x.zhihuishu.com/{i}", f"课{i}")
root.update_idletasks()
root.update()
sf.refresh()
check("行数够多时才需要滚", sf._max_offset() > 0, str(sf._max_offset()))

if sf._max_offset() > 0:
    sf.offset = sf.target = 0.0
    sf._apply(redraw=False)
    before = sf.offset
    sf._on_wheel(Wheel(-120))
    want = min(before + sf.STEP, float(sf._max_offset()))
    check("滚一格，事件返回时画面已到位（零延迟）", abs(sf.offset - want) < 0.5,
          f"{sf.offset} vs {want}")
    check("走到位就不排动画帧", sf._anim is None, str(sf._anim))
    check("移动后当场画完了", sf._frame_cost > 0, str(sf._frame_cost))

    moves, costs = [], []
    for round_no in range(4):
        d = -120 if round_no % 2 == 0 else 120
        for _ in range(20):
            prev = sf.offset
            sf._on_wheel(Wheel(d))
            moves.append(abs(sf.offset - prev))
            costs.append(sf._frame_cost)
        m, c = settle(sf)
        moves += m
        costs += c
    check("急滚时单次移动不超上限",
          max(moves) <= sf.MAX_FRAME_STEP + 0.001,
          f"max={max(moves)} limit={sf.MAX_FRAME_STEP}")
    check("每一次移动都当场画完，没有攒着", all(c > 0 for c in costs))
    worst = max(costs)
    check("单次重绘快过一帧（16ms）", worst < sf.FRAME_MS / 1000,
          f"最慢 {worst * 1000:.1f}ms")

    for _ in range(300):
        sf._on_wheel(Wheel(-120))
    settle(sf)
    check("滚到底夹住", abs(sf.offset - sf._max_offset()) < 1.0)
    for _ in range(600):
        sf._on_wheel(Wheel(120))
    settle(sf)
    check("滚回顶停在 0", abs(sf.offset) < 1.0, str(sf.offset))

    sf._on_scrollbar("moveto", "0.5")
    want = min(0.5 * sf.inner.winfo_reqheight(), float(sf._max_offset()))
    check("拖滚动条一步到位，不落在鼠标后面", abs(sf.offset - want) < 1.0,
          f"{sf.offset} vs {want}")
    check("拖滚动条不排动画补帧", sf._anim is None)

    t0 = time.perf_counter()
    for _ in range(30):
        sf._on_content_change()
    check("重复尺寸事件被短路，不会自激死循环",
          time.perf_counter() - t0 < 0.5)

# --- 6. 日志箭头 ---
print("\n== 日志箭头 ==")
style = ttk.Style()
fg = style.lookup("Arrow.TLabel", "foreground")
font = style.lookup("Arrow.TLabel", "font")
check("箭头是黑色", fg in ("#000000", "black"), str(fg))
size = font[1] if isinstance(font, (tuple, list)) else int(str(font).split()[-1])
check("箭头比正文大", int(size) >= 18, str(size))
check("展开态是实心下三角", app.log_arrow["text"] == "▼", repr(app.log_arrow["text"]))


def log_weight():
    return int(app.body.grid_rowconfigure(2)["weight"])


app._toggle_log()
root.update_idletasks()
check("收起态是实心右三角", app.log_arrow["text"] == "▶", repr(app.log_arrow["text"]))
check("收起后标签页吃掉空间（日志那行不再分权重）", log_weight() == 0, str(log_weight()))
app._toggle_log()
root.update_idletasks()
check("再展开恢复", app.log_arrow["text"] == "▼" and log_weight() > 0)
check("箭头没被宽度截断", app.log_arrow["width"] >= 3, str(app.log_arrow["width"]))

# 设置页改成分类式后不再有固定高度，曾经把日志区整个顶出了窗口
print("\n== 日志区不能被标签页挤没 ==")
for geom in ("1100x820", "1000x700"):
    root.geometry(geom)
    for size in (10, 15, 20, 24):
        app.font_size_var.set(float(size))
        app._apply_font_size()
        nb.select(2)
        app._show_section("运行")        # 最高的那一类，最容易挤爆
        root.update_idletasks()
        root.update()
        h = app.log_frame.winfo_height()
        check(f"{geom} 字号{size} 时日志区仍可见", h >= 40, f"只剩 {h}px")
app.font_size_var.set(15.0)
app._apply_font_size()
root.geometry("1100x820")
root.update_idletasks()

root.destroy()
print(f"\n通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
sys.exit(1 if FAIL else 0)
