"""
chmwriter.py - 纯 Python 的 CHM (Compiled HTML Help) 打包器

在 macOS / Linux 上生成 .chm，无需 Microsoft HTML Help Workshop。

实现范围：
  * ITSF / ITSP / PMGL / PMGI 目录块（未压缩 section 0）
  * /#SYSTEM 系统文件
  * 二进制目录树：/#TOCIDX、/#TOPICS、/#STRINGS、/#URLTBL、/#URLSTR
    （这是 hh.exe 显示左侧目录树的必要条件，纯 .hhc 不会被 hh.exe 读取）

二进制布局参考：
  - chmlib (jedwing/CHMLib) src/chm_lib.c  —— ITSF/ITSP/PMGL 头部字段与顺序
  - Free Pascal packages/chm/src/*.pas     —— #SYSTEM 记录流与二进制 TOC 语义
"""

from __future__ import annotations

import struct
import uuid
from dataclasses import dataclass, field
from typing import Iterable

BLOCK_SIZE = 0x1000
LANG_ENGLISH = 0x0409
LANG_CHINESE = 0x0804

# 编码说明：hh.exe 的二进制 TOC 字符串（#STRINGS/#SYSTEM）按系统活动代码页
# (ACP) 解码。简体中文 Windows 的 ACP 是 CP936(GBK)，因此中文标题必须以
# GBK 字节写入；正文 HTML 仍是 UTF-8 + <meta charset>，由 IE 引擎按 meta 识别。
TOC_TEXT_ENCODING = "gbk"

ITSF_GUID = uuid.UUID("{7C01FD10-7BAA-11D0-9E0C-00A0C922E6EC}")
ITSF_STREAM_GUID = uuid.UUID("{7C01FD11-7BAA-11D0-9E0C-00A0C922E6EC}")
ITSP_GUID = uuid.UUID("{5D02926A-212E-11D0-9DF9-00A0C922E6EC}")

TOC_HAS_CHILDREN = 4
TOC_HAS_LOCAL = 8


# --------------------------------------------------------------------------
# CHM 内部条目判定：关键词索引（Index）与全文搜索（Search）
#
# 这两件事容易混为一谈，但在 Windows hh.exe 里对应不同侧栏页签：
#
#   *.hhk + /#IVB /#INDEX   关键词索引 -> "索引"页签（本项目始终不生成）
#   /$FIftiMain             全文搜索库 -> "搜索"页签（chmcmd 可生成）
#
# 第三方阅读器会把关键词索引条目平铺进目录树，所以 .hhk 一律禁止；
# 全文搜索库是"搜索"页签的数据源，按 --search 取值决定要不要。
# 具体流名随 FPC/chmcmd 版本可能变化，因此集中在这里判定：
# build_chm.py（构建校验）和 verify_chm.py（成品自检）共用同一份规则。
# --------------------------------------------------------------------------

# 全文搜索索引本体：FPC 写 /$FIftiMain，MS hhc.exe 另写 /#FIfti* 系列
FULLTEXT_SEARCH_MARKERS = ("/$fifti", "/#fifti")

# 关键词索引（.hhk）的伴随流。全文搜索开启时 FPC/MS 也可能写 /#IDXHDR，
# 因此它们只在"未要求全文搜索却出现"时才算异常。
AUXILIARY_INDEX_MARKERS = ("/#idxhdr", "/#ivb", "/#index")


def detect_full_text_search_entries(names: Iterable[str]) -> list[str]:
    """挑出支撑 Windows "搜索"页签的全文搜索内部条目。

    names 为 CHM 内部条目名（如 ``/$FIftiMain``）；返回排序后的原样名字，
    找不到就返回空列表。
    """
    return sorted(n for n in names if n.lower().startswith(FULLTEXT_SEARCH_MARKERS))


def detect_keyword_index_entries(names: Iterable[str]) -> list[str]:
    """挑出关键词索引（``.hhk``）条目。本项目始终不生成，出现即视为污染。"""
    return sorted(n for n in names if n.lower().endswith(".hhk"))


def detect_auxiliary_index_entries(names: Iterable[str]) -> list[str]:
    """挑出关键词索引/全文搜索共用的辅助流（``#IDXHDR``、``#IVB``、``#INDEX``）。"""
    return sorted(n for n in names if n.lower().startswith(AUXILIARY_INDEX_MARKERS))


def detect_search_related_entries(names: Iterable[str]) -> list[str]:
    """用于判定的"搜索相关条目"全集：全文搜索本体 + 辅助流。

    未要求全文搜索时出现其中任何一个，都说明 CHM 里混进了不该有的索引数据。
    """
    return sorted(set(detect_full_text_search_entries(names))
                  | set(detect_auxiliary_index_entries(names)))


# /#SYSTEM 记录 4（编译信息）里的"开启全文搜索"标志。
# 实测：chmcmd 在 Full-text search=Yes 时把该 u32 置 1、No 时置 0，
# 与 hh.exe 是否显示"搜索"页签一致；只看 /$FIftiMain 存在与否不够。
SYSTEM_RECORD_COMPILE_INFO = 4


def parse_system_records(data: bytes) -> dict[int, list[bytes]]:
    """解析 /#SYSTEM：4 字节版本号 + 一串 u16 code / u16 size / data 记录。"""
    records: dict[int, list[bytes]] = {}
    offset = 4
    while offset + 4 <= len(data):
        code, size = struct.unpack_from("<HH", data, offset)
        start = offset + 4
        end = start + size
        if end > len(data):
            break
        records.setdefault(code, []).append(data[start:end])
        offset = end
    return records


def system_fulltext_search_flag(data: bytes) -> bool | None:
    """读 /#SYSTEM 记录 4 的全文搜索标志，返回 True/False/None（无法判定）。"""
    values = parse_system_records(data).get(SYSTEM_RECORD_COMPILE_INFO, [])
    if not values or len(values[0]) < 12:
        return None
    return struct.unpack_from("<I", values[0], 8)[0] != 0


# /#SYSTEM 记录 16：Default Font，格式 `字体名,点数,字符集`。
# 它决定 hh.exe 左侧 Contents/Search 导航树的字体与字号（原生控件，CSS 管不到）。
SYSTEM_RECORD_DEFAULT_FONT = 16


def system_default_font(data: bytes) -> str | None:
    """读 /#SYSTEM 记录 16 的导航窗格 Default Font；未声明返回 None。

    取值形如 ``Microsoft YaHei,10,134``（字体名,点数,字符集），
    按 CHM 的 ANSI 代码页（简体中文为 GBK）解码。
    """
    values = parse_system_records(data).get(SYSTEM_RECORD_DEFAULT_FONT, [])
    if not values:
        return None
    raw = values[0].split(b"\x00", 1)[0]
    return raw.decode(TOC_TEXT_ENCODING, "replace")


# /#WINDOWS（窗口定义）：4 字节窗口数 + 4 字节条目大小 + 条目数组。
# 条目 = HH_WINTYPE 的内嵌形态，字段用 #STRINGS 偏移代替指针；
# 偏移 0x10 是 fsWinProperties（导航窗格样式位）。
WINDOWS_ENTRY_FSWINPROPERTIES = 0x10
# HHWIN_PROP_TAB_SEARCH（Microsoft HTML Help SDK htmlhelp.h）
WINDOWS_FLAG_TAB_SEARCH = 0x00000400


def windows_search_tab_enabled(data: bytes) -> bool | None:
    """判断 /#WINDOWS 里第一个窗口定义是否开启"搜索"页签。

    返回 None 表示 CHM 没有窗口定义，hh.exe 会使用只有目录的内置默认窗口，
    即使 CHM 内有 /$FIftiMain 也不会出现"搜索"页签。
    """
    if len(data) < 8:
        return None
    count, size = struct.unpack_from("<II", data, 0)
    if count < 1 or size < WINDOWS_ENTRY_FSWINPROPERTIES + 4 or len(data) < 8 + size:
        return None
    props = struct.unpack_from("<I", data, 8 + WINDOWS_ENTRY_FSWINPROPERTIES)[0]
    return bool(props & WINDOWS_FLAG_TAB_SEARCH)


# --------------------------------------------------------------------------
# 基础编码工具
# --------------------------------------------------------------------------

def enc_int(value: int) -> bytes:
    """CHM 的 7-bit 变长整数（高位在前，非末字节置 0x80）。"""
    if value == 0:
        return b"\x00"
    groups = []
    while value:
        groups.append(value & 0x7F)
        value >>= 7
    groups.reverse()
    out = bytearray()
    for i, g in enumerate(groups):
        out.append(g | (0x80 if i != len(groups) - 1 else 0))
    return bytes(out)


def _u16(v: int) -> bytes:
    return struct.pack("<H", v & 0xFFFF)


def _u32(v: int) -> bytes:
    return struct.pack("<I", v & 0xFFFFFFFF)


def _i32(v: int) -> bytes:
    return struct.pack("<i", v)


def _u64(v: int) -> bytes:
    return struct.pack("<Q", v & 0xFFFFFFFFFFFFFFFF)


# --------------------------------------------------------------------------
# 目录树节点
# --------------------------------------------------------------------------

@dataclass
class TocNode:
    title: str
    local: str = ""          # CHM 内部路径，如 "index.html"（不带前导 /）
    children: list = field(default_factory=list)

    def add(self, node: "TocNode") -> "TocNode":
        self.children.append(node)
        return node


# --------------------------------------------------------------------------
# 字符串 / URL 池（供二进制 TOC 使用）
# --------------------------------------------------------------------------

class StringPool:
    """/#STRINGS：以 NUL 结尾的字符串池，条目不可跨 0x1000 边界。"""

    def __init__(self) -> None:
        self.buf = bytearray()
        self.index: dict[str, int] = {}

    def add(self, s: str) -> int:
        if s in self.index:
            return self.index[s]
        if not self.buf:
            self.buf.append(0)  # #STRINGS 以 NUL 开头
        data = s.encode(TOC_TEXT_ENCODING, "replace")
        pos = len(self.buf)
        next_block = (pos & 0xFFFFF000) + 0x1000
        if pos + len(data) + 1 > next_block:
            self.buf.extend(b"\x00" * (next_block - pos))
            pos = next_block
        offset = pos
        self.buf.extend(data)
        self.buf.append(0)
        self.index[s] = offset
        return offset


class UrlPool:
    """/#URLSTR + /#URLTBL：URL 字符串池与按 hash 排序的索引表。"""

    def __init__(self) -> None:
        self.urlstr = bytearray()
        self.entries: list[tuple[int, int, int]] = []  # (hash, topic_index, url_offset)
        self._url_index: dict[str, int] = {}

    @staticmethod
    def hash_url(url: str) -> int:
        h = 0
        for ch in url.encode("utf-8", "replace"):
            if ch > ord("Z"):
                ch -= ord("a") - ord("A")
            h = (h * 43 + (ch - ord("0"))) & 0xFFFFFFFF
        return h

    def _add_urlstr(self, url: str) -> int:
        data = url.encode("utf-8", "replace")
        need = 9 + len(data)
        rem = 0x4000 - (len(self.urlstr) % 0x4000)
        if rem < need:
            self.urlstr.extend(b"\x00" * rem)
        if len(self.urlstr) % 0x4000 == 0:
            self.urlstr.append(0)
        offset = len(self.urlstr)
        self.urlstr.extend(_u32(0))  # "Local" 之后的 URL 偏移
        self.urlstr.extend(_u32(0))  # FrameName 偏移
        self.urlstr.extend(data)
        self.urlstr.append(0)
        return offset

    def add(self, url: str, topic_index: int) -> int:
        """返回 #URLTBL 的条目序号，稍后由 build() 回填为真实偏移。"""
        if url.startswith("/"):
            url = url[1:]
        if url not in self._url_index:
            self._url_index[url] = self._add_urlstr(url)
        self.entries.append((self.hash_url(url), topic_index, self._url_index[url]))
        return len(self.entries) - 1

    def build(self) -> tuple[bytes, dict[int, int]]:
        """按 hash 排序生成 #URLTBL，返回 (数据, 序号->偏移 映射)。"""
        order = sorted(range(len(self.entries)), key=lambda i: self.entries[i][0])
        buf = bytearray()
        offsets: dict[int, int] = {}
        for i in order:
            h, topic, url_off = self.entries[i]
            if len(buf) & 0xFFC == 0xFFC:  # 不跨 0x1000 块
                buf.extend(_u32(0))
            # add() 返回的是写入前的条目序号；排序后仍须用原序号回填
            # #TOPICS。若使用排序后的位置，页面会指向错误的 URLTBL 条目。
            offsets[i] = len(buf)
            buf.extend(_u32(h))
            buf.extend(_u32(topic))
            buf.extend(_u32(url_off))
        return bytes(buf), offsets


# --------------------------------------------------------------------------
# 二进制目录树（#TOCIDX / #TOPICS）
# --------------------------------------------------------------------------

class BinaryToc:
    HEADER_SIZE = 0x1000
    ENTRY_MAGIC = 0x29A  # 首个 TTocEntry.IncrementedInt，与 FPC 实现保持一致

    def __init__(self) -> None:
        self.strings = StringPool()
        self.urls = UrlPool()
        self.topics: list[bytearray] = []  # 每条 16 字节
        self._nodes: list[dict] = []

    def build(self, roots: Iterable[TocNode]) -> tuple[bytes, bytes]:
        """返回 (#TOCIDX, #TOPICS)。

        EntryInfo 必须按深度优先（DFS）顺序写入：子节点紧跟父节点。
        大量第三方 CHM 阅读器依赖"线性顺序 + 父指针"恢复层级，
        广度优先（BFS）布局会被平铺成一级列表。
        """
        # 第一遍：深度优先展开，为每个节点预分配 EntryInfo 位置
        flat: list[dict] = []

        def walk(node: TocNode, parent_slot: int) -> None:
            slot = len(flat)
            flat.append(
                {
                    "node": node,
                    "size": 28 if node.children else 20,
                    "parent": parent_slot,
                    "sibling": -1,
                    "first_child": -1,
                    "entry_num": None,
                }
            )
            if parent_slot >= 0 and flat[parent_slot]["first_child"] < 0:
                flat[parent_slot]["first_child"] = slot
            for child in node.children:
                walk(child, slot)

        for root in roots:
            walk(root, -1)

        # 计算每个节点在 EntryInfo 流中的绝对偏移（4KB 头部之后）
        offset = self.HEADER_SIZE
        for item in flat:
            item["offset"] = offset
            offset += item["size"]

        # 同一层的兄弟节点串联
        prev_by_parent: dict[int, int] = {}
        for slot, item in enumerate(flat):
            p = item["parent"]
            if p in prev_by_parent:
                flat[prev_by_parent[p]]["sibling"] = slot
            prev_by_parent[p] = slot

        # 第二遍：写入 EntryInfo / TopicOffset / Entry 三条流
        entry_info = bytearray()
        topic_offsets = bytearray()
        entries = bytearray()
        entry_count = self.ENTRY_MAGIC

        for item in flat:
            node: TocNode = item["node"]
            props = 0
            if node.children:
                props |= TOC_HAS_CHILDREN
            if node.local:
                props |= TOC_HAS_LOCAL

            topic_index = 0
            if props & TOC_HAS_LOCAL:
                topic_index = len(self.topics)
                url_ref = self.urls.add(node.local, topic_index)
                self.topics.append(
                    bytearray(
                        _u32(item["offset"])
                        + _u32(self.strings.add(node.title))
                        + _u32(url_ref)  # 占位，稍后回填真实偏移
                        + _u16(2)  # InContents
                        + _u16(0)
                    )
                )
                topic_offset_pos = len(topic_offsets)
                topic_offsets.extend(_u32(topic_index))
                entries.extend(
                    _u32(item["offset"])
                    + _u32(entry_count)
                    + _u32(topic_offset_pos)
                    + _u32(topic_index)
                )
                item["entry_num"] = entry_count
                entry_count += 1
                topics_or_strings = topic_index
            else:
                topics_or_strings = self.strings.add(node.title)

            entry_info.extend(_u16(0))  # Unknown1
            entry_info.extend(_u16(entry_count - self.ENTRY_MAGIC))  # EntryIndex
            entry_info.extend(_u32(props))
            entry_info.extend(_u32(topics_or_strings))
            entry_info.extend(_u32(flat[item["parent"]]["offset"] if item["parent"] >= 0 else 0))
            entry_info.extend(_u32(flat[item["sibling"]]["offset"] if item["sibling"] >= 0 else 0))
            if props & TOC_HAS_CHILDREN:
                entry_info.extend(
                    _u32(flat[item["first_child"]]["offset"] if item["first_child"] >= 0 else 0)
                )
                entry_info.extend(_u32(0))  # Unknown3

        # 回填 #TOPICS 里的 URLTableOffset
        urltbl, url_offsets = self.urls.build()
        for i, topic in enumerate(self.topics):
            ref = struct.unpack_from("<I", topic, 8)[0]
            struct.pack_into("<I", topic, 8, url_offsets.get(ref, 0))

        header = bytearray(self.HEADER_SIZE)
        struct.pack_into("<I", header, 0, BLOCK_SIZE)
        struct.pack_into("<I", header, 4, self.HEADER_SIZE + len(entry_info) + len(topic_offsets))
        struct.pack_into("<I", header, 8, entry_count - self.ENTRY_MAGIC)
        struct.pack_into("<I", header, 12, self.HEADER_SIZE + len(entry_info))

        tocidx = bytes(header) + bytes(entry_info) + bytes(topic_offsets) + bytes(entries)
        topics = b"".join(bytes(t) for t in self.topics)
        self._urltbl = urltbl
        self._urlstr = bytes(self.urls.urlstr)
        return tocidx, topics

    @property
    def urltbl(self) -> bytes:
        return getattr(self, "_urltbl", b"")

    @property
    def urlstr(self) -> bytes:
        return getattr(self, "_urlstr", b"")

    @property
    def strings_blob(self) -> bytes:
        return bytes(self.strings.buf) or b"\x00"


# --------------------------------------------------------------------------
# CHM 打包器
# --------------------------------------------------------------------------

class ChmWriter:
    def __init__(
        self,
        title: str = "Documentation",
        default_page: str = "index.html",
        language_id: int = LANG_ENGLISH,
        default_font: str = "",
        toc_name: str = "toc.hhc",
        index_name: str = "",
        include_binary_toc: bool = False,
        build_time: int | None = None,
    ) -> None:
        self.title = title
        self.default_page = default_page
        self.language_id = language_id
        self.default_font = default_font
        self.toc_name = toc_name
        self.index_name = index_name
        # True 时写入 Windows hh.exe 启动和导航所需的二进制目录树
        #（/#TOCIDX 等五个文件）。同时保留 toc.hhc 供第三方阅读器使用。
        self.include_binary_toc = include_binary_toc
        # /#SYSTEM 记录 10 的时间戳（秒）。给定固定值时输出可字节复现；
        # 为 None 时用当前时间，仅用于一次性产物。
        self.build_time = build_time
        self.files: list[tuple[str, bytes]] = []   # ("/path/in/chm", data)
        self.toc: list[TocNode] = []

    # -- 内容收集 ---------------------------------------------------------

    def add_file(self, name: str, data: bytes) -> None:
        name = name.replace("\\", "/")
        if not name.startswith("/"):
            name = "/" + name
        self.files.append((name, data))

    def add_toc(self, node: TocNode) -> None:
        self.toc.append(node)

    # -- #SYSTEM ----------------------------------------------------------

    def _system_file(self) -> bytes:
        """记录式 /#SYSTEM（与 FPC chmwriter 相同的语义）。"""
        out = bytearray()
        out.extend(_u32(3))  # version

        def rec(code: int, payload: bytes) -> None:
            out.extend(_u16(code))
            out.extend(_u16(len(payload)))
            out.extend(payload)

        def rec_str(code: int, text: str) -> None:
            data = text.encode(TOC_TEXT_ENCODING, "replace") + b"\x00"
            rec(code, data)

        import time

        # 10: 时间戳（毫秒）。固定 build_time 时用它，保证同一份源码可复现构建。
        stamp = int(self.build_time if self.build_time is not None else time.time())
        rec(10, _u32((stamp * 1000) % (1 << 32)))
        # 9: 编译器版本串
        rec_str(9, "HHA Version 4.74.8702")
        # 4: 搜索/链接开关结构（36 字节）
        rec(
            4,
            _u32(self.language_id)
            + _u32(0)
            + _u32(0)   # 0 = 关闭全文搜索
            + _u32(0)   # klinks
            + _u32(0)   # alinks
            + _u32(0)
            + _u32(0)
            + _u32(0)
            + _u32(0),
        )
        rec_str(2, self.default_page)   # 默认页
        rec_str(3, self.title)          # 标题
        if self.default_font:
            rec_str(16, self.default_font)
        if self.toc_name:
            rec_str(0, self.toc_name)   # 目录文件（供第三方阅读器使用）
        if self.index_name:
            rec_str(1, self.index_name)
        if self.include_binary_toc:
            rec(11, _u32(0))            # 11: 存在二进制 TOC
        return bytes(out)

    # -- 目录块 -----------------------------------------------------------

    def _build_directory(self, entries: list[tuple[str, int, int, int]]) -> bytes:
        """entries: (name, section, offset, length)，返回 ITSP + 块序列。"""
        chunks: list[bytes] = []

        # --- PMGL ---
        pmgl_index: list[list[tuple[str, int, int, int]]] = [[]]
        for entry in entries:
            size = len(enc_int(len(entry[0]))) + len(entry[0]) + len(enc_int(entry[1])) \
                + len(enc_int(entry[2])) + len(enc_int(entry[3]))
            # quickref 每 5 个条目写一个相对偏移，末尾另有 2 字节条目数。
            cur = pmgl_index[-1]
            used = 20 + sum(self._entry_size(e) for e in cur) + size
            quickref = 2 * ((len(cur) + 1) // 5) + 2
            if used + quickref > BLOCK_SIZE and cur:
                pmgl_index.append([entry])
            else:
                cur.append(entry)

        # 单个 PMGL 块时，Windows 要求 depth=1/root=-1，不应额外生成 PMGI。
        # 多块时才生成一层 PMGI 根索引；本项目的条目数量不会使 PMGI 溢出。
        pmgi_chunks: list[bytes] = []
        if len(pmgl_index) > 1:
            pmgi_items = []
            for i, chunk_entries in enumerate(pmgl_index):
                pmgi_items.append((chunk_entries[0][0], i))
            pmgi_chunks.append(self._pmgi_chunk(pmgi_items))

        for i, chunk_entries in enumerate(pmgl_index):
            chunks.append(self._pmgl_chunk(chunk_entries, i, len(pmgl_index)))

        all_chunks = chunks + pmgi_chunks
        index_root = len(chunks) if pmgi_chunks else -1
        index_depth = 2 if pmgi_chunks else 1

        itsp = bytearray()
        itsp.extend(b"ITSP")
        itsp.extend(_u32(1))                      # version
        itsp.extend(_u32(0x54))                   # header length
        itsp.extend(_u32(0x0A))                   # unknown
        itsp.extend(_u32(BLOCK_SIZE))             # block length
        itsp.extend(_u32(2))                      # density / blockidx interval
        itsp.extend(_u32(index_depth))            # index depth
        itsp.extend(_i32(index_root))
        itsp.extend(_u32(0))                         # 首个 PMGL 块
        itsp.extend(_u32(len(pmgl_index) - 1))       # 最后一个 PMGL 块
        itsp.extend(_u32(0xFFFFFFFF))                # unknown
        itsp.extend(_u32(len(all_chunks)))           # 目录块总数
        itsp.extend(_u32(self.language_id))
        itsp.extend(ITSP_GUID.bytes_le)
        itsp.extend(_u32(0x54))                      # 头部长度（再次）
        itsp.extend(_i32(-1))
        itsp.extend(_i32(-1))
        itsp.extend(_i32(-1))
        assert len(itsp) == 0x54
        return bytes(itsp) + b"".join(all_chunks)

    @staticmethod
    def _entry_size(entry: tuple[str, int, int, int]) -> int:
        name = entry[0]
        return (
            len(enc_int(len(name)))
            + len(name)
            + len(enc_int(entry[1]))
            + len(enc_int(entry[2]))
            + len(enc_int(entry[3]))
        )

    def _pmgl_chunk(
        self, entries: list[tuple[str, int, int, int]], index: int, total: int
    ) -> bytes:
        body = bytearray()
        offsets: list[int] = []
        for name, section, offset, length in entries:
            # quickref 偏移以 PMGL 头部之后为起点，不是块的绝对偏移。
            offsets.append(len(body))
            body.extend(enc_int(len(name)))
            body.extend(name.encode("utf-8"))
            body.extend(enc_int(section))
            body.extend(enc_int(offset))
            body.extend(enc_int(length))

        # quickref 区：第 5、10、15...个条目的 WORD 偏移，倒序置于块尾；
        # 最后 2 字节是条目总数。Windows hh.exe 会严格校验这一布局。
        nqr = len(entries) // 5
        quickref_len = 2 * nqr + 2
        quickref = bytearray(quickref_len)
        struct.pack_into("<H", quickref, quickref_len - 2, len(entries))
        for i in range(nqr):
            struct.pack_into("<H", quickref, quickref_len - 4 - 2 * i, offsets[i * 5 + 4])

        header = bytearray()
        header.extend(b"PMGL")
        # free space 必须包含末尾的 quickref 区，阅读器据此推算条目区边界
        header.extend(_u32(BLOCK_SIZE - 20 - len(body)))
        header.extend(_u32(0))
        header.extend(_i32(index - 1 if index > 0 else -1))
        header.extend(_i32(index + 1 if index < total - 1 else -1))

        chunk = bytearray(BLOCK_SIZE)
        chunk[0:20] = header
        chunk[20:20 + len(body)] = body
        chunk[BLOCK_SIZE - quickref_len:BLOCK_SIZE] = quickref
        return bytes(chunk)

    def _pmgi_chunk(self, items: list[tuple[str, int]]) -> bytes:
        body = bytearray()
        offsets: list[int] = []
        for name, block in items:
            offsets.append(len(body))
            body.extend(enc_int(len(name)))
            body.extend(name.encode("utf-8"))
            body.extend(enc_int(block))

        nqr = len(items) // 5
        quickref_len = 2 * nqr + 2
        quickref = bytearray(quickref_len)
        struct.pack_into("<H", quickref, quickref_len - 2, len(items))
        for i in range(nqr):
            struct.pack_into("<H", quickref, quickref_len - 4 - 2 * i, offsets[i * 5 + 4])

        header = bytearray()
        header.extend(b"PMGI")
        header.extend(_u32(BLOCK_SIZE - 8 - len(body)))

        chunk = bytearray(BLOCK_SIZE)
        chunk[0:8] = header
        chunk[8:8 + len(body)] = body
        chunk[BLOCK_SIZE - quickref_len:BLOCK_SIZE] = quickref
        return bytes(chunk)

    # -- 写出 -------------------------------------------------------------

    def write(self, path: str) -> None:
        """写出未压缩 CHM（微软格式允许的合法形态，兼容性最好）。"""
        prepared: list[tuple[str, bytes]] = []
        if self.include_binary_toc:
            prepared.extend(self._collect_toc_files())
        prepared.append(("/#SYSTEM", self._system_file()))
        # DataSpace 属于系统项，按规范不带前导 "/"
        prepared.append(("::DataSpace/NameList", self._name_list()))
        prepared.extend(self.files)

        entries: list[tuple[str, int, int, int]] = []
        payload = bytearray()
        for name, data in prepared:
            entries.append((name, 0, len(payload), len(data)))
            payload.extend(data)
            while len(payload) % 8:
                payload.append(0)
        entries.append(("::DataSpace/Storage/Uncompressed/Content", 0, 0, len(payload)))

        entries.sort(key=lambda e: e[0].lower())
        directory = self._build_directory(entries)

        # 标准 ITSF v3 布局必须是：ITSF header -> 0x18 字节 Header Section 0
        # -> ITSP directory -> content。旧实现把 content 算进 Section 0 并放在
        # directory 前；项目自己的 reader 能按 data_offset 读回，但 Windows
        # hh.exe 会直接报“无法打开文件: mk:@MSITStore:...”。
        itsf_len = 0x60
        section0_len = 0x18
        section0_offset = itsf_len
        dir_offset = section0_offset + section0_len
        data_offset = dir_offset + len(directory)
        file_size = data_offset + len(payload)

        section0 = (
            _u32(0x01FE)            # 固定标识
            + _u32(0)
            + _u64(file_size)       # 整个 CHM 文件大小
            + _u32(0)
            + _u32(0)
        )

        itsf = bytearray()
        itsf.extend(b"ITSF")
        itsf.extend(_u32(3))
        itsf.extend(_u32(0x60))
        itsf.extend(_u32(1))
        itsf.extend(_u32(0))  # timestamp
        itsf.extend(_u32(self.language_id))
        itsf.extend(ITSF_GUID.bytes_le)
        itsf.extend(ITSF_STREAM_GUID.bytes_le)
        itsf.extend(_u64(section0_offset))
        itsf.extend(_u64(section0_len))
        itsf.extend(_u64(dir_offset))
        itsf.extend(_u64(len(directory)))
        itsf.extend(_u64(data_offset))
        assert len(itsf) == 0x60

        with open(path, "wb") as fh:
            fh.write(bytes(itsf))
            fh.write(section0)
            fh.write(directory)
            fh.write(bytes(payload))

    def _collect_toc_files(self) -> list[tuple[str, bytes]]:
        binary_toc = BinaryToc() if self.toc else None
        prepared: list[tuple[str, bytes]] = []
        if binary_toc:
            tocidx, topics = binary_toc.build(self.toc)
            prepared.append(("/#TOCIDX", tocidx))
            prepared.append(("/#TOPICS", topics))
            prepared.append(("/#STRINGS", binary_toc.strings_blob))
            if binary_toc.urlstr:
                prepared.append(("/#URLSTR", binary_toc.urlstr))
            if binary_toc.urltbl:
                prepared.append(("/#URLTBL", binary_toc.urltbl))
        return prepared

    @staticmethod
    def _name_list() -> bytes:
        """::DataSpace/NameList：仅声明 Uncompressed 存储。"""
        out = bytearray()
        out.extend(_u16(16))  # 以 WORD 为单位的总长度
        out.extend(_u16(1))   # 条目数
        out.extend(_u16(12))  # 名称字符数（不含结尾 NUL）
        out.extend("Uncompressed\0".encode("utf-16-le"))
        return bytes(out)


# --------------------------------------------------------------------------
# 只读校验器：把生成的 CHM 解析回来，确认结构自洽
# --------------------------------------------------------------------------

class ChmReader:
    def __init__(self, path: str) -> None:
        with open(path, "rb") as fh:
            self.data = fh.read()
        self._parse()

    def _parse(self) -> None:
        d = self.data
        assert d[0:4] == b"ITSF", "不是合法的 CHM 文件"
        self.version = struct.unpack_from("<I", d, 4)[0]
        self.dir_offset = struct.unpack_from("<Q", d, 0x48)[0]
        self.dir_len = struct.unpack_from("<Q", d, 0x50)[0]
        self.data_offset = struct.unpack_from("<Q", d, 0x58)[0]

        assert d[self.dir_offset:self.dir_offset + 4] == b"ITSP", "ITSP 头位置错误"
        p = self.dir_offset
        self.block_len = struct.unpack_from("<I", d, p + 0x10)[0]
        self.index_root = struct.unpack_from("<I", d, p + 0x1C)[0]
        self.num_blocks = struct.unpack_from("<I", d, p + 0x2C)[0]

        self.files: dict[str, tuple[int, int, int]] = {}
        base = self.dir_offset + 0x54
        for b in range(self.num_blocks):
            off = base + b * self.block_len
            if d[off:off + 4] != b"PMGL":
                continue
            # 条目区终点由 free_space 反推：block_end - free_space 之后的
            # 内容是 quickref 区，不能当作条目解析
            free_space = struct.unpack_from("<I", d, off + 4)[0]
            entry_end = off + self.block_len - free_space
            pos = off + 20
            while pos < entry_end:
                name_len, pos = self._read_enc_int(d, pos)
                name = d[pos:pos + name_len].decode("utf-8")
                pos += name_len
                section, pos = self._read_enc_int(d, pos)
                offset, pos = self._read_enc_int(d, pos)
                length, pos = self._read_enc_int(d, pos)
                self.files[name] = (section, offset, length)

    @staticmethod
    def _read_enc_int(d: bytes, pos: int) -> tuple[int, int]:
        value = 0
        while True:
            b = d[pos]
            pos += 1
            value = (value << 7) | (b & 0x7F)
            if not b & 0x80:
                break
        return value, pos

    def read(self, name: str) -> bytes:
        section, offset, length = self.files[name]
        start = self.data_offset + offset
        return self.data[start:start + length]

    def listing(self) -> list[str]:
        return sorted(self.files)

    def windows_layout_ok(self) -> bool:
        """Check the conventional ITSF v3 order required by Windows hh.exe."""
        d = self.data
        if len(d) < 0x60 or d[:4] != b"ITSF" or self.version != 3:
            return False
        section0_offset, section0_len, directory_offset, directory_len, data_offset = \
            struct.unpack_from("<5Q", d, 0x38)
        return (
            section0_offset == 0x60
            and section0_len == 0x18
            and directory_offset == 0x78
            and d[directory_offset:directory_offset + 4] == b"ITSP"
            and data_offset == directory_offset + directory_len
            and data_offset <= len(d)
        )

    def windows_directory_ok(self) -> bool:
        """Strictly validate PMGL/PMGI quickrefs as Windows hh.exe expects them."""
        d = self.data
        p = self.dir_offset
        if d[p:p + 4] != b"ITSP" or self.block_len != BLOCK_SIZE:
            return False
        depth = struct.unpack_from("<I", d, p + 0x18)[0]
        root = struct.unpack_from("<I", d, p + 0x1C)[0]
        first = struct.unpack_from("<I", d, p + 0x20)[0]
        last = struct.unpack_from("<I", d, p + 0x24)[0]
        base = p + 0x54
        pmgl_indexes: list[int] = []
        pmgi_indexes: list[int] = []

        for block_index in range(self.num_blocks):
            off = base + block_index * self.block_len
            sig = d[off:off + 4]
            if sig == b"PMGL":
                header_size = 20
                pmgl_indexes.append(block_index)
            elif sig == b"PMGI":
                header_size = 8
                pmgi_indexes.append(block_index)
            else:
                return False

            free_space = struct.unpack_from("<I", d, off + 4)[0]
            entry_end = off + self.block_len - free_space
            block_end = off + self.block_len
            if not (off + header_size <= entry_end <= block_end - 2):
                return False
            item_count = struct.unpack_from("<H", d, block_end - 2)[0]
            pos = off + header_size
            starts: list[int] = []
            try:
                for _ in range(item_count):
                    starts.append(pos - (off + header_size))
                    name_len, pos = self._read_enc_int(d, pos)
                    pos += name_len
                    if sig == b"PMGL":
                        _, pos = self._read_enc_int(d, pos)  # section
                        _, pos = self._read_enc_int(d, pos)  # offset
                        _, pos = self._read_enc_int(d, pos)  # length
                    else:
                        _, pos = self._read_enc_int(d, pos)  # child block
            except (IndexError, struct.error):
                return False
            if pos != entry_end:
                return False

            # FPC/Microsoft layout stores the 5th, 10th, ... entry offsets in
            # reverse order immediately before the item-count word.
            for q, entry_index in enumerate(range(4, item_count, 5)):
                actual = struct.unpack_from("<H", d, block_end - 4 - q * 2)[0]
                if actual != starts[entry_index]:
                    return False

        # Some established writers (including chmcmd) set FirstPMGL to the
        # second block while the previous/next chain still begins at block 0;
        # hh.exe accepts both. The pointers must at least reference PMGL blocks.
        if not pmgl_indexes or first not in pmgl_indexes or last not in pmgl_indexes:
            return False
        if len(pmgl_indexes) == 1:
            return depth == 1 and root == 0xFFFFFFFF and not pmgi_indexes
        return depth == 2 and len(pmgi_indexes) == 1 and root == pmgi_indexes[0]
