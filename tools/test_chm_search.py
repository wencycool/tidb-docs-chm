#!/usr/bin/env python3
"""test_chm_search.py —— CHM 全文搜索（Windows"搜索"页签）集成测试。

用最小的三页样张调用真实的 ``chmcmd`` 编译两版 CHM，核对"搜索"页签需要
的三件事是否同时成立：

  1. 全文搜索库 ``/$FIftiMain`` 存在；
  2. ``/#SYSTEM`` 记录 4 的全文搜索标志已置位；
  3. ``/#WINDOWS`` 窗口定义的导航窗格样式含 HHWIN_PROP_TAB_SEARCH(0x400)。

以及关闭全文搜索时必须一个搜索结构都没有，并且关键词索引（``.hhk``）
始终不出现。最后检查索引里确实收录了正文的 ASCII 词——FPC 的索引器只认
ASCII ``a-z0-9_``，中文/日文等 CJK 字节一律当分隔符，因此 chmcmd 产物的
中文关键词搜不到（见 docs/windows-search.md）。

用法：
    .venv/bin/python tools/test_chm_search.py

没有安装 chmcmd（Free Pascal）时打印跳过说明并以 0 退出。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import build_chm as B  # noqa: E402
from chmwriter import (  # noqa: E402
    ChmReader,
    detect_full_text_search_entries,
    detect_keyword_index_entries,
    system_fulltext_search_flag,
    windows_search_tab_enabled,
)

PAGES = {
    "index.html": "<h1>TiDB Documentation</h1><p>分布式数据库概览。"
                  "chmsearchprobezz</p>",
    "tikv.html": "<h1>TiKV</h1><p>raftstore region snapshot coprocessor</p>",
    "tiflash.html": "<h1>TiFlash</h1><p>TiFlash learner replica analytical query</p>",
}

# 正文里的唯一标记词，用来断言"页面正文确实进了索引"。
MARKER = "chmsearchprobezz"
# 样张里的常见词，只作为观察输出。
TOKENS = ("raftstore", "learner", "tiflash", "tikv")


def indexed(index: bytes, token: str) -> bool:
    """FIFTI 词表按"与上一个词共享的前缀长度 + 余下字符"压缩存储，
    词首字符可能不单独出现，因此按词尾探测。"""
    return token[-6:].encode() in index


def check(name: str, *conditions: tuple[str, bool]) -> bool:
    failed = [label for label, ok in conditions if not ok]
    print(f"  {'OK  ' if not failed else 'FAIL'} {name}")
    for label in failed:
        print(f"        未满足：{label}")
    return not failed


# 中文标题：CHM 的主题表（搜索结果标题）由 chmcmd 从 <title> 原样抄字节，
# hh.exe 按系统 ANSI(GBK) 显示，所以标题必须按 ANSI 写，否则搜索结果是乱码。
PAGE_TITLES = {
    "index.html": "TiDB 文档首页",
    "tikv.html": "TiKV 分布式存储",
    "tiflash.html": "TiFlash 列存副本",
}


def write_sample(root: str) -> None:
    for name, body in PAGES.items():
        title = PAGE_TITLES[name]
        page = B.page_bytes(
            B.wrap_page(title, body, lang="zh", chm_title=True), title, "zh")
        with open(os.path.join(root, name), "wb") as fh:
            fh.write(page)
    entries = [
        B.TocEntry("首页", "index.html"),
        B.TocEntry("TiKV", "tikv.html"),
        B.TocEntry("TiFlash", "tiflash.html"),
    ]
    with open(os.path.join(root, "toc.hhc"), "wb") as fh:
        fh.write(B.build_hhc(entries).encode("gbk"))


def build(root: str, name: str, full_text_search: bool) -> str | None:
    """用 build_hhp() 生成工程并编译，返回 CHM 路径（失败返回 None）。"""
    hhp = f"{name}.hhp"
    with open(os.path.join(root, hhp), "wb") as fh:
        fh.write(B.build_hhp("TiDB v7.5 中文文档", f"{name}.chm", sorted(PAGES),
                             "zh", binary_toc=True,
                             full_text_search=full_text_search).encode("gbk"))
    res = subprocess.run(["chmcmd", "--no-html-scan", hhp], cwd=root,
                         capture_output=True)
    chm = os.path.join(root, f"{name}.chm")
    if res.returncode != 0 or not os.path.exists(chm):
        print("        chmcmd 失败：" + res.stderr.decode("utf-8", "replace")[-300:])
        return None
    return chm


def read_stream(chm: str, root: str, name: str, reader: ChmReader) -> bytes:
    """读系统流；LZX 压缩产物的内容在 MSCompressed 命名空间，需 chmls 解包。"""
    try:
        data = reader.read(name)
    except KeyError:
        data = b""
    if data:
        return data
    unpack = os.path.join(root, f"unpack-{os.path.basename(chm)}")
    os.makedirs(unpack, exist_ok=True)
    subprocess.run(["chmls", "extractall", chm, unpack], capture_output=True)
    path = os.path.join(unpack, name.lstrip("/"))
    if not os.path.exists(path):
        return b""
    with open(path, "rb") as fh:
        return fh.read()


def main() -> int:
    if not shutil.which("chmcmd"):
        print("[跳过] 未找到 chmcmd（macOS: brew install fpc），"
              "无法做全文搜索集成测试")
        return 0
    print("[1/2] 开启全文搜索的 CHM")
    ok = True
    with tempfile.TemporaryDirectory(prefix="chm-search-") as root:
        write_sample(root)
        chm = build(root, "search", full_text_search=True)
        if chm is None:
            return 1
        reader = ChmReader(chm)
        fifti = detect_full_text_search_entries(reader.files)
        system = read_stream(chm, root, "/#SYSTEM", reader)
        windows = read_stream(chm, root, "/#WINDOWS", reader)
        flag = system_fulltext_search_flag(system)
        search_tab = windows_search_tab_enabled(windows)
        ok &= check("chmcmd 全文搜索构建",
                    ("生成 /$FIftiMain", fifti == ["/$FIftiMain"]),
                    ("/#SYSTEM 全文搜索标志已置位", flag is True),
                    ("窗口定义开启搜索页签", search_tab is True),
                    ("没有关键词索引 .hhk",
                     detect_keyword_index_entries(reader.files) == []))

        strings = read_stream(chm, root, "/#STRINGS", reader)
        title = PAGE_TITLES["tiflash.html"]
        ok &= check("搜索结果标题按 ANSI 存储",
                    ("#TOPICS/#STRINGS 是 GBK 字节", title.encode("gbk") in strings),
                    ("不是 UTF-8 字节", title.encode("utf-8") not in strings))

        index = read_stream(chm, root, "/$FIftiMain", reader)
        hit = [t for t in TOKENS if indexed(index, t)]
        ok &= check("索引收录正文词",
                    (f"标记词 {MARKER} 已索引", indexed(index, MARKER)))
        print(f"        索引 {len(index)} 字节；样张词命中 {len(hit)}/{len(TOKENS)}"
              f"（{', '.join(hit) or '无'}）")
        print("        注意：chmcmd 的索引器只认 ASCII a-z0-9_，"
              "中文/日文等 CJK 字节一律当分隔符，中文关键词搜不到")

    print("[2/2] 关闭全文搜索的 CHM")
    with tempfile.TemporaryDirectory(prefix="chm-search-") as root:
        write_sample(root)
        chm = build(root, "plain", full_text_search=False)
        if chm is None:
            return 1
        reader = ChmReader(chm)
        system = read_stream(chm, root, "/#SYSTEM", reader)
        windows = read_stream(chm, root, "/#WINDOWS", reader)
        ok &= check("关闭全文搜索时不写搜索结构",
                    ("没有全文搜索库",
                     detect_full_text_search_entries(reader.files) == []),
                    ("/#SYSTEM 全文搜索标志为 0",
                     system_fulltext_search_flag(system) is False),
                    ("窗口定义也不含搜索页签",
                     windows_search_tab_enabled(windows) is False))

    print("\n结论：" + ("全部通过" if ok else "存在失败项"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
