"""页面结构诊断。

用途：当章节列表识别失败时，把页面的真实结构导出成文本。

存在的理由很实际——平台随时改版，我们手上的选择器早晚失效。
失效时程序只会说"未能读取到章节列表"，而这句话对排查毫无帮助：
到底是没登录、页面没加载完、还是选择器变了？分不清。

导出一份结构，问题就从"猜"变成"看"：把文件发给维护者，
或者自己照着往 LESSON_CANDIDATES 里补一条即可。
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .logger import Logger
from .paths import app_dir

logger = Logger()

# 章节列表元素的 class 里通常会带这些词
KEYWORDS = ("video", "chapter", "lesson", "catalog", "catalogue", "item",
            "list", "node", "section", "tree", "menu", "course", "clearfix")

# 把页面里出现次数较多的 class 统计出来——重复出现的元素通常就是列表项
_JS_COLLECT = """
() => {
    const out = {
        url: location.href,
        title: document.title,
        bodyText: (document.body ? document.body.innerText : '').slice(0, 600),
        frames: [...document.querySelectorAll('iframe')].map(f => ({
            src: (f.src || '').slice(0, 160), id: f.id, cls: f.className,
        })).slice(0, 12),
        hasVideo: !!document.querySelector('video'),
        classCounts: [],
        repeatedGroups: [],
    };

    // class 出现频次统计
    const counts = new Map();
    document.querySelectorAll('*').forEach(el => {
        const cls = (el.className && el.className.baseVal !== undefined)
            ? el.className.baseVal : el.className;
        if (typeof cls !== 'string') return;
        cls.trim().split(/\\s+/).filter(Boolean).forEach(c => {
            counts.set(c, (counts.get(c) || 0) + 1);
        });
    });
    out.classCounts = [...counts.entries()]
        .filter(([c, n]) => n >= 3 && n <= 300)
        .sort((a, b) => b[1] - a[1]).slice(0, 60);

    // 找出"同一个父节点下有多个同 class 子节点"的结构，这就是列表的样子
    const groups = [];
    document.querySelectorAll('ul, ol, div').forEach(parent => {
        const kids = [...parent.children];
        if (kids.length < 3) return;
        const first = kids[0];
        const cls = (typeof first.className === 'string' ? first.className : '').trim();
        if (!cls) return;
        const same = kids.filter(k =>
            (typeof k.className === 'string' ? k.className : '').trim() === cls).length;
        if (same >= 3) {
            groups.push({
                childClass: cls.slice(0, 90),
                count: same,
                parentClass: (typeof parent.className === 'string'
                    ? parent.className : '').slice(0, 70),
                tag: first.tagName,
                sample: (first.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 70),
            });
        }
    });
    // 同一个 childClass 只留一条，按数量排序
    const seen = new Set();
    out.repeatedGroups = groups
        .sort((a, b) => b.count - a.count)
        .filter(g => { if (seen.has(g.childClass)) return false;
                       seen.add(g.childClass); return true; })
        .slice(0, 25);
    return out;
}
"""


async def dump_page_structure(page, candidates: tuple[str, ...] = (),
                              label: str = "page") -> Path | None:
    """把当前页面结构写成文本文件，返回文件路径。"""
    lines: list[str] = []

    def w(text: str = "") -> None:
        lines.append(text)

    try:
        data = await page.evaluate(_JS_COLLECT)
    except Exception as exc:
        logger.warn(f"页面结构导出失败：{Logger.summarize(exc)}")
        return None

    w("CourseMate 页面结构诊断")
    w(f"时间：{datetime.now():%Y-%m-%d %H:%M:%S}")
    w("=" * 70)
    w(f"地址：{data.get('url', '')}")
    w(f"标题：{data.get('title', '')}")
    w(f"页面里有 <video> 元素：{'是' if data.get('hasVideo') else '否'}")
    w()

    frames = data.get("frames") or []
    w(f"--- iframe（{len(frames)} 个）---")
    for f in frames:
        w(f"  id={f.get('id') or '-'}  class={f.get('cls') or '-'}")
        w(f"     src={f.get('src') or '-'}")
    if not frames:
        w("  （无）")
    w()

    if candidates:
        w("--- 当前候选选择器的命中情况 ---")
        for sel in candidates:
            try:
                n = len(await page.query_selector_all(sel))
            except Exception:
                n = -1
            mark = "命中" if n > 0 else ("查询出错" if n < 0 else "未命中")
            w(f"  {sel:28} {mark} {n if n > 0 else ''}")
        w()

    groups = data.get("repeatedGroups") or []
    w("--- 疑似列表结构（同一父节点下重复出现的同类子元素）---")
    w("    这些最可能就是章节列表，把 childClass 前面加点号即可作为选择器")
    if groups:
        for g in groups:
            cls_sel = "." + ".".join(g["childClass"].split()) if g["childClass"] else "?"
            w(f"  {g['count']:>3} 个  {cls_sel}")
            w(f"          标签={g['tag']}  父级class={g['parentClass']}")
            if g.get("sample"):
                w(f"          首项文本：{g['sample']}")
    else:
        w("  （没找到明显的重复结构，可能页面还没加载完，或内容在 iframe 里）")
    w()

    w("--- 含关键词的 class 频次 ---")
    hits = [(c, n) for c, n in (data.get("classCounts") or [])
            if any(k in c.lower() for k in KEYWORDS)]
    for c, n in hits[:35]:
        w(f"  {n:>4} 次  .{c}")
    if not hits:
        w("  （无）")
    w()

    w("--- 页面可见文字开头 ---")
    w((data.get("bodyText") or "").strip()[:600] or "（空白，页面可能没加载出来）")

    try:
        out_dir = app_dir() / "runtime"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"page_dump_{label}_{datetime.now():%Y%m%d_%H%M%S}.txt"
        path.write_text("\n".join(lines), encoding="utf-8")
        return path
    except OSError as exc:
        logger.warn(f"写入诊断文件失败：{Logger.summarize(exc)}")
        return None
