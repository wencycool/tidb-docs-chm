#!/usr/bin/env python3
"""
verify_chm.py —— 检查 CHM 的直接打开、离线资源、目录与版式规则。

背景：第三方阅读器（macOS 上的"CHM 阅读器-畅享版"、CHM Reader - Enjoy 等）
除目录树外，还可能把 CHM 内的**关键词索引**（``*.hhk``）合并进侧栏，表现为目录树
末尾多出一长串平铺条目。本脚本用于独立核对：

  1. ``/toc.hhc`` 与 Windows 原生二进制目录是否完整
  2. 是否混入关键词索引（会污染侧栏）
  3. 目录树统计：顶层章节数、节点总数、最大层级
  4. 目录指向的 HTML 是否都在 CHM 内
  5. 主题与图片是否使用短 ASCII 哈希文件名
  6. 是否残留网页模板、页首导航、远程显示资源或本地断链
  7. 有序列表是否明确写入层级类型
  8. ``/#SYSTEM`` 是否明确声明 ``index.html`` 和 ``toc.hhc``
  9. ITSF 是否按 Section 0、ITSP 目录、正文的 Windows 标准顺序写入
 10. Windows"搜索"页签所需的全文搜索结构是否与构建意图一致

用法：
    python3 tools/verify_chm.py dist/tidb-docs-cn/tidb-docs-cn.chm
    python3 tools/verify_chm.py --expect-search yes dist/tidb-docs-cn/tidb-docs-cn.chm

``--expect-search`` 取值：

  * ``auto``（默认）：只报告全文搜索结构，不做判定
  * ``yes``：必须存在全文搜索索引，且 ``/#SYSTEM`` 声明了全文搜索标志
  * ``no``：不得存在全文搜索索引

退出码：0 = 全部通过；1 = 存在兼容性、离线资源、目录或版式问题；2 = 参数错误。
"""

from __future__ import annotations

import os
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import unquote

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from chmwriter import (  # noqa: E402
    ChmReader,
    detect_auxiliary_index_entries,
    detect_full_text_search_entries,
    detect_keyword_index_entries,
    parse_system_records,
    system_fulltext_search_flag,
    windows_search_tab_enabled,
)

TOKEN_RE = re.compile(r"<UL>|</UL>|<LI>", re.I)
LOCAL_RE = re.compile(r'name="Local"\s+value="([^"]*)"', re.I)
# /#IDXHDR 是 hhc.exe 产物里的索引头（只有全文搜索时也存在）。FPC 不写它，
# 因此它不算错误，只在声明要搜索时作为提示：Windows 实机若没有"搜索"页签，
# 这是与微软编译器产物的第一处结构差异。
IDXHDR_MARKER = "/#IDXHDR"
BINARY_TOC = ("/#TOCIDX", "/#TOPICS", "/#STRINGS", "/#URLTBL", "/#URLSTR")
UTF8_BOM = b"\xef\xbb\xbf"
TOPIC_NAME_RE = re.compile(r"^/p[0-9a-f]{16}\.html$")
ASSET_NAME_RE = re.compile(r"^/m[0-9a-f]{16}\.[a-z0-9]+$")
ATTR_RE = re.compile(r'\b(?:href|src)\s*=\s*["\']([^"\']+)["\']', re.I)
REMOTE_ASSET_RE = re.compile(
    r'<(?:img|script|iframe|video|source)\b[^>]*\bsrc\s*=\s*["\']https?://|'
    r'<link\b[^>]*\bhref\s*=\s*["\']https?://', re.I
)


def looks_like_text(data: bytes) -> bool:
    return data.startswith((UTF8_BOM, b"<", b"\n", b"\r", b" "))


def extract_chm(chm_path: str) -> str | None:
    """用 FPC 的 chmls 把（可能被 LZX 压缩的）CHM 解包到临时目录，返回目录路径。"""
    if not shutil.which("chmls"):
        return None
    tmp = tempfile.mkdtemp(prefix="verify-chm-")
    res = subprocess.run(["chmls", "extractall", chm_path, tmp], capture_output=True)
    return tmp if res.returncode == 0 else None


def tree_stats(hhc_text: str) -> tuple[int, int, int]:
    """返回 (顶层节点数, 节点总数, 最大层级 1-based)。"""
    depth = 0
    top = total = 0
    max_depth = 1
    for m in TOKEN_RE.finditer(hhc_text):
        token = m.group(0).upper()
        if token == "<UL>":
            depth += 1
        elif token == "</UL>":
            depth = max(0, depth - 1)
        else:
            total += 1
            max_depth = max(max_depth, depth)
            if depth <= 1:
                top += 1
    return top, total, max_depth


def system_text(records: dict[int, list[bytes]], code: int) -> str:
    values = records.get(code, [])
    if not values:
        return ""
    return values[0].rstrip(b"\0").decode("gbk", "replace").replace("\\", "/")


def parse_args(argv: list[str]) -> tuple[str, str] | None:
    """解析 ``[--expect-search auto|yes|no] <chm 路径>``，不合法返回 None。"""
    expect = "auto"
    path = ""
    rest = list(argv)
    while rest:
        arg = rest.pop(0)
        if arg.startswith("--expect-search="):
            expect = arg.split("=", 1)[1]
        elif arg == "--expect-search":
            if not rest:
                return None
            expect = rest.pop(0)
        elif arg.startswith("-") or path:
            return None
        else:
            path = arg
    if not path or expect not in ("auto", "yes", "no"):
        return None
    return path, expect


def check_search(names: list[str], system_fts_flag: bool | None,
                 search_tab: bool | None, expect: str) -> tuple[bool, list[str], str]:
    """判定全文搜索结构与构建意图是否一致。

    返回 (是否通过, 失败说明列表, 提示说明列表)。关键词索引（``.hhk`` /
    ``#IVB`` / ``#INDEX``）会污染第三方阅读器侧栏，无论期望与否一律失败；
    全文搜索库（``/$FIftiMain``）则按 expect 判定。除了搜索库本身，
    Windows"搜索"页签还要求 ``/#SYSTEM`` 记录 4 的全文搜索标志与
    ``/#WINDOWS`` 窗口定义的 HHWIN_PROP_TAB_SEARCH 位同时成立。
    """
    keyword_entries = detect_keyword_index_entries(names)
    keyword_entries += detect_auxiliary_index_entries(names)
    keyword_entries = sorted(set(keyword_entries))
    fulltext = detect_full_text_search_entries(names)
    notes: list[str] = []
    failures: list[str] = []

    if keyword_entries:
        failures.append("关键词索引会被阅读器平铺追加到目录树末尾，"
                        f"必须从 CHM 中移除：{keyword_entries}")

    if search_tab is None:
        notes.append("CHM 内没有 /#WINDOWS 窗口定义：hh.exe 会用只有目录的内置默认"
                     "窗口，即使有全文搜索库也不会出现'搜索'页签")
    elif search_tab is False:
        notes.append("窗口定义未开启 HHWIN_PROP_TAB_SEARCH：hh.exe 左侧不会出现"
                     "'搜索'页签（需要 .hhp 的 [WINDOWS] 段把导航窗格样式设为"
                     " 0x63520 这类含 0x400 的值）")

    if expect == "yes":
        if not fulltext:
            failures.append("要求全文搜索（--expect-search yes），"
                            "但 CHM 内没有发现全文搜索索引结构")
        elif system_fts_flag is not True:
            failures.append("/#SYSTEM 记录 4 未声明全文搜索标志，"
                            "Windows hh.exe 不会显示'搜索'页签")
        elif search_tab is not True:
            failures.append("窗口定义未开启'搜索'页签，"
                            "Windows hh.exe 不会显示'搜索'页签")
        if not any(n.lower().startswith(IDXHDR_MARKER.lower()) for n in names):
            notes.append("CHM 内没有 /#IDXHDR（微软 hhc.exe 的全文搜索产物都带它，"
                         "FPC chmcmd 只在 Binary Index=Yes 时才写）；Windows 实机"
                         "若没有'搜索'页签，这是与微软产物的第一处结构差异")
    elif expect == "no":
        if fulltext or system_fts_flag:
            failures.append("未启用全文搜索（--expect-search no），"
                            f"却发现搜索结构：{fulltext or '/#SYSTEM 全文搜索标志'}")
    else:  # auto：不判定"该不该有"，只要求内部自洽
        if fulltext and system_fts_flag is False:
            failures.append("存在全文搜索索引，但 /#SYSTEM 记录 4 未置位，"
                            "Windows hh.exe 不会显示'搜索'页签（构建参数与实际产物不一致）")
        if fulltext and search_tab is not True:
            failures.append("存在全文搜索索引，但窗口定义没有开启'搜索'页签，"
                            "hh.exe 左侧不会出现搜索页签")
        if not fulltext and system_fts_flag:
            failures.append("/#SYSTEM 声明开启了全文搜索，但 CHM 内没有搜索索引结构")

    return not failures, failures, notes


def main() -> int:
    parsed = parse_args(sys.argv[1:])
    if parsed is None:
        usage = [ln for ln in __doc__.strip().splitlines() if "verify_chm.py" in ln]
        print("用法：\n  " + "\n  ".join(usage[-2:]), file=sys.stderr)
        return 2
    path, expect_search = parsed
    reader = ChmReader(path)
    names = sorted(reader.files)

    # 内容是否可直接读取；LZX 压缩的 CHM 需要先解包再核对正文/目录树
    try:
        readable = looks_like_text(reader.read("/index.html"))
    except KeyError:
        readable = False
    root = None
    if not readable:
        root = extract_chm(path)

    def read_bytes(name: str) -> bytes:
        if root:
            with open(os.path.join(root, name.lstrip("/")), "rb") as fh:
                return fh.read()
        return reader.read(name)

    keyword_index_files = detect_keyword_index_entries(names)
    auxiliary_index_files = detect_auxiliary_index_entries(names)
    index_files = sorted(set(keyword_index_files) | set(auxiliary_index_files))
    fulltext_files = detect_full_text_search_entries(names)
    binary = [n for n in names if n in BINARY_TOC]
    hhc = [n for n in names if n.lower().endswith(".hhc")]

    print(f"CHM       : {path}")
    print(f"条目总数  : {len(names)}（含系统文件）")
    print("压缩方式  : " + ("LZX 压缩（内容已用 chmls 解包核对）" if root
                            else ("未压缩" if readable else "LZX 压缩（未找到 chmls，无法解包核对）")))
    print(f"目录源    : {', '.join(hhc) if hhc else '（无 .hhc）'}"
          + ("，另有二进制目录树" if binary else ""))
    print(f"索引文件  : {', '.join(index_files) if index_files else '无'}")
    print(f"全文数据库: {', '.join(fulltext_files) if fulltext_files else '无'}"
          f"（--expect-search {expect_search}）")

    ok = True
    layout_ok = reader.windows_layout_ok()
    print(f"ITSF 布局 : {'Windows 标准顺序' if layout_ok else '偏移或写入顺序错误'}")
    if not layout_ok:
        ok = False
        print("  [失败] 必须按 Section 0 -> ITSP 目录 -> 正文顺序写入，"
              "否则 hh.exe 会报 mk:@MSITStore 无法打开")
    directory_ok = reader.windows_directory_ok()
    print(f"目录块布局: {'Windows PMGL/PMGI 规范' if directory_ok else 'quickref 或根索引错误'}")
    if not directory_ok:
        ok = False
        print("  [失败] PMGL/PMGI 每 5 项 quickref、相对偏移或根索引不符合 hh.exe 要求")
    try:
        system = reader.read("/#SYSTEM")
    except KeyError:
        system = b""
    records = parse_system_records(system) if len(system) >= 4 else {}
    default_topic = system_text(records, 2)
    contents_file = system_text(records, 0)
    startup_ok = (default_topic == "index.html" and "/index.html" in reader.files
                  and contents_file == "toc.hhc" and "/toc.hhc" in reader.files)
    print(f"启动记录  : 默认页 {default_topic or '缺失'}，目录 {contents_file or '缺失'}")
    if not startup_ok:
        ok = False
        print("  [失败] /#SYSTEM 必须明确声明默认页 index.html 与传统目录 toc.hhc，"
              "否则 hh.exe 无法确定打开哪一页、从哪棵目录树导航")
    system_fts_flag = system_fulltext_search_flag(system)
    try:
        windows_stream = read_bytes("/#WINDOWS")
    except (KeyError, OSError):
        windows_stream = b""
    search_tab = windows_search_tab_enabled(windows_stream)
    search_ok, search_failures, search_notes = check_search(
        names, system_fts_flag, search_tab, expect_search)
    print("全文搜索  : " + ("已启用" if fulltext_files else "未启用")
          + (f"，/#SYSTEM 全文搜索标志 {'已置位' if system_fts_flag else '未置位'}"
             if system_fts_flag is not None else "，/#SYSTEM 未声明编译信息记录"))
    print("导航窗格  : " + ({True: "窗口定义已开启'搜索'页签",
                            False: "窗口定义未开启'搜索'页签",
                            None: "无窗口定义（hh.exe 用默认窗口，只有目录）"}[search_tab]))
    for note in search_notes:
        print(f"  [提示] {note}")
    if not search_ok:
        ok = False
        for failure in search_failures:
            print(f"  [失败] {failure}")
    missing_binary = sorted(set(BINARY_TOC) - set(binary))
    binary_flag = 11 in records
    if not hhc:
        ok = False
        print("  [失败] 缺少 toc.hhc，第三方阅读器可能无法显示正确层级")
    elif not missing_binary and binary_flag:
        print("  [通过] toc.hhc 与 Windows 原生二进制目录均完整")
    elif missing_binary and not binary_flag:
        # --toc-mode hhc：只写传统目录，/#SYSTEM 也未声称有二进制目录，属自洽形态
        print("  [提示] 无二进制目录（--toc-mode hhc）：Windows hh.exe 左侧导航为空，"
              "第三方阅读器仍按 toc.hhc 显示层级")
    else:
        ok = False
        print(f"  [失败] Windows 原生目录不完整：缺少 {missing_binary or '无'}，"
              f"/#SYSTEM 二进制目录标志 {'存在' if binary_flag else '缺失'}")
        print("  [失败] 这种不一致会让 Windows hh.exe 的目录导航失效")

    if hhc and (root or readable):
        text = read_bytes(hhc[0]).decode("gbk", "replace")
        top, total, max_depth = tree_stats(text)
        print(f"目录树    : 顶层章节 {top} 个，节点 {total} 个，最大层级 {max_depth} 级")
        locals_ = [v.split("#")[0] for v in LOCAL_RE.findall(text)]
        missing = sorted({v for v in locals_ if v and "/" + v not in reader.files})
        print(f"指向页面  : {len(locals_)} 个链接，缺失文件 {len(missing)} 个")
        if missing:
            ok = False
            print("  [失败] 目录指向的页面不在 CHM 内：", missing[:5])

    html_files = [n for n in names if n.endswith(".html")]
    if root or readable:
        bom = [n for n in html_files if read_bytes(n).startswith(UTF8_BOM)]
        print(f"正文编码  : {len(bom)}/{len(html_files)} 个 HTML 带 UTF-8 BOM"
              + ("（阅读器按默认编码打开即可正确显示）"
                 if html_files and len(bom) == len(html_files) else ""))
        if html_files and len(bom) < len(html_files):
            print("  [提示] 缺 BOM 的页面会被阅读器按系统 ANSI 解码（中文乱码），"
                  "建议重新构建时保持 --utf8-bom（默认开启）")

        topic_names = [n for n in html_files if n not in ("/index.html", "/license.html")]
        bad_topic_names = [n for n in topic_names if not TOPIC_NAME_RE.match(n)]
        print(f"主题文件名: {len(topic_names) - len(bad_topic_names)}/{len(topic_names)} 个为短 ASCII 哈希名")
        if bad_topic_names:
            ok = False
            print("  [失败] 非兼容主题文件名：", bad_topic_names[:5])

        media_exts = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".bmp", ".tif", ".tiff")
        media_files = [n for n in names if n.lower().endswith(media_exts)]
        bad_media_names = [n for n in media_files if not ASSET_NAME_RE.match(n)]
        print(f"图片文件名: {len(media_files) - len(bad_media_names)}/{len(media_files)} 个为短 ASCII 哈希名")
        if bad_media_names:
            ok = False
            print("  [失败] 非兼容图片文件名：", bad_media_names[:5])

        shortcode_pages = []
        nav_pages = []
        remote_asset_pages = []
        ordered_total = ordered_typed = image_tags = 0
        missing_refs: set[str] = set()
        missing_anchors: set[str] = set()
        content_names = set(names)
        html_texts = {
            name: read_bytes(name).decode("utf-8-sig", "replace")
            for name in html_files
        }
        anchors = {
            name: set(re.findall(r'\b(?:id|name)=["\']([^"\']+)["\']', text, re.I))
            for name, text in html_texts.items()
        }
        for name in html_files:
            text = html_texts[name]
            if re.search(r"\{\{<\s*/?copyable\b", text, re.I):
                shortcode_pages.append(name)
            if re.search(r'class=["\'][^"\']*\b(?:nav|toc)\b[^"\']*["\']', text, re.I):
                nav_pages.append(name)
            if REMOTE_ASSET_RE.search(text):
                remote_asset_pages.append(name)
            ordered_total += len(re.findall(r"<ol\b", text, re.I))
            ordered_typed += len(re.findall(r'<ol\b[^>]*\btype=["\'][1ai]["\']', text, re.I))
            image_tags += len(re.findall(r"<img\b", text, re.I))
            for raw_ref in ATTR_RE.findall(text):
                raw_ref = unquote(raw_ref)
                if (not raw_ref or raw_ref.startswith("//")
                        or re.match(r"^[a-z][a-z0-9+.-]*:", raw_ref, re.I)):
                    continue
                ref, _, fragment = raw_ref.partition("#")
                ref = ref.split("?", 1)[0]
                if not ref:
                    target = name
                else:
                    target = "/" + posixpath.normpath(
                        posixpath.join(posixpath.dirname(name.lstrip("/")), ref.lstrip("/"))
                    ).lstrip("/")
                if target not in content_names:
                    missing_refs.add(f"{name} -> {target}")
                elif fragment and target in anchors and fragment not in anchors[target]:
                    missing_anchors.add(f"{name} -> {target}#{fragment}")
        print(f"正文清理  : 模板残留 {len(shortcode_pages)} 页，页首导航 {len(nav_pages)} 页，"
              f"远程显示资源 {len(remote_asset_pages)} 页")
        print(f"列表层级  : {ordered_typed}/{ordered_total} 个有序列表带明确类型")
        print(f"离线资源  : 图片标签 {image_tags} 个，本地引用缺失 {len(missing_refs)} 个，"
              f"锚点缺失 {len(missing_anchors)} 个")
        if (shortcode_pages or nav_pages or remote_asset_pages
                or ordered_typed != ordered_total or missing_refs or missing_anchors):
            ok = False
            if shortcode_pages:
                print("  [失败] 模板标记残留：", shortcode_pages[:5])
            if nav_pages:
                print("  [失败] 文章含重复页首导航：", nav_pages[:5])
            if remote_asset_pages:
                print("  [失败] 页面显示依赖网络资源：", remote_asset_pages[:5])
            if ordered_typed != ordered_total:
                print("  [失败] 有序列表未全部写入层级类型")
            if missing_refs:
                print("  [失败] 本地链接或资源缺失：", sorted(missing_refs)[:5])
            if missing_anchors:
                print("  [失败] 本地锚点缺失：", sorted(missing_anchors)[:5])
    else:
        print("正文编码  : 跳过（压缩内容未解包）")

    print("结论      : " + ("直接打开、离线资源、目录与版式规则全部通过" if ok
                            else "存在兼容性、离线资源、目录或版式问题，见上方 [失败] 项"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
