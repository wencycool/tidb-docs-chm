#!/usr/bin/env python3
"""
build_chm.py —— 把 pingcap/docs-cn 的 Markdown 文档打包成 CHM。

设计目标：
  1. 体积小：默认剔除 media 图片与全部视频嵌入，去掉 Hugo 短代码与站内冗余脚本
  2. 无视频：删除 <iframe> / <video> / YouTube、Bilibili 等嵌入及其引导句
  3. 优雅：统一 CSS（按 hh.exe 的 IE 渲染引擎能力编写，不使用 flex/grid/var）
  4. 可复现：输出最小 .hhp/.hhc + HTML，可在 Windows 上重新编译
  5. 兼容：短 ASCII 内容名、单一传统目录、无自定义窗口
  6. Windows 原生 "搜索"页签：--search 控制 CHM Full Text Search（chmcmd 生成）

用法：
    python3 build_chm.py --repo ../src/docs --out ../out --sections "Get Started,Deploy"

带 Windows"搜索"页签的正式构建：
    python3 build_chm.py --repo ../src/docs --out ../out --all \
        --compiler chmcmd --search fulltext
"""

from __future__ import annotations

import argparse
import hashlib
import html as html_lib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import markdown  # noqa: E402

from chmwriter import (  # noqa: E402
    ChmWriter,
    TocNode,
    detect_full_text_search_entries,
    detect_keyword_index_entries,
    detect_search_related_entries,
    system_default_font,
    system_fulltext_search_flag,
    windows_search_tab_enabled,
)

MD_EXTENSIONS = ["extra", "sane_lists", "toc", "attr_list", "md_in_html"]

CALLOUT_KINDS = ("Note", "Tip", "Warning", "Caution", "Important", "Note ")

# 正文 HTML 统一带 UTF-8 BOM。hh.exe/第三方阅读器默认按系统 ANSI（中文=
# CP936）解码，只声明 <meta charset> 不够；带 BOM 时它们会优先按 UTF-8 解码，
# 打开即正常显示中文，不必手动切换"文本编码"。
UTF8_BOM = b"\xef\xbb\xbf"

# --------------------------------------------------------------------------
# 字号与 Windows 导航字体
#
# 正文和左侧导航必须分开处理：
#   * 正文是 HTML，由 style.css 的 body 基准字号 + em 相对字号控制；
#   * 左侧 Contents/Search 导航是 Windows 原生控件，CSS 管不到它，
#     由 CHM 的 Default Font（.hhp 的 `Default Font=` 与 /#SYSTEM 记录 16）
#     给定"字体名,点数,字符集"，剩下的缩放交给 Windows DPI。
# 不做"按窗口宽度动态缩放字体"：CHM 里跑的是 MSHTML，按 DPI 放大才是正解。
# --------------------------------------------------------------------------

DEFAULT_BODY_FONT_SIZE = 15
DEFAULT_NAV_FONT_SIZE = 10
BODY_FONT_SIZE_RANGE = (12, 20)
NAV_FONT_SIZE_RANGE = (8, 14)

# Windows 二进制目录需要的五个系统流（--toc-mode binary 时校验其齐全）
BINARY_TOC_STREAMS = {"/#TOCIDX", "/#TOPICS", "/#STRINGS", "/#URLTBL", "/#URLSTR"}

# --------------------------------------------------------------------------
# Windows 查看器窗口定义（.hhp 的 [WINDOWS] 段）
#
# hh.exe 左侧导航窗格有哪些页签不是由 CHM 里"有什么"决定的，而是由窗口定义
# （CHM 内的 /#WINDOWS 流，来自 .hhp 的 [WINDOWS]）里的 fsWinProperties 位决定：
# 只有 HHWIN_PROP_TAB_SEARCH(0x400) 置位才会出现"搜索"页签——即使 CHM 里
# 已经有 /$FIftiMain 全文搜索库。没有窗口定义时，查看器退回到内置默认窗口
# （HHWIN_PROP_TRI_PANE，仅目录），这就是"有索引却没搜索页签"的成因。
# 位值取自 Microsoft HTML Help SDK 的 htmlhelp.h（Wine 的 hhctrl.ocx
# 重实现里同样按 HHWIN_PROP_TAB_SEARCH 创建搜索页签）。
# --------------------------------------------------------------------------

DEFAULT_WINDOW_NAME = "main"

# 三窗格 + 自动同步 + 收藏 + 可改标题 + 增强搜索（不含"搜索"页签）
WINDOW_NAV_STYLE_PLAIN = 0x63120
# HTML Help Workshop 的默认值：在上面基础上加"搜索"页签（0x400）
WINDOW_NAV_STYLE_SEARCH = 0x63520
# 工具栏按钮：折叠展开、后退、前进、主页、同步、选项、打印
WINDOW_TOOLBAR_BUTTONS = 0x384E

# --------------------------------------------------------------------------
# 目录树
# --------------------------------------------------------------------------

@dataclass
class Doc:
    path: str            # 仓库内路径，如 br/backup-and-restore-overview.md
    title: str = ""
    summary: str = ""
    html_name: str = ""  # CHM 内路径，如 br/backup-and-restore-overview.html


@dataclass
class TocEntry:
    title: str
    path: str = ""
    children: list = field(default_factory=list)


# --------------------------------------------------------------------------
# 读取
# --------------------------------------------------------------------------

def git_read(repo: str, path: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", repo, "show", f"HEAD:{path}"],
            capture_output=True,
            check=True,
        )
        return out.stdout.decode("utf-8", "replace")
    except subprocess.CalledProcessError:
        return None


def git_exists(repo: str, path: str) -> bool:
    res = subprocess.run(
        ["git", "-C", repo, "cat-file", "-e", f"HEAD:{path}"],
        capture_output=True,
    )
    return res.returncode == 0


def git_file_set(repo: str) -> set[str]:
    """一次性取回仓库全部路径，避免逐文件 fork git。"""
    out = subprocess.run(
        ["git", "-C", repo, "ls-tree", "-r", "--name-only", "HEAD"],
        capture_output=True,
        check=True,
    )
    return set(out.stdout.decode("utf-8", "replace").splitlines())


def git_read_many_raw(repo: str, paths: list[str]) -> dict[str, bytes]:
    """用单次 git cat-file --batch 批量读取文件原始字节。"""
    if not paths:
        return {}
    query = "".join(f"HEAD:{p}\n" for p in paths).encode()
    proc = subprocess.run(
        ["git", "-C", repo, "cat-file", "--batch"],
        input=query,
        capture_output=True,
    )
    data = proc.stdout
    result: dict[str, bytes] = {}
    pos = 0
    for path in paths:
        nl = data.find(b"\n", pos)
        if nl < 0:
            break
        parts = data[pos:nl].decode("utf-8", "replace").split()
        if len(parts) < 3 or parts[1] != "blob":
            pos = nl + 1
            continue
        size = int(parts[2])
        start = nl + 1
        result[path] = data[start:start + size]
        pos = start + size + 1  # 末尾还有一个换行
    return result


def git_read_many(repo: str, paths: list[str]) -> dict[str, str]:
    return {
        p: b.decode("utf-8", "replace")
        for p, b in git_read_many_raw(repo, paths).items()
    }


# --------------------------------------------------------------------------
# 清洗
# --------------------------------------------------------------------------

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.S)
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
IFRAME_RE = re.compile(r"<\s*(iframe|video|source)\b.*?<\s*/\s*\1\s*>", re.S | re.I)
IFRAME_VOID_RE = re.compile(r"<\s*(iframe|video|source)\b[^>]*/?\s*>", re.I)
VIDEO_LEAD_RE = re.compile(
    r"^\s*(?:The following video[^.\n]*\.|Watch the following video[^.\n]*\.|"
    r"下列视频[^。\n]*。)\s*$",
    re.M | re.I,
)
SHORTCODE_RE = re.compile(r"\{\{<.*?>\}\}", re.S)
MARKDOWN_TOC_RE = re.compile(r"^\s*\[TOC\]\s*$", re.M | re.I)
# Hugo 变量：{{{ .company }}} / {{{.tidb-operator-version}}}，取值见仓库 variables.json
# （只处理三花括号形式；`{{fn .Table}}`、`{{ColumnName}}` 等出现在代码/模板示例里，必须原样保留）
TEMPLATE_VAR_RE = re.compile(r"\{\{\{\s*\.([A-Za-z0-9_-]+)\s*\}\}\}")
SIMPLETAB_RE = re.compile(r"</?SimpleTab[^>]*>", re.I)
DIV_LABEL_RE = re.compile(r'<div\s+[^>]*?\blabel="([^"]*)"[^>]*>', re.I)
DETAILS_RE = re.compile(r"<details(\s+markdown=\"1\")?>", re.I)
# 图片的替代文字不能跨行。否则正文里的未闭合字面量（例如 ``/*T![``）
# 会一直吞到后续普通链接的 ``](``，把文章链接误改成 m*.html 图片资源。
IMAGE_RE = re.compile(r"!\[([^\]\n]*)\]\(([^)\s\n]+)(?:[ \t]+\"[^\"\n]*\")?\)")
HTML_IMG_RE = re.compile(r"<img\b[^>]*>", re.I)
HTML_IMG_SRC_RE = re.compile(r"\bsrc\s*=\s*([\"'])([^\"']+)\1", re.I)
HTML_IMG_ALT_RE = re.compile(r"\balt\s*=\s*([\"'])([^\"']*)\1", re.I)
DOCS_DOWNLOAD_IMAGE_RE = re.compile(
    r"https?://docs-download\.pingcap\.com/media/images/docs-cn/(?P<path>[^?#]+)", re.I
)
# 链接文字里允许出现 [ ]（如 [`BATCH [ON COLUMN] LIMIT INTEGER DELETE`](/x.md)）
MD_LINK_RE = re.compile(
    r"(?<!!)\[((?:[^\[\]\n]|\[[^\]\n]*\])*)\]\((/[^)\s]+\.md)(#[^)\s]*)?\)"
)
# 指向 /media/... 的普通链接（少数文档用它代替图片语法）
MEDIA_LINK_RE = re.compile(
    r"(?<!!)\[((?:[^\[\]\n]|\[[^\]\n]*\])*)\]\((/media/[^)\s]+)\)"
)
# 官网 TiDB 自管理文档链接：命中仓库内文档就改成本地页面，其它站点保持外链
PINGCAP_DOC_RE = re.compile(
    r"https?://docs\.pingcap\.com/(?:zh/)?tidb/"
    r"(?P<version>stable|dev|v\d+\.\d+(?:\.\d+)?)/"
    r"(?P<path>[^)#?\s]+)(?P<anchor>#[^)\s\"'<>]*)?",
    re.I,
)


def html_name_for_doc(path: str) -> str:
    """Return a short ASCII-only topic name for reliable Windows CHM URLs."""
    normalized = path.lstrip("/").replace("\\", "/")
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:16]
    return f"p{digest}.html"


def local_asset_name(path: str) -> str:
    """Return a short ASCII-only name while retaining the media extension."""
    normalized = path.lstrip("/").replace("\\", "/")
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:16]
    ext = os.path.splitext(normalized)[1].lower()
    return f"m{digest}{ext}"


def normalize_anchor(anchor: str) -> str:
    """Keep one URL fragment; malformed source links sometimes repeat it."""
    if not anchor:
        return ""
    fragment = anchor.lstrip("#").split("#", 1)[0]
    return f"#{fragment}" if fragment else ""


def resolve_build_epoch(repo: str) -> int | None:
    """构建时刻（秒）：优先 SOURCE_DATE_EPOCH，其次文档源码 HEAD 的提交时间。

    这个值同时用于页脚日期和 `/#SYSTEM` 记录 10 的时间戳，
    刻意不用"当前时间"：同一份源码必须能构建出字节一致的 CHM，
    否则模块开头"可复现"这条设计目标就不成立。
    """
    epoch = os.environ.get("SOURCE_DATE_EPOCH", "").strip()
    if epoch.isdigit():
        return int(epoch)
    res = subprocess.run(["git", "-C", repo, "log", "-1", "--format=%ct"],
                         capture_output=True, text=True)
    stamp = res.stdout.strip()
    if res.returncode == 0 and stamp.isdigit():
        return int(stamp)
    return None


def resolve_build_date(repo: str) -> str:
    """页脚日期（ISO）：由 resolve_build_epoch 换算，缺失时退回今天。"""
    epoch = resolve_build_epoch(repo)
    return date.fromtimestamp(epoch).isoformat() if epoch is not None else date.today().isoformat()


def split_frontmatter(text: str) -> tuple[dict, str]:
    meta: dict[str, str] = {}
    m = FRONTMATTER_RE.match(text)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip().strip("'\"")
        text = text[m.end():]
    return meta, text


def strip_videos(text: str, stats: dict) -> str:
    """删除 iframe/video 嵌入及其引导句。"""
    before = text
    text = IFRAME_RE.sub("", text)
    text = IFRAME_VOID_RE.sub("", text)
    text = VIDEO_LEAD_RE.sub("", text)
    # 仅指向视频站点的链接行
    text = re.sub(
        r"^\s*\[[^\]]*\]\((https?://(?:www\.)?(?:youtube\.com|youtu\.be|bilibili\.com|"
        r"player\.bilibili\.com|vimeo\.com)[^)]*)\)\s*$",
        "",
        text,
        flags=re.M | re.I,
    )
    if text != before:
        stats["videos"] += len(IFRAME_RE.findall(before)) + len(IFRAME_VOID_RE.findall(before))
    return text


FENCE_LINE_RE = re.compile(
    r"^(?P<lead>[ \t]*)(?P<quote>(?:>[ \t]?)*)(?P<indent>[ \t]*)"
    r"(?P<mark>`{3,}|~{3,})[ \t]*(?P<lang>[A-Za-z0-9_+#.-]*).*$"
)

# 代码块占位符。Python-Markdown 只把「行首缩进 < 4 空格」的原始 HTML 当块处理，
# 而列表项里的代码块必然缩进 ≥ 4 空格（引用块里的还要带 `> ` 前缀），直接把
# `<pre><code>` 写进正文会被当成段落文字再解析一遍：`# 注释` 变成标题、代码被
# 拆成好几段。所以这里只放占位符，等 Markdown 转换完再整体换回真正的 HTML。
CODE_TOKEN_FMT = "%%CHM-CODE-{}%%"
CODE_TOKEN_RE = re.compile(r"%%CHM-CODE-(\d+)%%")


def convert_fences(text: str, stats: dict, code_blocks: list[str]) -> str:
    """把围栏代码块换成占位符，渲染好的 `<pre><code>` 追加进 code_blocks。

    Python-Markdown 的 fenced_code 不认列表项内缩进 4 空格的围栏（```` ```shell ````
    会被当成行内 code，``` 直接出现在正文里），所以自己扫描围栏；换成占位符后
    代码内容也不会被后面的短代码/变量/链接替换规则误改。
    """
    lines = text.split("\n")
    out: list[str] = []
    count = 0
    i = 0
    while i < len(lines):
        m = FENCE_LINE_RE.match(lines[i])
        if not m:
            out.append(lines[i])
            i += 1
            continue
        lead, quote, indent, mark, lang = (m.group("lead"), m.group("quote"),
                                           m.group("indent"), m.group("mark"),
                                           m.group("lang"))
        closing = re.compile(
            "^" + re.escape(lead) + re.escape(quote) + r"[ \t]*" + re.escape(mark[0])
            + "{" + str(len(mark)) + r",}[ \t]*$"
        )
        body: list[str] = []
        j = i + 1
        while j < len(lines) and not closing.match(lines[j]):
            body.append(lines[j])
            j += 1
        if j >= len(lines):  # 没有闭合围栏：保持原样
            out.append(lines[i])
            i += 1
            continue
        dedented = []
        for ln in body:
            for prefix in (lead, quote, indent):
                if prefix and ln.startswith(prefix):
                    ln = ln[len(prefix):]
            dedented.append(ln)
        code = html_lib.escape("\n".join(dedented))
        cls = f' class="language-{lang}"' if lang else ""
        code_blocks.append(f"<pre><code{cls}>{code}</code></pre>")
        out.append(f"{lead}{quote}{indent}{CODE_TOKEN_FMT.format(len(code_blocks) - 1)}")
        count += 1
        i = j + 1
    stats["code_blocks"] = stats.get("code_blocks", 0) + count
    return "\n".join(out)


def apply_template_vars(text: str, variables: dict[str, str], stats: dict) -> str:
    """替换 Hugo 变量 {{{ .key }}}；仓库里没有的键直接去掉标记，避免正文露出 {{{ ... }}}。"""
    def sub(m: re.Match) -> str:
        key = m.group(1)
        if key in variables:
            stats["vars"] += 1
            return variables[key]
        stats["vars_unknown"][key] = stats["vars_unknown"].get(key, 0) + 1
        return ""

    return TEMPLATE_VAR_RE.sub(sub, text)


def build_link_index(doc_paths: list[str], raw_map: dict[str, str]) -> dict[str, str]:
    """建立 slug -> 仓库内 md 路径的索引，用于把官网链接改成本地页面。

    slug 取文件名（官网 URL 用的是同一套短名），并补充 front matter 里的 aliases。
    同名冲突时优先取层级更浅的那个（更接近官网的稳定链接）。
    """
    index: dict[str, str] = {}
    for path in sorted(doc_paths, key=lambda p: (p.count("/"), p)):
        index.setdefault(os.path.basename(path)[:-3], path)
    for path, text in raw_map.items():
        m = FRONTMATTER_RE.match(text)
        if not m:
            continue
        for alias in re.findall(r"['\"](/[^'\"]+)['\"]", m.group(1)):
            slug = alias.rstrip("/").split("/")[-1]
            if slug and slug not in index:
                index[slug] = path
    return index


def rewrite_links(text: str, keep_images: bool, stats: dict,
                  link_index: dict[str, str] | None = None,
                  included: set[str] | None = None,
                  web_prefix: str = "",
                  available_files: set[str] | None = None) -> str:
    """链接改写：

    - 指向**本 CHM 内**页面的 .md 链接 -> 本地 .html
    - 指向**未收录**页面的 .md 链接 -> 官网地址（离线时可自行判断去官网看）
    - 官网自管理文档链接若目标在本 CHM 内 -> 本地页面；否则保持外链
    - 图片按策略保留或省略
    """
    def link_sub(m: re.Match) -> str:
        label, target = m.group(1), m.group(2)
        anchor = normalize_anchor(m.group(3) or "")
        path = target.lstrip("/")
        if included is None or path in included:
            return f"[{label}]({html_name_for_doc(path)}{anchor})"
        # 该页面没有打进 CHM：改指官网，避免留下点不开的站内链接
        slug = os.path.basename(path)[:-3]
        stats["md_external"] += 1
        return f"[{label}]({web_prefix}{slug}/{anchor})"

    text = MD_LINK_RE.sub(link_sub, text)

    # [说明](/media/x.png) 这类普通链接：打包图片时保留链接，否则退化成纯文字
    def media_sub(m: re.Match) -> str:
        label, url = m.group(1), m.group(2)
        if keep_images:
            stats["image_paths"].append(url.lstrip("/"))
            return f"[{label}]({local_asset_name(url)})"
        stats["media_links"] += 1
        return label

    text = MEDIA_LINK_RE.sub(media_sub, text)

    # 官网链接指向的文档如果就在本 CHM 里，改成本地页面；否则（Cloud、K8s、
    # GitHub 等本 CHM 没有的内容）原样保留外链。
    if link_index:
        current_match = re.search(r"/(stable|dev|v\d+\.\d+(?:\.\d+)?)/?$", web_prefix, re.I)
        current_version = current_match.group(1).lower() if current_match else ""

        def doc_sub(m: re.Match) -> str:
            target_version = m.group("version").lower()
            # Explicit links to another documentation version can refer to pages or
            # headings absent from this build. Keep those links on the website.
            if not current_version or target_version != current_version:
                stats["ext_kept"] += 1
                return m.group(0)
            # URL 里可能是嵌套路径（如 v8.5/tiproxy/tiproxy-overview/），取末段做 slug
            slug = m.group("path").rstrip("/").split("/")[-1]
            anchor = normalize_anchor(m.group("anchor") or "")
            path = link_index.get(slug)
            if not path:
                stats["ext_kept"] += 1
                return m.group(0)
            stats["ext_localized"] += 1
            return f"{html_name_for_doc(path)}{anchor}"

        text = PINGCAP_DOC_RE.sub(doc_sub, text)

    def image_sub(m: re.Match) -> str:
        alt, url = m.group(1), m.group(2)
        if keep_images:
            stats["image_paths"].append(url.lstrip("/"))
            return f"![{alt}]({local_asset_name(url)})"
        stats["images"] += 1
        label = html_lib.escape(alt or "illustration")
        return f'<p class="img-missing">[图片已省略] {label}</p>'

    text = IMAGE_RE.sub(image_sub, text)

    # 少数新文档直接写远程 <img src="...">，Markdown 图片正则无法处理。
    # 能在 docs-cn/media 中找到同名资源时改成本地短文件名；找不到时显示
    # 离线占位说明，绝不让最终 CHM 的页面显示依赖网络。
    def html_image_sub(m: re.Match) -> str:
        tag = m.group(0)
        src_match = HTML_IMG_SRC_RE.search(tag)
        if not src_match:
            return tag
        src = src_match.group(2)
        path = ""
        if src.startswith("/media/"):
            path = src.lstrip("/").split("?", 1)[0].split("#", 1)[0]
        else:
            remote = DOCS_DOWNLOAD_IMAGE_RE.fullmatch(src)
            if remote:
                path = "media/" + remote.group("path").lstrip("/")
        if (keep_images and path and
                (available_files is None or path in available_files)):
            stats["image_paths"].append(path)
            local = local_asset_name(path)
            return tag[:src_match.start(2)] + local + tag[src_match.end(2):]
        alt_match = HTML_IMG_ALT_RE.search(tag)
        label = html_lib.escape(alt_match.group(2) if alt_match else "illustration")
        stats["images"] += 1
        return f'<p class="img-missing">[图片已省略] {label}</p>'

    text = HTML_IMG_RE.sub(html_image_sub, text)
    return text


def normalize_blocks(text: str) -> str:
    """处理 Hugo shortcode 与 HTML 容器。

    关键点：`<div label="…">` / `<details>` 这类容器里的内容是 Markdown，
    必须加 `markdown="1"` 交给 md_in_html 处理，否则列表、代码块、引用会
    原样输出成一坨（``` 和 > 直接显示在正文里）。
    """
    text = SHORTCODE_RE.sub("", text)
    text = MARKDOWN_TOC_RE.sub("", text)
    text = SIMPLETAB_RE.sub("", text)
    text = DIV_LABEL_RE.sub(
        lambda m: '<div class="tab-pane" markdown="1">'
                  f'<p class="tab-label">{m.group(1)}</p>', text
    )
    text = DETAILS_RE.sub('<details markdown="1">', text)
    text = HTML_COMMENT_RE.sub("", text)
    return text


def clean_markdown(text: str, keep_images: bool, stats: dict,
                   variables: dict[str, str] | None = None,
                   link_index: dict[str, str] | None = None,
                   included: set[str] | None = None,
                   web_prefix: str = "",
                   available_files: set[str] | None = None) -> tuple[dict, str, list[str]]:
    meta, body = split_frontmatter(text)
    code_blocks: list[str] = []
    body = convert_fences(body, stats, code_blocks)
    if variables:
        # 标题/摘要也会展示在页面上，同样要做变量替换
        meta = {k: apply_template_vars(v, variables, stats) if isinstance(v, str) else v
                for k, v in meta.items()}
        body = apply_template_vars(body, variables, stats)
    body = normalize_blocks(body)
    body = strip_videos(body, stats)
    body = rewrite_links(body, keep_images, stats, link_index, included, web_prefix,
                         available_files)
    return meta, body, code_blocks


# --------------------------------------------------------------------------
# 图片压缩（--image-profile）
# --------------------------------------------------------------------------

# 文档里的图以 UI 截图/监控面板为主（PNG 真彩、单张 1~4 MB）。
# PNG 调色板量化 + 降采样对这类图收益最大，且文字仍然清晰；
# 照片类（.jpg）按质量重编码。
IMAGE_PROFILES = {
    "original": {"max_width": 0, "png_colors": 0, "jpeg_quality": 0},
    "compact": {"max_width": 1200, "png_colors": 256, "jpeg_quality": 82},
    "tiny": {"max_width": 1000, "png_colors": 128, "jpeg_quality": 78},
}

RASTER_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}


def _shrink_with_sips(data: bytes, ext: str, max_width: int) -> bytes | None:
    """Pillow 不可用时退回 macOS 自带 sips（仅降采样，不做调色板量化）。"""
    import shutil
    import tempfile

    if not shutil.which("sips"):
        return None
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "in" + ext)
        dst = os.path.join(tmp, "out" + ext)
        with open(src, "wb") as fh:
            fh.write(data)
        res = subprocess.run(
            ["sips", "-Z", str(max_width), src, "--out", dst],
            capture_output=True,
        )
        if res.returncode != 0 or not os.path.exists(dst):
            return None
        with open(dst, "rb") as fh:
            return fh.read()


def optimize_images(raw_imgs: dict[str, bytes], opts: dict, stats: dict) -> dict[str, bytes]:
    """按 max_width / png_colors / jpeg_quality 压缩图片，压不小就保留原图。"""
    max_width = opts.get("max_width", 0) or 0
    colors = opts.get("png_colors", 0) or 0
    quality = opts.get("jpeg_quality", 0) or 0
    stats.update({"images": 0, "before": 0, "after": 0, "skipped": 0})
    if not max_width and not colors and not quality:
        return raw_imgs

    try:
        import io

        from PIL import Image
    except ImportError:
        print("      [提示] 未安装 Pillow，改用 sips 降采样（无法做调色板量化）")
        Image = None  # type: ignore[assignment]

    out: dict[str, bytes] = {}
    for path, data in raw_imgs.items():
        ext = os.path.splitext(path)[1].lower()
        stats["before"] += len(data)
        if ext not in RASTER_EXTS:
            stats["skipped"] += 1
            out[path] = data
            stats["after"] += len(data)
            continue
        new = None
        try:
            if Image is None:
                if max_width:
                    new = _shrink_with_sips(data, ext, max_width)
            else:
                im = Image.open(io.BytesIO(data))
                if max_width and im.width > max_width:
                    height = max(1, round(im.height * max_width / im.width))
                    im = im.resize((max_width, height), Image.LANCZOS)
                buf = io.BytesIO()
                if ext == ".png":
                    if colors:
                        if im.mode in ("RGBA", "LA") or (
                            im.mode == "P" and "transparency" in im.info
                        ):
                            # 调色板化会丢 alpha：先合成到白底（CHM 正文为白底）
                            bg = Image.new("RGB", im.size, (255, 255, 255))
                            rgba = im.convert("RGBA")
                            bg.paste(rgba, mask=rgba.split()[-1])
                            im = bg
                        else:
                            im = im.convert("RGB")
                        im = im.convert("P", palette=Image.ADAPTIVE, colors=colors)
                    im.save(buf, format="PNG", optimize=True)
                elif quality:
                    im.convert("RGB").save(
                        buf, format="JPEG", quality=quality, optimize=True,
                        progressive=True,
                    )
                else:
                    im.save(buf, format=im.format or "PNG", optimize=True)
                new = buf.getvalue()
        except Exception:
            new = None
        if new and len(new) < len(data):
            stats["images"] += 1
            out[path] = new
            stats["after"] += len(new)
        else:
            stats["skipped"] += 1
            out[path] = data
            stats["after"] += len(data)
    return out


# --------------------------------------------------------------------------
# HTML 渲染
# --------------------------------------------------------------------------

CALLOUT_RE = re.compile(
    r"<blockquote>\s*<p><strong>\s*(Note|Tip|Warning|Caution|Important)\s*:?\s*</strong>\s*:?\s*</p>(.*?)</blockquote>",
    re.S | re.I,
)
CALLOUT_INLINE_RE = re.compile(
    r"<blockquote>\s*<p><strong>\s*(Note|Tip|Warning|Caution|Important)\s*:?\s*</strong>\s*:?\s*(.*?)</p>(.*?)</blockquote>",
    re.S | re.I,
)


def render_callouts(html_text: str) -> str:
    def repl(m: re.Match) -> str:
        kind = m.group(1).strip().lower()
        rest = "".join(g for g in m.groups()[1:] if g)
        return (
            f'<div class="callout callout-{kind}">'
            f'<p class="callout-title">{m.group(1).strip().title()}</p>{rest}</div>'
        )

    html_text = CALLOUT_RE.sub(repl, html_text)
    html_text = CALLOUT_INLINE_RE.sub(repl, html_text)
    return html_text


P_BLOCK_RE = re.compile(r"<p>(.*?)</p>", re.S)


def restore_code_blocks(html_text: str, code_blocks: list[str]) -> str:
    """把占位符换回 `<pre><code>` 代码块。

    占位符独立成段时（列表项、引用块里都是这种情况），去掉 Markdown 加上的
    `<p>` 外壳；万一和正文落在同一段里，就按位置拆成「正文段落 + 代码块」。
    """
    if not code_blocks or not CODE_TOKEN_RE.search(html_text):
        return html_text

    def fix_paragraph(m: re.Match) -> str:
        inner = m.group(1)
        if not CODE_TOKEN_RE.search(inner):
            return m.group(0)
        parts = CODE_TOKEN_RE.split(inner)
        out: list[str] = []
        for idx, seg in enumerate(parts):
            if idx % 2:  # 奇数位是占位符里的编号
                out.append(code_blocks[int(seg)])
            elif seg.strip():
                out.append(f"<p>{seg.strip()}</p>")
        return "".join(out)

    html_text = P_BLOCK_RE.sub(fix_paragraph, html_text)
    return CODE_TOKEN_RE.sub(lambda m: code_blocks[int(m.group(1))], html_text)


def md_to_html(md_text: str, code_blocks: list[str] | None = None) -> str:
    html_text = markdown.markdown(
        md_text,
        extensions=MD_EXTENSIONS,
        extension_configs={"toc": {"slugify": docs_heading_slug}},
        output_format="html",
    )
    if code_blocks:
        html_text = restore_code_blocks(html_text, code_blocks)
    return html_text


def docs_heading_slug(value: str, separator: str) -> str:
    """Match the Unicode heading anchors used by docs.pingcap.com.

    Punctuation is removed, Unicode letters and underscores are kept, and each
    whitespace character becomes a hyphen. Keeping consecutive/trailing
    hyphens is required for anchors such as optimizer hint signatures.
    """
    # Python-Markdown passes only the heading's text here (inline markup is
    # already removed), so angle-bracketed parameters are content, not tags.
    value = html_lib.unescape(value).lower()
    value = re.sub(r"[^\w\s-]", "", value, flags=re.UNICODE)
    return re.sub(r"\s", separator, value)


BLOCK_IN_P_RE = re.compile(r"<p>\s*(<pre>.*?</pre>)\s*</p>", re.S | re.I)
NAV_BLOCK_RE = re.compile(
    r'<div\s+class="(?:toc|nav)"[^>]*>.*?</div>', re.S | re.I
)
ORDERED_LIST_TAG_RE = re.compile(r"<ol\b[^>]*>|</ol\s*>", re.I)
TABLE_RE = re.compile(r"(?<!<div class=\"tablewrap\">)(<table\b.*?</table>)", re.S | re.I)
HEADING_DUPLICATE_ID_RE = re.compile(
    r'(<h[1-6]\b[^>]*\bid=["\'])([^"\']+?)_(\d+)(["\'])', re.I
)


def unwrap_block_in_p(html_text: str) -> str:
    """去掉包裹 <pre> 的 <p>（列表项内的代码块会被 Markdown 包进段落，HTML 非法）。"""
    return BLOCK_IN_P_RE.sub(r"\1", html_text)


def apply_ordered_list_types(html_text: str) -> str:
    """Use decimal/alpha/roman markers by nesting depth, preserving ``start``."""
    depth = 0
    types = ("1", "a", "i")

    def replace(m: re.Match) -> str:
        nonlocal depth
        tag = m.group(0)
        if tag.lower().startswith("</"):
            depth = max(0, depth - 1)
            return tag
        list_type = types[depth % len(types)]
        depth += 1
        if re.search(r"\btype\s*=", tag, re.I):
            return re.sub(r'\btype\s*=\s*(["\']).*?\1', f'type="{list_type}"', tag,
                          count=1, flags=re.I)
        return tag[:-1] + f' type="{list_type}">'

    return ORDERED_LIST_TAG_RE.sub(replace, html_text)


def finalize_html(html_text: str) -> str:
    """Apply the offline layout rules after Markdown rendering."""
    html_text = unwrap_block_in_p(html_text)
    html_text = NAV_BLOCK_RE.sub("", html_text)
    # Python-Markdown uses _1 for duplicate IDs; docs.pingcap.com uses -1.
    html_text = HEADING_DUPLICATE_ID_RE.sub(r"\1\2-\3\4", html_text)
    html_text = apply_ordered_list_types(html_text)
    html_text = TABLE_RE.sub(r'<div class="tablewrap">\1</div>', html_text)
    return html_text


CSS = """
/* TiDB Docs CHM — 兼容 Windows hh.exe 的离线版式
   字号策略：html 让 CSS 像素按 1:1 走（不做窗口自适应）；body 的
   font-size:__BODY_FONT_SIZE__px 是唯一基准，标题/代码/表格一律用 em
   相对它表达，以后整体调字号只改基准、比例自动保持。
   Windows 左侧导航树不受这份样式控制，它由 /#SYSTEM 记录 16 的
   Default Font（见 --nav-font-size）决定。
   不使用 flex/grid/var/clamp/vw：hh.exe 内的 MSHTML 可能退回旧文档模式。 */
html{font-size:100%}
body{font-family:"Microsoft YaHei","Segoe UI","PingFang SC",Arial,sans-serif;
 font-size:__BODY_FONT_SIZE__px;line-height:1.70;color:#27364a;background:#fff;margin:0}
.page{max-width:1120px;margin:0 auto;padding:22px 32px 44px}
.brand{font-size:.86em;line-height:1.5;color:#697889;border-bottom:3px solid #c83044;
 padding-bottom:8px;letter-spacing:1px}
a{color:#1964a3;text-decoration:none}a:hover{text-decoration:underline}
h1{font-size:1.85em;line-height:1.35;color:#17283f;margin:18px 0 14px}
h2{font-size:1.45em;line-height:1.40;color:#17283f;border-bottom:1px solid #dde5ee;
 margin-top:28px;padding-bottom:6px}
h3{font-size:1.22em;line-height:1.45;margin-top:22px}
h4{font-size:1.08em;line-height:1.50;margin-top:18px}
h1 code,h2 code,h3 code,h4 code{font-size:inherit;font-weight:inherit;color:inherit;
 background:transparent;padding:0}
p,ul,ol{margin:9px 0}ul,ol{padding-left:22px}
ul ul,ul ol,ol ul,ol ol{padding-left:18px}li{margin:2px 0}
ul{list-style-type:disc}ul ul{list-style-type:circle}ul ul ul{list-style-type:square}
ol{list-style-type:decimal}ol ol{list-style-type:lower-alpha}
ol ol ol{list-style-type:lower-roman}ol ol ol ol{list-style-type:decimal}
code,pre{font-family:Consolas,"Courier New",monospace}
code{font-size:.94em;background:#eff3f7;padding:2px 5px}
pre{font-size:.92em;background:#f4f7fb;border:1px solid #dce4ef;border-left:3px solid #9cabbf;
 margin:12px 0;padding:12px 14px;overflow:auto;line-height:1.55;white-space:pre}
pre code{font-size:1em;background:none;padding:0;color:#27364a}
.tablewrap{margin:12px 0;overflow:auto}
table{border-collapse:collapse;margin:0;font-size:.94em;line-height:1.50;width:100%}
td,th{border:1px solid #d8e1eb;padding:6px 9px;text-align:left;vertical-align:top}
th{background:#eaf0f7;color:#243750}tr:nth-child(even){background:#f8fafc}
td>ul,th>ul{margin:0;padding-left:18px}td li,th li{margin:0;line-height:1.45}
td>p,th>p{margin:0 0 4px}td>p:last-child,th>p:last-child{margin-bottom:0}
blockquote{background:#f1f6fb;border-left:4px solid #779cc1;
 margin:12px 0;padding:1px 16px;color:#27364a}
.callout{margin:12px 0;padding:10px 14px;border-left:4px solid #0969da;background:#ddf4ff}
.callout-tip{border-left-color:#1a7f37;background:#dafbe1}
.callout-warning,.callout-caution{border-left-color:#9a6700;background:#fff8c5}
.callout-important{border-left-color:#cf222e;background:#ffebe9}
.callout-title{font-weight:600;margin:0 0 4px;color:#0d1117}
.tab-pane{border:1px solid #d8e1eb;border-left:3px solid #1964a3;background:#f7fbff;
 padding:2px 14px 8px;margin:12px 0}
.tab-label{font-weight:600;color:#1964a3;font-size:.8em;margin:8px 0 2px}
.img-missing{display:block;color:#66788a;font-size:.8em;font-style:italic;
 border:1px dashed #b9c5d2;background:#f8fafc;padding:5px 9px;margin:8px 0}
img{max-width:100%;height:auto;border:0}
hr{border:0;border-top:1px solid #dde5ee;margin:28px 0}
.nav,.toc{display:none}
.doc-footer,.footer{border-top:1px solid #dde5ee;margin-top:28px;padding-top:14px;
 font-size:.8em;color:#6d7e92}
/* 封面 */
.cover{padding-top:8px}
.cover h1{border-bottom:0;font-size:2.27em;margin-bottom:6px}
.cover .sub{color:#57606a;font-size:1em;margin:0 0 6px 0}
.cover .stat{color:#8c959f;font-size:.8em;margin:0 0 30px 0}
.chapter{margin:0 0 26px 0}
.chapter h2{margin:0 0 8px 0;padding:0;border-bottom:1px solid #eaeef2;font-size:1.27em}
.chapter ul{list-style:none;padding-left:0;margin:0}
.chapter li{margin:3px 0;font-size:.93em}
.chapter a{color:#0969da}
"""


@dataclass(frozen=True)
class RenderOptions:
    """一次构建的版式取值。

    与 BuildFeatures（能力开关：二进制目录、全文搜索）分开：字体是渲染参数，
    不是 capability，混在一起会让能力层失去意义。
    """

    body_font_size: int
    nav_font_size: int
    nav_default_font: str


def default_chm_font(lang: str, nav_font_size: int = DEFAULT_NAV_FONT_SIZE) -> str:
    """CHM 导航窗格的 Default Font，格式 `字体名,点数,字符集`。

    中文用 Microsoft YaHei + 134(GB2312/CP936)，英文用 Segoe UI + 0。
    第三段在不同 HTML Help 编译器里历史上有 charset/flags 差异，最终以
    Windows 实机为准（见 docs/windows-font-dpi.md）。
    """
    if lang == "zh":
        return f"Microsoft YaHei,{nav_font_size},134"
    return f"Segoe UI,{nav_font_size},0"


def resolve_render_options(lang: str, body_font_size: int,
                           nav_font_size: int) -> RenderOptions:
    """校验并组装版式参数；超范围抛 ValueError，由 main() 转成构建失败。"""
    low, high = BODY_FONT_SIZE_RANGE
    if not low <= body_font_size <= high:
        raise ValueError(f"--body-font-size 允许范围 {low}~{high}")
    low, high = NAV_FONT_SIZE_RANGE
    if not low <= nav_font_size <= high:
        raise ValueError(f"--nav-font-size 允许范围 {low}~{high}")
    return RenderOptions(body_font_size=body_font_size,
                         nav_font_size=nav_font_size,
                         nav_default_font=default_chm_font(lang, nav_font_size))


def build_css(body_font_size: int = DEFAULT_BODY_FONT_SIZE) -> str:
    """生成正文样式表；唯一基准是 body 字号，其余字号都是相对单位。"""
    # 用 replace 而不是 .format()：样式表里有大量 {} 会被当成占位符。
    return CSS.replace("__BODY_FONT_SIZE__", str(body_font_size))

PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="{lang}">
<head>
<meta http-equiv="Content-Type" content="text/html; charset=utf-8">
<meta http-equiv="X-UA-Compatible" content="IE=edge">
<title>{title}</title>
<link rel="stylesheet" type="text/css" href="{css}">
</head>
<body>
<div class="page">
<div class="brand">TiDB{version_label} ・ 中文离线文档</div>
{body}
<div class="footer">来源：PingCAP docs-cn ・ {source_ref} ・ 离线整理日期：{build_date}<br>
保留原文内容；调整离线排版、链接及图片。<a href="license.html">CC BY-SA 3.0 / 许可与说明</a></div>
</div>
</body>
</html>
"""


def wrap_page(title: str, body: str, css: str = "style.css",
              lang: str = "en", source_ref: str = "", build_date: str = "") -> str:
    version = source_ref[len("release-"):] if source_ref.startswith("release-") else ""
    return PAGE_TEMPLATE.format(
        lang=lang,
        title=html_lib.escape(title),
        css=css,
        body=body,
        version_label=f" / v{html_lib.escape(version)}" if version else "",
        source_ref=html_lib.escape(source_ref or "本地源码"),
        build_date=html_lib.escape(build_date or date.today().isoformat()),
    )


# --------------------------------------------------------------------------
# TOC.md 解析
# --------------------------------------------------------------------------

TOC_ITEM_RE = re.compile(r"^(?P<indent>[ \t]*)[-*+] (?P<content>.+?)\s*$")
TOC_LINK_RE = re.compile(r"^\[(?P<title>[^\]]*)\]\((?P<url>[^)]+)\)$")


def parse_toc_md(text: str) -> list[TocEntry]:
    roots: list[TocEntry] = []
    levels: dict[int, TocEntry] = {}
    for line in text.splitlines():
        if not line.strip() or line.strip().startswith("<!--"):
            continue
        m = TOC_ITEM_RE.match(line)
        if not m:
            continue
        indent = m.group("indent").replace("\t", "  ")
        level = len(indent) // 2
        content = m.group("content")
        lm = TOC_LINK_RE.match(content)
        if lm:
            title, url = lm.group("title"), lm.group("url")
        else:
            title, url = content, ""
        entry = TocEntry(title=title.strip(), path=url.lstrip("/"))
        parent = levels.get(level - 1)
        if parent is not None:
            parent.children.append(entry)
        elif level == 0:
            roots.append(entry)
        else:
            continue
        for k in [k for k in levels if k >= level]:
            del levels[k]
        levels[level] = entry
    return roots


def prune_toc(entries: list[TocEntry], file_set: set[str]) -> list[TocEntry]:
    """去掉不存在的文档与空分组。"""
    kept: list[TocEntry] = []
    for e in entries:
        children = prune_toc(e.children, file_set)
        if e.path.endswith(".md"):
            if e.path in file_set:
                kept.append(TocEntry(e.title, e.path, children))
            elif children:
                kept.append(TocEntry(e.title, "", children))
        elif children:
            kept.append(TocEntry(e.title, "", children))
    return kept


def collect_docs(entries: list[TocEntry]) -> list[str]:
    out: list[str] = []
    for e in entries:
        if e.path:
            out.append(e.path)
        out.extend(collect_docs(e.children))
    return out


# --------------------------------------------------------------------------
# 构建
# --------------------------------------------------------------------------

def build_cover(title: str, entries: list[TocEntry], doc_count: int,
                note: str = "已移除视频与图片资源") -> str:
    parts = [f'<div class="cover"><h1>{html_lib.escape(title)}</h1>',
             '<p class="sub">TiDB Self-Managed 官方文档离线版（CHM）</p>',
             f'<p class="stat">共 {doc_count} 篇文档 &middot; {note}</p>']
    for chapter in entries:
        parts.append('<div class="chapter">')
        parts.append(f"<h2>{html_lib.escape(chapter.title)}</h2>")
        parts.append("<ul>")
        for item in walk_leaf_items(chapter):
            if item.path:
                parts.append(
                    f'<li><a href="{html_lib.escape(html_name_for_doc(item.path))}">'
                    f"{html_lib.escape(item.title)}</a></li>"
                )
        parts.append("</ul></div>")
    parts.append("</div>")
    return "\n".join(parts)


def build_preview(title: str, entries: list[TocEntry], doc_count: int, chm_name: str,
                  nav_font_size: int = DEFAULT_NAV_FONT_SIZE) -> str:
    """模拟 CHM 阅读器窗口的预览页，用于在 macOS 上直观看效果。

    侧栏字号用 pt 而不是写死 px：Windows 导航树的实际字号来自 Default Font
    的"点数"，预览页跟着用 pt 才能反映真实观感。
    """
    import json

    tree = []
    for e in entries:
        tree.append(_toc_to_dict(e))
    payload = json.dumps(tree, ensure_ascii=False)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta http-equiv="Content-Type" content="text/html; charset=utf-8">
<title>{html_lib.escape(title)} — CHM 效果预览</title>
<style>
*{{box-sizing:border-box}}
body{{margin:0;font-family:"Segoe UI","Microsoft YaHei",Tahoma,Arial,sans-serif;
 background:#e8eaed;color:#24292f}}
.window{{width:100%;height:100vh;display:table;border:1px solid #b8bec6;background:#fff}}
.toolbar{{background:#f1f3f5;border-bottom:1px solid #c8ced6;padding:7px 12px;font-size:13px;
 color:#495057;display:table-caption}}
.toolbar b{{color:#0d1117}}
.toolbar .tabs{{float:right}}
.toolbar .tabs span{{padding:3px 12px;border:1px solid #c8ced6;border-bottom:none;
 background:#e9ecef;color:#6c757d;font-size:12px}}
.toolbar .tabs span.on{{background:#fff;color:#0d1117;font-weight:600}}
.body{{display:table-row;height:100%}}
.side{{width:302px;border-right:1px solid #c8ced6;background:#fbfcfd;overflow:auto;
 padding:10px 6px;font-size:{nav_font_size}pt;display:table-cell;vertical-align:top}}
.side ul{{list-style:none;margin:0;padding-left:15px}}
.side .grp{{font-weight:600;color:#0d1117;cursor:pointer;padding:3px 4px;
 border-radius:2px;user-select:none}}
.side .grp:hover{{background:#eef2f6}}
.side li{{margin:1px 0}}
.side a{{color:#1f6feb;text-decoration:none;display:block;padding:3px 4px;border-radius:2px}}
.side a:hover{{background:#e7f1ff;text-decoration:none}}
.side a.cur{{background:#1f6feb;color:#fff}}
.side .arrow{{display:inline-block;width:12px;color:#8c959f;font-size:10px}}
.main{{display:table-cell;height:100%;vertical-align:top;background:#fff}}
iframe{{width:100%;height:100%;border:0;background:#fff}}
</style>
</head>
<body>
<div class="window">
  <div class="toolbar"><b>{html_lib.escape(title)}</b> &nbsp;·&nbsp; {doc_count} 篇 &nbsp;·&nbsp;
    {html_lib.escape(chm_name)}
    <span class="tabs"><span class="on">目录</span><span>索引</span><span>搜索</span></span>
  </div>
  <div class="body">
    <div class="side"><ul id="tree"></ul></div>
    <div class="main"><iframe id="cv" src="index.html"></iframe></div>
  </div>
</div>
<script>
var DATA = {payload};
function build(items, parent, depth) {{
  items.forEach(function (it) {{
    var li = document.createElement('li');
    var url = it.p || '';
    if (it.c && it.c.length) {{
      var grp = document.createElement('div');
      grp.className = 'grp';
      grp.innerHTML = '<span class="arrow">&#9660;</span>' + it.t;
      li.appendChild(grp);
      var ul = document.createElement('ul');
      build(it.c, ul, depth + 1);
      li.appendChild(ul);
      grp.onclick = function () {{
        var hidden = ul.style.display === 'none';
        ul.style.display = hidden ? '' : 'none';
        grp.querySelector('.arrow').innerHTML = hidden ? '&#9660;' : '&#9654;';
      }};
    }} else if (url) {{
      var a = document.createElement('a');
      a.href = '#'; a.textContent = it.t;
      a.onclick = function (ev) {{
        ev.preventDefault();
        document.getElementById('cv').src = url;
        var all = document.querySelectorAll('.side a');
        for (var i = 0; i < all.length; i++) all[i].className = '';
        a.className = 'cur';
      }};
      li.appendChild(a);
    }} else {{
      li.textContent = it.t;
    }}
    parent.appendChild(li);
  }});
}}
build(DATA, document.getElementById('tree'), 0);
</script>
</body>
</html>
"""


def _toc_to_dict(e: TocEntry) -> dict:
    return {"t": e.title, "p": html_name_for_doc(e.path) if e.path else "",
            "c": [_toc_to_dict(c) for c in e.children]}


def walk_leaf_items(entry: TocEntry) -> list[TocEntry]:
    out: list[TocEntry] = []
    if entry.path:
        out.append(entry)
    for c in entry.children:
        out.extend(walk_leaf_items(c))
    return out


def build_hhc(entries: list[TocEntry]) -> str:
    # 与 HHW 生成的 sitemap 保持一致：不含 charset meta，
    # 由调用方按本地代码页（简体中文 Windows = GBK）编码
    lines = [
        '<!DOCTYPE HTML PUBLIC "-//IETF//DTD HTML//EN">',
        "<HTML>",
        "<HEAD>",
        '<meta name="GENERATOR" content="Microsoft HTML Help Workshop 4.1">',
        "<!-- Sitemap 1.0 -->",
        "</HEAD>",
        "<BODY>",
        "<UL>",
    ]

    def walk(items: list[TocEntry]) -> None:
        for it in items:
            local = html_name_for_doc(it.path) if it.path else ""
            lines.append("<LI><OBJECT type=\"text/sitemap\">")
            lines.append(f'<param name="Name" value="{html_lib.escape(it.title, quote=True)}">')
            if local:
                lines.append(f'<param name="Local" value="{html_lib.escape(local, quote=True)}">')
            lines.append("</OBJECT></LI>")
            if it.children:
                lines.append("<UL>")
                walk(it.children)
                lines.append("</UL>")

    walk(entries)
    lines.append("</UL></BODY></HTML>")
    return "\n".join(lines)


def collect_entries(entries: list[TocEntry]) -> list[TocEntry]:
    out = []
    for e in entries:
        out.append(e)
        out.extend(collect_entries(e.children))
    return out


def build_hhp(title: str, chm_name: str, files: list[str], lang: str = "en",
              binary_toc: bool = True, full_text_search: bool = False,
              window_name: str = DEFAULT_WINDOW_NAME,
              default_font: str = "") -> str:
    """生成可直接打开的最小 HTML Help 工程，不创建关键词索引。

    binary_toc=True 时让 hhc.exe/chmcmd 一并写入 Windows 原生二进制目录，
    与两个编译器自己的默认产物一致（`--toc-mode hhc` 可关闭）。

    full_text_search=True 时写 `Full-text search=Yes`，由支持 FTS 的编译器
    （chmcmd）生成 Windows "搜索"页签需要的全文索引库。
    注意这**不是**关键词索引：`Binary Index=No` 与 `.hhk` 都保持关闭，
    否则第三方阅读器会把索引条目平铺进目录树。

    同时总是写一个 `[WINDOWS]` 窗口定义：左侧导航窗格有哪些页签由它决定，
    其中"搜索"页签对应 fsWinProperties 的 HHWIN_PROP_TAB_SEARCH(0x400) 位。
    没有窗口定义时 hh.exe 用内置默认窗口（只有目录），**即使 CHM 里有全文
    搜索库也不会出现"搜索"页签**，所以这个定义不能省。

    default_font 非空时写 `Default Font=`：它决定左侧 Contents/Search 导航树
    的字体与点数（Windows 原生控件，CSS 管不到），格式 `字体名,点数,字符集`。
    """
    # hhp 是 hhc.exe 按 ANSI 读的，Language 行保持纯 ASCII，避免编码问题
    lang_line = "0x0804 Simplified Chinese" if lang == "zh" else "0x0409 English (United States)"
    nav_style = (WINDOW_NAV_STYLE_SEARCH if full_text_search
                 else WINDOW_NAV_STYLE_PLAIN)
    # [WINDOWS] 每行的字段顺序（与 HTML Help Workshop 一致，chmcmd 同样解析）：
    # 标题, 目录, 索引, 默认页, 主页, 跳转按钮 1/2 的文件与文字,
    # 导航窗格样式, 窗格宽度, 工具栏按钮, 窗口矩形, 样式, 扩展样式,
    # 显示状态, 窗格初始关闭, 默认页签, 页签位置, 通知 ID
    window_fields = [
        f'"{title}"', '"toc.hhc"', '""', '"index.html"', '"index.html"',
        "", "", "", "",
        f"0x{nav_style:X}", "", f"0x{WINDOW_TOOLBAR_BUTTONS:X}",
        "", "", "", "", "", "", "", "0",
    ]
    lines = [
        "[OPTIONS]",
        "Compatibility=1.1 or later",
        f"Compiled file={chm_name}",
        "Contents file=toc.hhc",
        "Default topic=index.html",
        f"Default window={window_name}",
        f"Title={title}",
        f"Language={lang_line}",
    ]
    if default_font:
        lines.append(f"Default Font={default_font}")
    lines.extend([
        f"Binary TOC={'Yes' if binary_toc else 'No'}",
        "Binary Index=No",
        f"Full-text search={'Yes' if full_text_search else 'No'}",
        "Create CHI file=No",
        "Display compile progress=No",
        "",
        "[WINDOWS]",
        f"{window_name}=" + ",".join(window_fields),
        "",
        "[FILES]",
    ])
    lines.extend(files)
    lines.extend(["", "[INFOTYPES]", ""])
    return "\n".join(lines)


def choose_compiler(requested: str, chmcmd_available: bool) -> str:
    """Resolve auto to chmcmd when installed, otherwise the builtin writer."""
    if requested == "auto":
        return "chmcmd" if chmcmd_available else "builtin"
    return requested


@dataclass(frozen=True)
class BuildFeatures:
    """一次构建实际启用的 Windows 原生能力。

    目前只有二进制目录和全文搜索；以后加关键词索引、CHI、合并 CHM 时
    继续往这里加字段，主流程不需要再判断 `args.xxx` / `compiler`。
    """

    binary_toc: bool
    full_text_search: bool


def resolve_build_features(compiler: str, search_mode: str, toc_mode: str,
                           has_chmcmd: bool) -> BuildFeatures:
    """把 CLI 取值解析成实际启用的能力，`--search fulltext` 不允许静默降级。

    auto：chmcmd 可用就开启全文搜索，builtin 则关闭（由调用方打印提示）。
    fulltext：必须由 chmcmd 生成，否则抛 RuntimeError，调用方转成构建失败。
    none：保持无搜索。
    """
    binary_toc = toc_mode == "binary"

    if search_mode == "none":
        full_text_search = False

    elif search_mode == "fulltext":
        if compiler == "builtin":
            raise RuntimeError(
                "builtin writer 尚未实现 CHM Full Text Search，"
                "--search fulltext 需要 chmcmd"
            )
        if not has_chmcmd:
            raise RuntimeError(
                "--search fulltext 需要 Free Pascal 的 chmcmd"
                "（macOS: brew install fpc），不能退化为无搜索构建"
            )
        full_text_search = True

    elif search_mode == "auto":
        full_text_search = compiler == "chmcmd" and has_chmcmd

    else:  # pragma: no cover - argparse choices 已经挡住
        raise ValueError(f"未知的 --search 取值：{search_mode}")

    return BuildFeatures(binary_toc=binary_toc, full_text_search=full_text_search)


def to_chm_toc(entries: list[TocEntry]) -> list[TocNode]:
    nodes: list[TocNode] = []
    for e in entries:
        node = TocNode(title=e.title, local=html_name_for_doc(e.path) if e.path else "")
        for c in e.children:
            node.children.extend(to_chm_toc([c]))
        nodes.append(node)
    return nodes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, help="pingcap/docs-cn 的 git 仓库路径")
    ap.add_argument("--out", required=True, help="输出目录")
    ap.add_argument("--title", default="TiDB Documentation")
    ap.add_argument("--chm", default="tidb-docs.chm")
    ap.add_argument("--sections", default="", help="逗号分隔的顶层章节名，留空取前 3 个")
    ap.add_argument("--limit", type=int, default=0, help="最多收录多少篇文档，0 表示不限")
    ap.add_argument("--images", "--keep-images", dest="images", action="store_true",
                    help="包含文档引用的图片资源（默认不包含，体积最小）")
    ap.add_argument("--prune", default="none", choices=["none", "hhp", "chm"],
                    help="打包完成后清理中间产物：none=保留 HTML 版与工程文件（默认）；"
                         "hhp=只留 *.chm + docs.hhp/toc.hhc（工程存档，正文已清理）；"
                         "chm=只留 *.chm")
    ap.add_argument("--compiler", default="auto", choices=["auto", "builtin", "chmcmd"],
                    help="打包器：auto=有 chmcmd 时用 LZX 压缩，否则自动用内置打包器；"
                         "builtin=内置未压缩；chmcmd=强制使用，缺少时报错")
    ap.add_argument("--search", default="auto", choices=["auto", "fulltext", "none"],
                    help="Windows'搜索'页签用的全文搜索：auto=chmcmd 可用时开启，"
                         "builtin 自动关闭并提示；fulltext=强制要求生成，必须用 chmcmd，"
                         "否则构建失败；none=关闭")
    ap.add_argument("--image-profile", default="compact",
                    choices=sorted(IMAGE_PROFILES),
                    help="图片压缩档位：compact=宽≤1200 + PNG 256 色（默认）；"
                         "original=原图；tiny=宽≤1000 + PNG 128 色")
    ap.add_argument("--image-max-width", type=int, default=-1,
                    help="覆盖档位的最大宽度（像素，0=不缩放）")
    ap.add_argument("--image-colors", type=int, default=-1,
                    help="覆盖档位的 PNG 调色板色数（0=保持真彩）")
    ap.add_argument("--image-jpeg-quality", type=int, default=-1,
                    help="覆盖档位的 JPEG 质量（0=不重编码）")
    ap.add_argument("--ref", default="",
                    help="TiDB 版本分支，如 release-8.5 / release-7.1（默认 master 最新）")
    ap.add_argument("--source-ref", default="",
                    help=argparse.SUPPRESS)
    ap.add_argument("--toc-mode", default="binary", choices=["binary", "hhc"],
                    help="目录形态：binary=额外写入 Windows hh.exe 用的二进制目录树"
                         "（默认，与 hhc.exe / chmcmd 自己的产物一致，Windows 侧栏"
                         "才有原生导航）；hhc=只写传统 toc.hhc（Windows 左侧导航为空，"
                         "个别第三方阅读器侧栏更干净）")
    ap.add_argument("--all", action="store_true", help="收录 TOC.md 中的全部章节")
    ap.add_argument("--body-font-size", type=int, default=DEFAULT_BODY_FONT_SIZE,
                    help=f"正文基准字号（px，默认 {DEFAULT_BODY_FONT_SIZE}，"
                         f"允许 {BODY_FONT_SIZE_RANGE[0]}~{BODY_FONT_SIZE_RANGE[1]}）；"
                         "标题/代码/表格是相对它的 em，比例不受影响")
    ap.add_argument("--nav-font-size", type=int, default=DEFAULT_NAV_FONT_SIZE,
                    help=f"Windows CHM 左侧 Contents/Search 导航字号（pt，默认 "
                         f"{DEFAULT_NAV_FONT_SIZE}，允许 {NAV_FONT_SIZE_RANGE[0]}~"
                         f"{NAV_FONT_SIZE_RANGE[1]}）；写进 .hhp 的 Default Font 与 "
                         "/#SYSTEM 记录 16")
    ap.add_argument("--lang", default="zh", choices=["zh", "en"],
                    help="zh: 中文文档（GBK 目录 + 0x0804），en: 英文")
    ap.add_argument("--utf8-bom", dest="utf8_bom", action="store_true", default=True,
                    help="正文 HTML/CSS 写入 UTF-8 BOM（默认开启，"
                         "让阅读器按 UTF-8 解码，打开即不乱码）")
    ap.add_argument("--no-utf8-bom", dest="utf8_bom", action="store_false",
                    help="关闭 BOM（正文改为仅靠 <meta charset> 声明编码）")
    args = ap.parse_args()

    repo = os.path.abspath(args.repo)
    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)
    # 打包器与能力必须在生成 hhp 之前定下来：docs.hhp 和 docs.chmcmd.hhp 的
    # Full-text search 取值都取决于最终用的是哪个打包器，早解析可避免
    # 两份工程文件配置不一致。
    has_chmcmd = shutil.which("chmcmd") is not None
    compiler = choose_compiler(args.compiler, has_chmcmd)
    try:
        features = resolve_build_features(
            compiler=compiler,
            search_mode=args.search,
            toc_mode=args.toc_mode,
            has_chmcmd=has_chmcmd,
        )
    except (RuntimeError, ValueError) as exc:
        print(f"[失败] {exc}", file=sys.stderr)
        return 1
    try:
        render_opts = resolve_render_options(
            lang=args.lang,
            body_font_size=args.body_font_size,
            nav_font_size=args.nav_font_size,
        )
    except ValueError as exc:
        print(f"[失败] {exc}", file=sys.stderr)
        return 1
    want_binary_toc = features.binary_toc
    want_full_text_search = features.full_text_search
    if args.search == "auto" and not want_full_text_search:
        print("      提示：当前使用内置打包器，成品没有 Windows'搜索'页签；"
              "需要搜索请安装 chmcmd（macOS: brew install fpc），"
              "或改用 --compiler chmcmd --search fulltext")
    build_epoch = resolve_build_epoch(repo)
    build_date = resolve_build_date(repo)
    # 清理旧版本构建器留下的启动脚本——只删内容确实是本项目旧产物时才动手，
    # 避免误删输出目录里用户自己的同名文件。
    obsolete_launcher = os.path.join(out, "open-chm.cmd")
    if os.path.isfile(obsolete_launcher):
        with open(obsolete_launcher, "rb") as fh:
            if b"hh.exe" in fh.read(4096).lower():
                os.remove(obsolete_launcher)
                print("      已清理旧版启动脚本 open-chm.cmd")
            else:
                print(f"      保留非本工具生成的 {obsolete_launcher}")

    if args.ref:
        print(f"[0/6] 切换到版本分支 {args.ref}")
        subprocess.run(
            ["git", "-C", repo, "fetch", "--depth", "1", "origin", args.ref],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", repo, "checkout", "--force", "FETCH_HEAD"],
            check=True, capture_output=True,
        )

    print(f"[1/6] 解析 TOC.md  仓库={repo}")
    toc_text = git_read(repo, "TOC.md")
    if not toc_text:
        print("无法读取 TOC.md", file=sys.stderr)
        return 1
    entries = parse_toc_md(toc_text)
    if args.all:
        pass
    elif args.sections:
        wanted = [s.strip() for s in args.sections.split(",") if s.strip()]
        entries = [e for e in entries if e.title in wanted]
    else:
        entries = entries[:3]
    file_set = git_file_set(repo)
    entries = prune_toc(entries, file_set)
    if not entries:
        print("没有匹配到任何章节", file=sys.stderr)
        return 1

    doc_paths: list[str] = []
    for p in collect_docs(entries):
        if p not in doc_paths:
            doc_paths.append(p)
    if args.limit:
        doc_paths = doc_paths[: args.limit]
        allowed = set(doc_paths)
        entries = prune_entries(entries, allowed)
    print(f"      章节 {len(entries)} 个，文档 {len(doc_paths)} 篇")

    print("[2/6] 转换 Markdown -> HTML")
    stats = {"videos": 0, "images": 0, "image_paths": [],
             "vars": 0, "vars_unknown": {}, "ext_localized": 0, "ext_kept": 0,
             "md_external": 0, "code_blocks": 0, "media_links": 0}
    media_note = ("含图片资源（{} 档）".format(args.image_profile) if args.images
                  else "已移除视频与图片资源")
    variables: dict[str, str] = {}
    vars_text = git_read(repo, "variables.json")
    if vars_text:
        try:
            variables = {str(k): str(v) for k, v in json.loads(vars_text).items()}
        except json.JSONDecodeError:
            print("      [警告] variables.json 解析失败，跳过模板变量替换")
    pages: dict[str, bytes] = {}
    raw_map = git_read_many(repo, doc_paths)
    link_index = build_link_index(doc_paths, raw_map)
    included = set(doc_paths)
    source_ref = args.source_ref or args.ref
    web_prefix = ("https://docs.pingcap.com/zh/tidb/" if args.lang == "zh"
                  else "https://docs.pingcap.com/tidb/")
    web_prefix += (f"v{source_ref[len('release-'):]}/" if source_ref.startswith("release-")
                   else "stable/")
    for idx, path in enumerate(doc_paths, 1):
        raw = raw_map.get(path)
        if raw is None:
            continue
        if idx % 200 == 0:
            print(f"      {idx}/{len(doc_paths)}")
        meta, body, code_blocks = clean_markdown(raw, args.images, stats, variables,
                                                 link_index, included, web_prefix,
                                                 file_set)
        html_body = finalize_html(render_callouts(md_to_html(body, code_blocks)))
        title = meta.get("title") or os.path.basename(path)[:-3].replace("-", " ").title()
        page = wrap_page(title, html_body, lang=args.lang,
                         source_ref=source_ref, build_date=build_date)
        pages[html_name_for_doc(path)] = page.encode("utf-8")
    print(f"      移除视频嵌入 {stats['videos']} 处，省略图片 {stats['images']} 张")
    print(f"      模板变量替换 {stats['vars']} 处"
          + (f"，未知变量 {stats['vars_unknown']}" if stats["vars_unknown"] else ""))
    print(f"      官网文档链接改内链 {stats['ext_localized']} 处，"
          f"保留外链 {stats['ext_kept']} 处")
    print(f"      未收录文档的站内链接改指官网 {stats['md_external']} 处，"
          f"代码块转换 {stats['code_blocks']} 个")
    if stats["media_links"]:
        print(f"      未打包图片的裸链接退化为纯文本 {stats['media_links']} 处")

    if args.images:
        img_paths = sorted(set(stats["image_paths"]))
        print(f"      打包引用图片 {len(img_paths)} 张（首建需按需下载，可能较慢）")
        raw_imgs = git_read_many_raw(repo, img_paths)
        got = len(raw_imgs)
        profile = IMAGE_PROFILES[args.image_profile]
        img_opts = {
            "max_width": args.image_max_width if args.image_max_width >= 0
            else profile["max_width"],
            "png_colors": args.image_colors if args.image_colors >= 0
            else profile["png_colors"],
            "jpeg_quality": args.image_jpeg_quality if args.image_jpeg_quality >= 0
            else profile["jpeg_quality"],
        }
        img_stats: dict = {}
        raw_imgs = optimize_images(raw_imgs, img_opts, img_stats)
        for p, data in raw_imgs.items():
            pages[local_asset_name(p)] = data
        print(f"      图片入库 {got}/{len(img_paths)}")
        if img_stats["images"] or img_stats["before"]:
            before = img_stats["before"] / 1048576
            after = img_stats["after"] / 1048576
            ratio = (after / before * 100) if before else 100
            print(f"      图片压缩档 {args.image_profile}"
                  f"（宽≤{img_opts['max_width'] or '原图'}、"
                  f"PNG {img_opts['png_colors'] or '真彩'} 色、"
                  f"JPEG q{img_opts['jpeg_quality'] or '-'}）："
                  f"{before:.1f} MB -> {after:.1f} MB（{ratio:.0f}%，"
                  f"压缩 {img_stats['images']} 张、保留原图 {img_stats['skipped']} 张）")

    print("[3/6] 生成封面与资源")
    print(f"      构建日期 {build_date}（页脚；可用 SOURCE_DATE_EPOCH 覆盖）")
    doc_count = sum(1 for k in pages if k.endswith(".html"))
    cover = build_cover(args.title, entries, doc_count, note=media_note)
    pages["index.html"] = wrap_page(
        args.title, cover, source_ref=source_ref, build_date=build_date
    ).encode("utf-8")
    license_body = """<h1>许可与说明</h1>
<p>本文档内容来源于 PingCAP 官方中文文档仓库 <code>pingcap/docs-cn</code>。</p>
<p>TiDB 文档内容采用 CC BY-SA 3.0 许可；离线版本仅调整排版、链接、媒体资源和 CHM 打包结构。</p>
<p>构建工具代码采用 MIT 许可。详细条款请参见项目仓库中的 <code>LICENSE</code> 文件。</p>"""
    pages["license.html"] = wrap_page("许可与说明", license_body,
                                      lang=args.lang,
                                      source_ref=source_ref,
                                      build_date=build_date).encode("utf-8")
    pages["style.css"] = build_css(render_opts.body_font_size).encode("utf-8")
    pages["preview.html"] = build_preview(
        args.title, entries, len(pages), args.chm, render_opts.nav_font_size
    ).encode("utf-8")

    if args.utf8_bom:
        for name in list(pages):
            if name.endswith((".html", ".css")) and not pages[name].startswith(UTF8_BOM):
                pages[name] = UTF8_BOM + pages[name]

    print("[4/6] 生成直接打开兼容的 hhp / hhc")
    # hhc 由 hh.exe 与 hhc.exe 按 ANSI 代码页解析，中文环境必须 GBK。
    # 只生成传统目录 + 可选的 Windows 二进制目录与全文搜索，
    # 不生成关键词索引（.hhk），避免条目被平铺进目录树。
    pages["toc.hhc"] = build_hhc(entries).encode("gbk")
    file_list = sorted(pages)
    skip_in_chm = {"preview.html"}
    with open(os.path.join(out, "toc.hhc"), "wb") as fh:
        fh.write(pages["toc.hhc"])
    with open(os.path.join(out, "docs.hhp"), "wb") as fh:
        fh.write(
            build_hhp(args.title, args.chm,
                      [f for f in file_list if f not in skip_in_chm], args.lang,
                      binary_toc=want_binary_toc,
                      full_text_search=want_full_text_search,
                      default_font=render_opts.nav_default_font)
            .encode("gbk")
        )
    for name, data in pages.items():
        target = os.path.join(out, name)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as fh:
            fh.write(data)
    print(f"      Windows 二进制目录：{'启用' if want_binary_toc else '关闭'}")
    print("      Windows 全文搜索："
          + ("启用（由 chmcmd 生成）" if want_full_text_search else "关闭"))
    print(f"      正文字号：{render_opts.body_font_size}px（标题/代码/表格按 em 相对缩放）")
    print(f"      Windows 导航字体：{render_opts.nav_default_font}")
    print("[5/6] 打包 CHM")
    chm_path = os.path.join(out, args.chm)
    chm_files = [f for f in file_list if f not in skip_in_chm]
    if args.compiler == "auto" and compiler == "builtin":
        print("      未找到 chmcmd：auto 自动改用内置未压缩打包器")
    if compiler == "chmcmd" and not has_chmcmd:
        print("      找不到 chmcmd（Free Pascal 的 CHM 编译器）："
              "brew install fpc，或改用 --compiler auto / builtin", file=sys.stderr)
        return 1

    chmcmd_hhp = ""
    if compiler == "chmcmd":
        # chmcmd 自带 LZX 压缩实现；按 --toc-mode 决定是否附带二进制目录，
        # 按 --search 决定是否生成 Windows 全文搜索索引
        chmcmd_hhp = "docs.chmcmd.hhp"
        with open(os.path.join(out, chmcmd_hhp), "wb") as fh:
            fh.write(build_hhp(args.title, args.chm, chm_files, args.lang,
                               binary_toc=want_binary_toc,
                               full_text_search=want_full_text_search,
                               default_font=render_opts.nav_default_font).encode("gbk"))
        res = subprocess.run(["chmcmd", "--no-html-scan", chmcmd_hhp],
                             cwd=out, capture_output=True)
        if res.returncode != 0 or not os.path.exists(chm_path):
            print("      chmcmd 编译失败：", res.stderr.decode("utf-8", "replace")[-500:],
                  file=sys.stderr)
            return 1
        print(f"      chmcmd（Free Pascal，LZX 压缩）完成，"
              f"未压缩时约 {sum(len(pages[f]) for f in chm_files) / 1048576:.1f} MB")
    else:
        writer = ChmWriter(
            title=args.title,
            default_page="index.html",
            language_id=0x0804 if args.lang == "zh" else 0x0409,
            toc_name="toc.hhc",
            # 导航树字体：与 chmcmd 的 `Default Font=` 同源，保证两条后端一致
            default_font=render_opts.nav_default_font,
            index_name="",  # 不声明索引：避免阅读器把索引条目平铺进目录树
            # 二进制目录由 --toc-mode 决定（默认 binary）。它给 Windows hh.exe
            # 提供原生左侧导航；toc.hhc 同时保留，供第三方阅读器恢复层级。
            # 注意：Windows 能否打开并不取决于这几个流——hhc.exe 产物（WiX.chm、
            # TiDB 官方 7.5 CHM）就没有 /#TOCIDX，真正的打开兼容性由 ITSF 段序决定。
            include_binary_toc=want_binary_toc,
            build_time=build_epoch,
        )
        for name in chm_files:
            writer.add_file(name, pages[name])
        for node in to_chm_toc(entries):
            writer.add_toc(node)
        writer.write(chm_path)

    print("[6/6] 校验")
    from chmwriter import ChmReader

    reader = ChmReader(chm_path)
    layout_ok = reader.windows_layout_ok()
    print(f"      Windows ITSF 布局校验 {'OK' if layout_ok else '失败'}")
    if not layout_ok:
        print("      [失败] Section 0、ITSP 目录和正文数据的偏移不符合 hh.exe 要求",
              file=sys.stderr)
        return 1
    directory_ok = reader.windows_directory_ok()
    print(f"      Windows PMGL/PMGI 目录块校验 {'OK' if directory_ok else '失败'}")
    if not directory_ok:
        print("      [失败] 目录块 quickref、层级或根索引不符合 hh.exe 要求",
              file=sys.stderr)
        return 1
    expect = {"/" + n for n in file_list if n not in skip_in_chm} | {"/#SYSTEM"}
    binary_toc_required = BINARY_TOC_STREAMS if want_binary_toc else set()
    expect |= binary_toc_required
    present = set(reader.files)
    missing = sorted(expect - present)
    missing_binary = sorted(binary_toc_required - present)
    content_readable = chm_content_readable(reader)
    toc_head = None
    if binary_toc_required and not missing_binary and content_readable:
        toc_head = reader.read("/#TOCIDX")[:4]
    ok_toc, toc_note = binary_toc_check(binary_toc_required, present,
                                        content_readable, toc_head)
    print(f"      Windows 二进制目录校验 {toc_note}")
    print(f"      文件条目 {len(reader.files)} 个，缺失 {len(missing)} 个")
    if missing:
        print("      缺失：", missing[:10])
    if content_readable:
        print(f"      index.html 回读 {len(reader.read('/index.html'))} 字节")
    else:
        # LZX 压缩：用 chmcmd 自带的 chmls 解包，与打包前的源文件逐个字节比对
        extracted, total = verify_compressed_content(chm_path, out, chm_files)
        if extracted is None:
            print("      [提示] 未找到 chmls，跳过压缩内容比对（brew install fpc）")
        else:
            print(f"      解压比对：{extracted}/{total} 个文件与源文件字节一致")
            if extracted != total:
                print("      [失败] 压缩包内容与源文件不一致", file=sys.stderr)
                return 1

    # 关键词索引（.hhk）会污染第三方阅读器侧栏，一律禁止；
    # 全文搜索库（/$FIftiMain）是 Windows"搜索"页签的数据源，按 --search 判定。
    keyword_index_entries = detect_keyword_index_entries(reader.files)
    fulltext_entries = detect_full_text_search_entries(reader.files)
    search_entries = detect_search_related_entries(reader.files)
    try:
        system_blob = read_chm_stream(reader, chm_path, "/#SYSTEM")
    except KeyError:
        system_blob = b""
    system_fts_flag = system_fulltext_search_flag(system_blob)
    system_font = system_default_font(system_blob)
    windows_blob = read_chm_stream(reader, chm_path, "/#WINDOWS")
    search_tab = windows_search_tab_enabled(windows_blob)
    if system_font is None:
        print("      [失败] /#SYSTEM 未声明导航窗格字体（Default Font）")
        return 1
    if system_font != render_opts.nav_default_font:
        print(f"      [失败] 导航窗格字体与构建参数不一致："
              f"{system_font} != {render_opts.nav_default_font}", file=sys.stderr)
        return 1
    print(f"      Windows 导航字体校验 OK：{system_font}")
    hygiene_ok = True
    if keyword_index_entries:
        hygiene_ok = False
        print(f"      [失败] CHM 内混入关键词索引文件：{keyword_index_entries}")
    if want_full_text_search:
        if not fulltext_entries:
            hygiene_ok = False
            print("      [失败] 要求全文搜索，但 CHM 内没有发现全文搜索内部结构")
        elif system_fts_flag is not True:
            hygiene_ok = False
            print("      [失败] /#SYSTEM 未声明全文搜索标志，"
                  "Windows hh.exe 不会显示'搜索'页签")
        elif search_tab is not True:
            hygiene_ok = False
            print("      [失败] 窗口定义未开启'搜索'页签（HHWIN_PROP_TAB_SEARCH），"
                  "hh.exe 左侧不会出现'搜索'页签")
        else:
            print("      Windows 全文搜索索引校验 OK："
                  f"{len(fulltext_entries)} 个内部条目"
                  f"（{', '.join(fulltext_entries)}），"
                  "窗口定义已开启'搜索'页签")
            print("      提示：chmcmd 的全文索引只收 ASCII 词"
                  "（FPC 索引器不认中日韩字），中文关键词搜不到；"
                  "要中文搜索请在 Windows 上用 hhc.exe 重编 docs.hhp")
    elif search_entries or system_fts_flag:
        hygiene_ok = False
        print("      [失败] 未启用全文搜索却发现搜索索引："
              f"{search_entries or '/#SYSTEM 全文搜索标志'}")
    else:
        print("      Windows 全文搜索校验 OK：未启用全文搜索，也未发现搜索索引")
    if missing_binary:
        hygiene_ok = False
        print(f"      [失败] Windows 二进制目录不完整：缺少 {missing_binary}")
    elif binary_toc_required:
        print("      二进制目录完整：供 Windows hh.exe 启动和导航；toc.hhc 供第三方阅读器")
    else:
        print("      目录组成：仅传统 toc.hhc（--toc-mode hhc），无二进制目录与索引")
    if hygiene_ok:
        print("      目录卫生检查 OK：无关键词索引条目、无额外汇总条目")

    if args.prune != "none" and hygiene_ok:
        keep = {args.chm}
        if args.prune == "hhp":
            keep |= {"docs.hhp", "toc.hhc"}
        removed, freed = prune_out_dir(
            out, [*file_list, "docs.hhp",
                  *([chmcmd_hhp] if chmcmd_hhp else [])], keep)
        print(f"      清理中间产物 {removed} 个（-{freed / 1048576:.1f} MB），"
              f"只保留：{'、'.join(sorted(keep))}")

    total_html = sum(len(v) for v in pages.values())
    chm_size = os.path.getsize(chm_path)
    print()
    print("构建完成")
    print(f"  HTML 总计 : {total_html / 1024:.1f} KB（{len(pages)} 个文件，已打进 CHM）")
    print(f"  CHM 大小  : {chm_size / 1024:.1f} KB  -> {chm_path}")
    if args.prune != "chm":
        print(f"  HHP 工程  : {os.path.join(out, 'docs.hhp')}（Windows: hhc.exe docs.hhp）")
    return 0 if hygiene_ok and not missing and ok_toc else 1


def struct_pack_blocksize() -> bytes:
    import struct

    return struct.pack("<I", 0x1000)


def prune_entries(entries: list[TocEntry], allowed: set[str]) -> list[TocEntry]:
    out: list[TocEntry] = []
    for e in entries:
        children = prune_entries(e.children, allowed)
        if e.path in allowed:
            out.append(TocEntry(e.title, e.path, children))
        elif children:
            out.append(TocEntry(e.title, "", children))
    return out


def chm_content_readable(reader) -> bool:
    """CHM 内容是否为可直接读取（未压缩）的形态。"""
    try:
        head = reader.read("/index.html")[:16]
    except KeyError:
        return False
    return head.startswith((b"\xef\xbb\xbf", b"<", b"\n", b"\r", b" "))


def read_chm_stream(reader, chm_path: str, name: str) -> bytes:
    """读取 CHM 内部条目，读不到返回空字节串。

    LZX 压缩产物的系统流（``/#WINDOWS``、``/$FIftiMain`` 等）内容在
    ``::DataSpace/Storage/MSCompressed`` 命名空间里，内置解析器按偏移取到的是
    空数据，此时用 FPC 的 ``chmls`` 整包解包后回读。
    """
    try:
        data = reader.read(name)
    except KeyError:
        data = b""
    if data:
        return data
    if not shutil.which("chmls"):
        return b""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        res = subprocess.run(["chmls", "extractall", chm_path, tmp],
                             capture_output=True)
        if res.returncode != 0:
            return b""
        path = os.path.join(tmp, name.lstrip("/"))
        if not os.path.isfile(path):
            return b""
        with open(path, "rb") as fh:
            return fh.read()


def binary_toc_check(required: set[str], present: set[str],
                     content_readable: bool,
                     toc_head: bytes | None = None) -> tuple[bool, str]:
    """判定二进制目录是否合格，返回 (是否通过, 说明文本)。

    required 为空集表示 `--toc-mode hhc`（不写二进制目录）。

    LZX 压缩产物的正文放在 ::DataSpace/Storage/MSCompressed 命名空间里，
    内置解析器读不出来（content_readable=False），此时只能核对五个流齐全；
    结构由编译它的 chmcmd 保证，正文另有 chmls 逐字节比对。
    早期版本在这里无条件回读 /#TOCIDX，于是**任何** chmcmd 构建都会误报失败
    并把构建脚本的退出码变成 1（build.sh 带 set -e，整条流水线中断）。
    """
    missing = sorted(required - present)
    if missing:
        return False, f"失败：缺少 {missing}"
    if not required:
        return True, "跳过（--toc-mode hhc：只写传统 toc.hhc）"
    if not content_readable:
        return True, "OK（LZX 压缩：五个流齐全；正文由 chmls 解包比对）"
    ok = toc_head == struct_pack_blocksize()
    return ok, "OK" if ok else "失败（/#TOCIDX 头部不是 0x1000 块大小）"


def verify_compressed_content(chm_path: str, out: str,
                              names: list[str]) -> tuple[int | None, int]:
    """用 FPC 的 chmls 解包（LZX）压缩 CHM，与打包前的源文件逐字节比对。

    返回 (一致文件数, 应比对文件数)；chmls 不可用时一致数为 None。
    """
    if not shutil.which("chmls"):
        return None, len(names)
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        res = subprocess.run(["chmls", "extractall", chm_path, tmp],
                             capture_output=True)
        if res.returncode != 0:
            return 0, len(names)
        same = 0
        for name in names:
            src = os.path.join(out, name)
            got = os.path.join(tmp, name)
            if not (os.path.isfile(src) and os.path.isfile(got)):
                continue
            with open(src, "rb") as a, open(got, "rb") as b:
                if a.read() == b.read():
                    same += 1
        return same, len(names)


def prune_out_dir(out: str, written: list[str], keep: set[str]) -> tuple[int, int]:
    """删除本次构建写出的中间产物，只保留 keep 中的文件；返回 (删除数, 释放字节)。

    只动本次真正写出的文件（HTML 页面、CSS、图片、toc.hhc/docs.hhp），
    顺带清掉因此变空的目录，不会误删目录里其它内容。
    """
    removed = 0
    freed = 0
    for name in written:
        if name in keep:
            continue
        path = os.path.join(out, name)
        if os.path.isfile(path):
            freed += os.path.getsize(path)
            os.remove(path)
            removed += 1
    for root, _dirs, _files in os.walk(out, topdown=False):
        if os.path.abspath(root) == os.path.abspath(out):
            continue
        try:
            os.rmdir(root)  # 只删空目录
        except OSError:
            pass
    return removed, freed


if __name__ == "__main__":
    raise SystemExit(main())
