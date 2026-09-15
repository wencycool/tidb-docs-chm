#!/usr/bin/env python3
"""build_chm.py 渲染回归测试。

重点盯住「代码块被 Markdown 二次解析」这一类问题：列表项、引用块里的围栏必然
被缩进（≥4 空格）或带 `> ` 前缀，若直接把 `<pre><code>` 写进正文，Markdown 会把
它当成段落文字，`# 注释` 变成标题、代码被拆成好几段。

用法：
    python3 tools/test_render.py            # 用例 + 全量文档扫描（仓库在时）
    python3 tools/test_render.py --fast     # 只跑用例
"""

from __future__ import annotations

import datetime
import os
import re
import struct
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import build_chm as B  # noqa: E402
from chmwriter import ChmReader  # noqa: E402

EMPTY_STATS = {
    "videos": 0, "images": 0, "image_paths": [], "vars": 0, "vars_unknown": {},
    "ext_localized": 0, "ext_kept": 0, "md_external": 0, "code_blocks": 0,
    "media_links": 0,
}

# 渲染后不该出现的痕迹：标题/分隔线（代码内容被当成 Markdown 解析了）、
# 字面围栏、以及没被换回去的占位符
LEAK_RE = re.compile(r"<h[1-6][ >]|<hr\b|```|%%CHM-CODE-\d+%%")

CODE = """```toml
[server]
# 增大 gRPC 线程池
grpc-concurrency = 10

[raftstore]
# 针对写密集型负载进行优化
apply-pool-size = 4
```"""

CODE_TEXT = ("[server]\n# 增大 gRPC 线程池\ngrpc-concurrency = 10\n\n"
             "[raftstore]\n# 针对写密集型负载进行优化\napply-pool-size = 4")


def indent_lines(text: str, prefix: str) -> str:
    return "\n".join(prefix + ln if ln else ln for ln in text.split("\n"))


def render(md_text: str, keep_images: bool = False) -> str:
    stats = dict(EMPTY_STATS)
    _meta, body, code_blocks = B.clean_markdown(md_text, keep_images, stats)
    return B.finalize_html(B.render_callouts(B.md_to_html(body, code_blocks)))


def check(name: str, md_text: str, *conditions: tuple[str, bool]) -> bool:
    """conditions 为 (说明, 是否满足)；有不满足的就打印实际渲染结果。"""
    html = render(md_text)
    failed = [label for label, ok in conditions if not ok]
    print(f"  {'OK  ' if not failed else 'FAIL'} {name}")
    for label in failed:
        print(f"        未满足：{label}")
        print("        ---- 实际渲染 ----")
        for line in html.strip().split("\n"):
            print(f"        {line}")
    return not failed


def cases() -> bool:
    print("[1/2] 渲染用例")
    ok = True

    # hh.exe may otherwise fall back to an old document mode. That mode renders
    # list markers as oversized circles and changes spacing compared with the
    # verified reference CHM.
    sample_page = B.wrap_page("样式测试", "<ul><li>一级</li></ul>", lang="zh")
    ok &= check("Windows 文档模式与列表样式", "正文",
                ("强制使用最新 MSHTML 文档模式",
                 '<meta http-equiv="X-UA-Compatible" content="IE=edge">' in sample_page),
                ("明确三级无序列表标记",
                 "ul{list-style-type:disc}" in B.CSS
                 and "ul ul{list-style-type:circle}" in B.CSS
                 and "ul ul ul{list-style-type:square}" in B.CSS))

    # 1. 顶层围栏
    html = render(f"说明：\n\n{CODE}\n")
    ok &= check("顶层代码块", f"说明：\n\n{CODE}\n",
                ("代码内容原样保留", CODE_TEXT in html),
                ("无标题/围栏残留", not LEAK_RE.search(html)))

    # 2. 列表项内围栏（缩进 4 空格）—— 本次修复的主场景
    md = f"- 说明：\n\n{indent_lines(CODE, '    ')}\n"
    html = render(md)
    ok &= check("列表项内代码块", md,
                ("在 <li> 里生成 <pre><code>", "<li>" in html and html.count("<pre><code") == 1),
                ("代码没有被拆成段落", html.count("</li>") == 1 and CODE_TEXT in html),
                ("注释没变成标题", not LEAK_RE.search(html)))

    # 3. 嵌套列表（缩进 8 空格）
    md = f"- 说明：\n\n    - 二级：\n\n{indent_lines(CODE, '        ')}\n"
    html = render(md)
    ok &= check("嵌套列表内代码块", md,
                ("代码块在二级列表项里", html.count("<ul>") == 2 and html.count("<pre><code") == 1),
                ("无残留", not LEAK_RE.search(html)))

    # 4. 引用块内围栏（每行带 "> " 前缀）
    md = "> 说明：\n>\n" + indent_lines(CODE, "> ") + "\n>\n> 结束语。\n"
    html = render(md)
    ok &= check("引用块内代码块", md,
                ("代码块留在 <blockquote> 内", html.count("<blockquote>") == 1
                 and html.count("<pre><code") == 1),
                ("引用块后的正文仍在块内", "结束语。" in html.split("</blockquote>")[0]),
                ("无残留", not LEAK_RE.search(html)))

    # 5. 列表项 → 引用块 → 代码（缩进 + 引用前缀同时出现）
    md = f"- 说明：\n\n{indent_lines(CODE, '    > ')}\n"
    html = render(md)
    ok &= check("列表项内引用块里的代码块", md,
                ("渲染为 <pre><code>", html.count("<pre><code") == 1 and CODE_TEXT in html),
                ("无残留", not LEAK_RE.search(html)))

    # 6. 代码块后面还有列表项正文：不能被"挤出"列表
    md = f"- 说明：\n\n{indent_lines(CODE, '    ')}\n\n    代码块后面的说明。\n\n- 下一项\n"
    html = render(md)
    ok &= check("代码块后的列表正文", md,
                ("仍在同一个列表里", html.count("<ul>") == 1 and html.count("<li>") == 2),
                ("后续文字在 <li> 内", "代码块后面的说明。" in html.split("<li>")[1]),
                ("无残留", not LEAK_RE.search(html)))

    # 7. div 容器（<div label="…"> 交给 md_in_html 处理）
    md = f'<div label="TiKV">\n\n{CODE}\n</div>\n'
    html = render(md)
    ok &= check("div 容器内代码块", md,
                ("渲染为 <pre><code>", html.count("<pre><code") == 1),
                ("无残留", not LEAK_RE.search(html)))

    # 8. 代码里的 Markdown 语法保持原样
    md = "- 说明：\n\n    ```text\n    * 不是列表\n    | a | b |\n    <div>原样</div>\n    ```\n"
    html = render(md)
    ok &= check("代码里的 Markdown 语法不被解析", md,
                ("星号仍在代码里", "* 不是列表" in html),
                ("HTML 被转义", "&lt;div&gt;原样&lt;/div&gt;" in html),
                ("没生成表格/标题", html.count("<ul>") == 1 and "<hr" not in html))

    # 9. 未闭合的围栏保持原样，不吞掉后面的正文
    md = "- 说明：\n\n    ```toml\n    x = 1\n"
    html = render(md)
    ok &= check("未闭合围栏不吞正文", md, ("正文还在", "说明" in html))

    # 10. 官网用 CSS 按层级切换有序列表标记；CHM 同时写 type 属性，
    # 避免旧版 hh.exe 把内层 a/b/c 错显示成 1/2/3。
    md = "1. 外层一\n\n    1. 内层一\n    2. 内层二\n\n2. 外层二\n"
    html = render(md)
    ok &= check("有序列表层级", md,
                ("外层使用数字", '<ol type="1">' in html),
                ("内层使用字母", '<ol type="a">' in html),
                ("外层序号未被拆开", html.count('<ol type="1">') == 1))

    # 11. 网页专用按钮短代码和页首 TOC 标记不应进入离线正文。
    md = "# 标题\n\n[TOC]\n\n{{< copyable \"shell-regular\" >}}\n\n```bash\necho ok\n```\n"
    html = render(md)
    ok &= check("网页模板标记清理", md,
                ("copyable 已移除", "copyable" not in html),
                ("页内 TOC 已移除", 'class="toc"' not in html and "[TOC]" not in html),
                ("代码仍正常", "echo ok" in html))

    # 12. 宽表格需要滚动容器，不能撑出 CHM 正文区域。
    md = "| A | B |\n|---|---|\n| 1 | 2 |\n"
    html = render(md)
    ok &= check("表格滚动容器", md,
                ("表格被容器包裹", '<div class="tablewrap"><table>' in html))

    # 13. 所有文章用短 ASCII 文件名，本地链接也必须指向相同映射。
    stats = dict(EMPTY_STATS)
    _meta, linked, _blocks = B.clean_markdown(
        "[目标](/nested/目标.md#章节)", False, stats,
        included={"nested/目标.md"}, web_prefix="https://example.invalid/"
    )
    expected = B.html_name_for_doc("nested/目标.md") + "#章节"
    ok &= check("短 ASCII 内链", "[目标](/nested/目标.md#章节)",
                ("链接使用哈希文件名", expected in linked),
                ("文件名只含 ASCII", B.html_name_for_doc("nested/目标.md").isascii()))

    # 14. 直接打开兼容工程只保留传统目录，不生成关键词索引；
    #     Windows"搜索"页签需要全文搜索库 + 窗口定义里的搜索页签位，两者都要有。
    hhp = B.build_hhp("TiDB v7.5", "tidb.chm", ["index.html", "toc.hhc"],
                      full_text_search=True)
    hhp_plain = B.build_hhp("TiDB v7.5", "tidb.chm", ["index.html", "toc.hhc"],
                            full_text_search=False)
    window_line = [ln for ln in hhp.splitlines() if ln.startswith("main=")]
    ok &= check("直接打开 HHP 结构", "正文",
                ("声明传统目录", "Contents file=toc.hhc" in hhp),
                ("默认页正确", "Default topic=index.html" in hhp),
                ("声明默认窗口", "Default window=main" in hhp),
                ("无关键词索引", "Index file=" not in hhp and "Binary Index=No" in hhp),
                ("开启全文数据库", "Full-text search=Yes" in hhp),
                ("窗口定义开启搜索页签",
                 len(window_line) == 1 and ",0x63520," in window_line[0]),
                ("窗口定义字段数为 20",
                 len(window_line) == 1
                 and len(window_line[0].split("=", 1)[1].split(",")) == 20),
                ("关闭全文搜索时无搜索页签",
                 "Full-text search=No" in hhp_plain and ",0x63120," in hhp_plain))

    # 14.1. --search 的三态语义：auto 跟着 chmcmd 走，fulltext 不允许静默降级。
    f = B.resolve_build_features("chmcmd", "auto", "binary", True)
    ok &= check("--search auto + chmcmd", "正文",
                ("开启全文搜索", f.full_text_search is True),
                ("保留二进制目录", f.binary_toc is True))
    f = B.resolve_build_features("builtin", "auto", "binary", False)
    ok &= check("--search auto + builtin", "正文",
                ("不启用全文搜索", f.full_text_search is False))
    f = B.resolve_build_features("chmcmd", "none", "binary", True)
    ok &= check("--search none", "正文",
                ("不启用全文搜索", f.full_text_search is False))
    f = B.resolve_build_features("chmcmd", "auto", "hhc", True)
    ok &= check("--toc-mode hhc", "正文",
                ("不写二进制目录", f.binary_toc is False))
    for compiler, mode, available, label in [
        ("builtin", "fulltext", False, "builtin + fulltext"),
        ("chmcmd", "fulltext", False, "chmcmd 缺失 + fulltext"),
    ]:
        try:
            B.resolve_build_features(compiler, mode, "binary", available)
        except RuntimeError:
            ok &= check(f"--search 强制失败：{label}", "正文",
                        ("按预期抛错", True))
        else:
            ok &= check(f"--search 强制失败：{label}", "正文",
                        ("按预期抛错", False))

    # 14.2. 关键词索引与全文搜索必须分开判定。
    names = ["/index.html", "/$FIftiMain", "/#WINDOWS", "/#SYSTEM"]
    ok &= check("全文搜索条目判定", "正文",
                ("只认全文搜索库",
                 B.detect_full_text_search_entries(names) == ["/$FIftiMain"]),
                ("不把窗口定义当索引",
                 B.detect_keyword_index_entries(names) == []
                 and B.detect_search_related_entries(names) == ["/$FIftiMain"]),
                ("识别关键词索引",
                 B.detect_keyword_index_entries(["/index.hhk"]) == ["/index.hhk"]))

    # 14.3. 窗口定义的"搜索"页签位：取自 MS hhc.exe 产物（GaussDB 产品文档
    #       的 /#WINDOWS 用 0x62520，HTML Help Workshop 默认值 0x63520）。
    def window_blob(props: int) -> bytes:
        entry = bytearray(196)
        struct.pack_into("<II", entry, 0, 196, 0)
        struct.pack_into("<I", entry, 0x10, props)
        return struct.pack("<II", 1, 196) + bytes(entry)

    ok &= check("窗口定义搜索页签判定", "正文",
                ("0x63520 含搜索页签",
                 B.windows_search_tab_enabled(window_blob(0x63520)) is True),
                ("0x63120 不含搜索页签",
                 B.windows_search_tab_enabled(window_blob(0x63120)) is False),
                ("无窗口定义时返回 None",
                 B.windows_search_tab_enabled(b"") is None))

    # 15. 图片版资源也使用短 ASCII 文件名，避免 CHM 内部路径兼容问题。
    stats = dict(EMPTY_STATS)
    _meta, image_md, _blocks = B.clean_markdown("![架构图](/media/架构图.png)", True, stats)
    asset = B.local_asset_name("/media/架构图.png")
    ok &= check("短 ASCII 图片资源", "正文",
                ("图片链接已映射", asset in image_md),
                ("资源名只含 ASCII", asset.isascii()))

    # 15.1. 文档正文可能用 ``/*T![`` 描述 TiDB 注释语法。图片正则不能
    # 从这个未闭合的字面量跨行吞到后续普通链接，否则会生成不存在的 m*.html。
    stats = {**EMPTY_STATS, "image_paths": [], "vars_unknown": {}}
    source = "语法为 `/*T![feature]`。\n\n详见 [Optimizer Hints](/optimizer-hints.md)。"
    _meta, linked, _blocks = B.clean_markdown(
        source, True, stats, included={"optimizer-hints.md"}
    )
    expected = B.html_name_for_doc("optimizer-hints.md")
    ok &= check("图片语法不跨行吞普通链接", source,
                ("普通文档链接仍映射为主题页", f"]({expected})" in linked),
                ("没有伪造 HTML 图片资源", "m" + expected[1:] not in linked),
                ("没有登记伪图片", not stats["image_paths"]))

    # 15.2. 新文档有时直接使用 docs-download.pingcap.com 的 HTML 图片。
    # 本地存在就打包，不存在就显示离线占位，最终页面不能继续引用网络资源。
    remote = ("https://docs-download.pingcap.com/media/images/docs-cn/"
              "tiproxy/tiproxy-traffic-replay.png")
    source = f'<img src="{remote}" alt="TiProxy 流量回放" width="800" />'
    stats = {**EMPTY_STATS, "image_paths": [], "vars_unknown": {}}
    _meta, localized, _blocks = B.clean_markdown(
        source, True, stats,
        available_files={"media/tiproxy/tiproxy-traffic-replay.png"}
    )
    asset = B.local_asset_name("media/tiproxy/tiproxy-traffic-replay.png")
    ok &= check("HTML 远程图片本地化", source,
                ("改成本地短资源名", f'src="{asset}"' in localized),
                ("登记本地图片", stats["image_paths"] == [
                    "media/tiproxy/tiproxy-traffic-replay.png"]),
                ("不再依赖网络", "https://" not in localized))

    missing_source = source.replace("tiproxy-traffic-replay.png", "missing-v2.png")
    stats = {**EMPTY_STATS, "image_paths": [], "vars_unknown": {}}
    _meta, omitted, _blocks = B.clean_markdown(
        missing_source, True, stats, available_files=set()
    )
    ok &= check("缺失的 HTML 远程图片离线降级", missing_source,
                ("显示图片省略说明", "[图片已省略] TiProxy 流量回放" in omitted),
                ("不再依赖网络", "https://" not in omitted),
                ("没有登记不存在的资源", not stats["image_paths"]))

    # 16. 标题锚点必须与官网一致并保留中文，否则 CHM 内章节链接会失效。
    md = "## Point_Get 和 Batch_Point_Get\n\n## 第 2 步：创建 Access Key Pair\n"
    html = render(md)
    ok &= check("官网兼容标题锚点", md,
                ("中英文标题锚点一致", 'id="point_get-和-batch_point_get"' in html),
                ("中文步骤标题锚点一致", 'id="第-2-步创建-access-key-pair"' in html))

    # 17. 官网重复标题使用 -1 后缀；代码标题中的 <option> 是文字，不能被当标签删掉。
    md = ("## 重复标题\n\n## 重复标题\n\n"
          "### `config [show | set <option> <value> | placement-rules]`\n")
    html = render(md)
    ok &= check("重复和代码标题锚点", md,
                ("重复标题使用官网后缀", 'id="重复标题-1"' in html),
                ("尖括号参数保留", 'id="config-show--set-option-value--placement-rules"' in html))

    # 18. 仅把同版本官网链接本地化；跨版本链接必须继续指向原版本网页。
    stats = dict(EMPTY_STATS)
    source = ("[当前](https://docs.pingcap.com/zh/tidb/v7.5/target/#章节) "
              "[旧版](https://docs.pingcap.com/zh/tidb/v7.4/target/#旧章节)")
    _meta, linked, _blocks = B.clean_markdown(
        source, False, stats, link_index={"target": "target.md"},
        included={"target.md"}, web_prefix="https://docs.pingcap.com/zh/tidb/v7.5/"
    )
    ok &= check("官网跨版本链接", "正文",
                ("当前版本改成本地链接", B.html_name_for_doc("target.md") + "#章节" in linked),
                ("旧版本保持官网链接", "https://docs.pingcap.com/zh/tidb/v7.4/target/#旧章节" in linked))

    # 19. 少数上游链接意外重复写了 fragment，只保留第一个有效锚点。
    stats = dict(EMPTY_STATS)
    _meta, linked, _blocks = B.clean_markdown(
        "[RU](/ru.md#什么是-request-unit-ru#什么是-request-unit-ru)", False, stats,
        included={"ru.md"}
    )
    ok &= check("重复锚点清理", "正文",
                ("只保留一个 fragment",
                 linked.endswith(B.html_name_for_doc("ru.md") + "#什么是-request-unit-ru)")))

    # 20. auto 优先压缩，但缺少 chmcmd 时必须能离线回退构建。
    ok &= check("打包器自动选择", "正文",
                ("有 chmcmd 时压缩", B.choose_compiler("auto", True) == "chmcmd"),
                ("缺少时回退内置", B.choose_compiler("auto", False) == "builtin"),
                ("强制模式不改变", B.choose_compiler("chmcmd", False) == "chmcmd"
                 and B.choose_compiler("builtin", True) == "builtin"))

    # 21. Windows 要求 ITSF Section 0 固定为 0x18 字节，ITSP 目录紧随其后，
    # 正文位于目录之后。旧的 content -> directory 顺序会触发 mk:@MSITStore 错误。
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "layout.chm")
        writer = B.ChmWriter(title="TiDB", default_page="index.html", toc_name="toc.hhc",
                             include_binary_toc=True)
        writer.add_file("index.html", b"<html>ok</html>")
        writer.add_file("page.html", b"<html>page</html>")
        writer.add_file("toc.hhc", b"<html></html>")
        root = B.TocNode("Home", "index.html")
        root.add(B.TocNode("Page", "page.html"))
        writer.add_toc(root)
        writer.write(path)
        with open(path, "rb") as fh:
            binary = fh.read()
        section0_offset, section0_len, directory_offset, directory_len, data_offset = \
            struct.unpack_from("<5Q", binary, 0x38)
        ok &= check("Windows CHM 二进制布局", "正文",
                    ("Section 0 固定长度", section0_offset == 0x60 and section0_len == 0x18),
                    ("ITSP 紧随 Section 0", directory_offset == 0x78
                     and binary[directory_offset:directory_offset + 4] == b"ITSP"),
                    ("正文位于目录之后", data_offset == directory_offset + directory_len))
        reader = ChmReader(path)
        ok &= check("Windows CHM 目录块布局", "正文",
                    ("PMGL quickref 与根索引有效", reader.windows_directory_ok()))
        binary_toc = {"/#TOCIDX", "/#TOPICS", "/#STRINGS", "/#URLTBL", "/#URLSTR"}
        system_records = {}
        system = reader.read("/#SYSTEM")
        pos = 4
        while pos + 4 <= len(system):
            code, size = struct.unpack_from("<HH", system, pos)
            system_records[code] = system[pos + 4:pos + 4 + size]
            pos += 4 + size
        ok &= check("Windows CHM 启动目录", "正文",
                    ("五个二进制目录流完整", binary_toc <= set(reader.files)),
                    ("SYSTEM 声明二进制目录", 11 in system_records))

        # 没有写二进制目录时，/#SYSTEM 也不能谎报它存在。
        plain = os.path.join(tmp, "plain.chm")
        plain_writer = B.ChmWriter(include_binary_toc=False)
        plain_writer.add_file("index.html", b"<html>ok</html>")
        plain_writer.write(plain)
        system = ChmReader(plain).read("/#SYSTEM")
        codes = []
        pos = 4
        while pos + 4 <= len(system):
            code, size = struct.unpack_from("<HH", system, pos)
            codes.append(code)
            pos += 4 + size
        ok &= check("SYSTEM 与目录流一致", "正文",
                    ("无目录流时不声明记录 11", 11 not in codes))

    # 22. --toc-mode hhc 时必须真的不写二进制目录（目录形态可控），
    # 且 hhp 的 Binary TOC 随之变化。
    hhp_binary = B.build_hhp("TiDB", "t.chm", ["index.html"], "zh", binary_toc=True)
    hhp_plain = B.build_hhp("TiDB", "t.chm", ["index.html"], "zh", binary_toc=False)
    ok &= check("目录形态开关", "正文",
                ("binary 时声明 Binary TOC=Yes", "Binary TOC=Yes" in hhp_binary),
                ("hhc 时声明 Binary TOC=No", "Binary TOC=No" in hhp_plain))

    # 23. LZX 压缩产物读不到内容，#TOCIDX 校验必须跳过而不是判失败——
    # 早期实现无条件回读，导致任何 chmcmd 构建都误报失败、构建脚本退出码 1。
    streams = B.BINARY_TOC_STREAMS
    ok2, _ = B.binary_toc_check(streams, set(streams), content_readable=False)
    ok &= check("LZX 产物不误判二进制目录", "正文",
                ("压缩且流齐全时通过", ok2),
                ("压缩时不读内容也能给出结论",
                 B.binary_toc_check(streams, set(streams), False)[1].startswith("OK")))
    ok3, _ = B.binary_toc_check(streams, set(streams) - {"/#URLSTR"}, False)
    ok4, _ = B.binary_toc_check(streams, set(streams), True, b"\x00\x10\x00\x00")
    ok5, _ = B.binary_toc_check(streams, set(streams), True, b"\x00\x20\x00\x00")
    ok6, _ = B.binary_toc_check(set(), set(), True, None)
    ok &= check("二进制目录判定规则", "正文",
                ("缺流判失败", not ok3),
                ("未压缩且头部正确判通过", ok4),
                ("未压缩且头部错误判失败", not ok5),
                ("--toc-mode hhc 不要求二进制目录", ok6))

    # 24. 页脚构建日期必须可复现：同一份源码（或同一个 SOURCE_DATE_EPOCH）
    # 必须给出同一个日期，不能随"今天"变化。
    saved = os.environ.get("SOURCE_DATE_EPOCH")
    os.environ["SOURCE_DATE_EPOCH"] = "1700000000"
    try:
        first = B.resolve_build_date(".")
        second = B.resolve_build_date(".")
        epoch = B.resolve_build_epoch(".")
    finally:
        if saved is None:
            os.environ.pop("SOURCE_DATE_EPOCH", None)
        else:
            os.environ["SOURCE_DATE_EPOCH"] = saved
    # 用本地时区换算期望值，避免测试依赖机器时区
    expected_date = datetime.date.fromtimestamp(1700000000).isoformat()
    ok &= check("构建日期可复现", "正文",
                ("SOURCE_DATE_EPOCH 生效且稳定",
                 first == second == expected_date
                 and epoch == 1700000000),
                ("页面日期与构建日期一致",
                 first in B.wrap_page("t", "b", source_ref="release-7.5", build_date=first)))

    # 25. /#SYSTEM 记录 10 的时间戳必须取自 build_time：早期实现写 time.time()，
    # 于是同一份源码在不同时刻构建会得到不同字节的 CHM（页脚日期只是其中一半问题）。
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "stamp.chm")
        writer = B.ChmWriter(build_time=1700000000)
        writer.add_file("index.html", b"<html>ok</html>")
        writer.write(path)
        system = ChmReader(path).read("/#SYSTEM")
        recs: dict[int, bytes] = {}
        pos = 4
        while pos + 4 <= len(system):
            code, size = struct.unpack_from("<HH", system, pos)
            recs[code] = system[pos + 4:pos + 4 + size]
            pos += 4 + size
        ok &= check("CHM 时间戳可复现", "正文",
                    ("记录 10 来自 build_time",
                     10 in recs and struct.unpack("<I", recs[10])[0] == (1700000000 * 1000) % (1 << 32)))
    return ok


def corpus() -> bool:
    repo = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "repos", "docs-cn")
    if not os.path.isdir(repo):
        print("[2/2] 跳过全量扫描（未找到 repos/docs-cn，先跑一次 ./build.sh 拉源码）")
        return True
    print("[2/2] 全量文档扫描")
    pre_re = re.compile(r"<pre><code[^>]*>.*?</code></pre>", re.S)
    escaped_re = re.compile(r"</p>|</blockquote>|<h[1-6][ >]")
    broken: dict[str, int] = {}
    blocks = files = 0
    for root, dirs, names in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in (".git", "media")]
        for name in names:
            if not name.endswith(".md"):
                continue
            path = os.path.join(root, name)
            with open(path, encoding="utf-8", errors="replace") as fh:
                raw = fh.read()
            files += 1
            stats = dict(EMPTY_STATS)
            _meta, body, code_blocks = B.clean_markdown(raw, False, stats)
            html = B.finalize_html(B.render_callouts(B.md_to_html(body, code_blocks)))
            blocks += stats["code_blocks"]
            bad = sum(1 for m in pre_re.finditer(html) if escaped_re.search(m.group(0)))
            bad += len(B.CODE_TOKEN_RE.findall(html)) + html.count("```")
            if bad:
                broken[os.path.relpath(path, repo)] = bad
    print(f"  {'OK  ' if not broken else 'FAIL'} 扫描 {files} 篇文档 / {blocks} 个代码块，"
          f"渲染异常 {sum(broken.values())} 处")
    for path, count in sorted(broken.items(), key=lambda x: -x[1])[:10]:
        print(f"        {count:3d}  {path}")
    return not broken


def main() -> int:
    ok = cases()
    if "--fast" not in sys.argv[1:]:
        ok &= corpus()
    print("\n结论：" + ("全部通过" if ok else "存在失败项"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
