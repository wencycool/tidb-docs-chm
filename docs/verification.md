# 验收记录

记录本项目主要兼容性与版式问题的**现象 → 根因 → 修复 → 证据**，便于复核与回归。

- 验收日期：2026-09-13
- 文档源：`pingcap/docs-cn` @ `924e58f`（828 篇文档、472 张图）
- 验收环境：macOS + **CHM 阅读器-畅享版 2.7.1**（`com.shrek.chmreaderenjoy`，
  App Store 名 CHM Reader - Enjoy）
- 复核命令：`./build.sh`（无图）、`./build.sh --images`（含图）后接
  `python3 tools/verify_chm.py <chm>`

## 1. 侧栏目录"多出一长串条目"

**现象**：目录树在顶层章节之后（例：`术语表` 下面）接着一长串平铺条目，
把第二层文档名全部摊在顶级。

**根因**：CHM 里同时放了目录源 `toc.hhc` 和索引源 `index.hhk` 时，
该阅读器会把**索引条目合并进目录面板并平铺**。用带 / 不带 `index.hhk` 的两个
同内容 CHM 对比可以稳定复现。

**修复**：构建流程不再生成 `index.hhk`，`/#SYSTEM` 也不声明索引；HHP 明确关闭
二进制索引和全文搜索。`chmcmd` 会附带 Windows 原生二进制目录，但传统 `toc.hhc`
仍是唯一的用户目录来源，且工程不创建自定义 `#WINDOWS` 窗口。

**证据**：[`screenshots/02-toc-leak-repro.jpg`](screenshots/02-toc-leak-repro.jpg)（复现）
对比 [`screenshots/01-toc-clean.jpg`](screenshots/01-toc-clean.jpg)（修复后）。

**附带发现**：阅读器对**已经打开的同名 CHM 不会重新加载**——文件换成新版本后
再次 `open` 只是把旧窗口切到前面，仍显示旧目录；`⌘W` 关掉该文档或 `⌘Q`
退出阅读器后重新打开，才会读到新内容（用带版本标记的测试 CHM 实测）。

## 2. 含图片版与图片压缩档

**现象**：默认产物省略图片；原图入库则 CHM 达 123.7 MB。

**修复**：`--images` 收录图片，`--image-profile` 提供三档压缩。PNG 采用
"降采样 + 调色板量化"（比转 JPEG 更小且文字更清晰），带透明通道的图先合成白底。

| 档位 | 处理 | 图片 | 整包 CHM |
| --- | --- | --- | --- |
| `original` | 原图入库 | 111.3 MB | 123.7 MB |
| `compact`（默认） | 宽 ≤1200、PNG 256 色、JPEG q82 | 28.1 MB | 38.5 MB |
| `tiny` | 宽 ≤1000、PNG 128 色、JPEG q78 | 21.3 MB | 31.5 MB |

**证据**：[`screenshots/03-images-render.jpg`](screenshots/03-images-render.jpg)
（compact 档在阅读器中的实际观感）、
[`screenshots/04-images-cover.jpg`](screenshots/04-images-cover.jpg)（封面页）。

## 3. 打开即乱码（默认编码）

**现象**：阅读器"文本编码"为 `Default` 时中文乱码，每次都要手动切到
`Unicode (UTF-8)`。

**根因**：该阅读器与 hh.exe 默认按系统 ANSI（简体中文 = CP936/GBK）解码正文，
只写 `<meta charset="utf-8">` 不足以让它们改判；而目录源 `toc.hhc`、`#STRINGS`
本来就是 GBK，所以侧栏正常、正文乱码。

**实测对照**（同一段中文，5 个变体，均在 `Default` 编码下打开）：

| 变体 | 结果 |
| --- | --- |
| UTF-8，语言 ID 0x0804 | 乱码 |
| **UTF-8 + BOM，语言 ID 0x0804** | **正常** |
| GBK（`gb18030` 写入），语言 ID 0x0804 | 正常 |
| UTF-8，语言 ID 0x0409 | 乱码 |
| **UTF-8 + BOM，语言 ID 0x0409** | **正常** |

**修复**：正文 HTML 与 CSS 统一写入 **UTF-8 BOM**（`--utf8-bom`，默认开启），
阅读器按 BOM 判定编码，`Default` 即可正确显示；正文仍是 UTF-8，不存在字符丢失。
`tools/verify_chm.py` 会报告"正文编码：N/N 个 HTML 带 UTF-8 BOM"。

**证据**：[`screenshots/05-encoding-default-noimage.jpg`](screenshots/05-encoding-default-noimage.jpg)、
[`screenshots/06-encoding-default-images.jpg`](screenshots/06-encoding-default-images.jpg)
（两版都在 `Default` 编码下正常显示）。

## 4. LZX 压缩

**做法**：压缩模式复用 Free Pascal 的 `chmcmd`（`packages/chm` 内含
`paslzxcomp` 的 LZX 实现）。`build_chm.py --compiler auto`（`build.sh` 默认）检测到
`chmcmd` 时使用 LZX；缺少工具时自动回退到内置未压缩打包器。只有显式指定
`--compiler chmcmd` 才会在工具缺失时报错。

| 产物 | 内置打包器（未压缩） | `chmcmd`（LZX） | 降幅 |
| --- | --- | --- | --- |
| 无图版 | 9.92 MB | **2.52 MB** | -75% |
| 含图版（compact 档） | 39.43 MB | **30.73 MB** | -22% |

**目录形态不变**：`chmcmd` 用的 `docs.chmcmd.hhp` 不写索引文件、关掉全文索引，
只保留二进制目录树（Windows hh.exe 原生导航）+ `toc.hhc`。实测该组合在
macOS 阅读器侧栏仍干净（用"有索引 / 无索引"两个变体对照复现过）。

**正确性验证**：构建时用 FPC 的 `chmls extractall` 把压缩 CHM 解包，与打包前的
源文件逐个字节比对——无图版 834/834、含图版 1303/1303 全部一致；另外
`7zz t` 报 `Everything is Ok`，阅读器实测中文、图片、目录树均正常。

**证据**：[`screenshots/07-lzx-compressed.jpg`](screenshots/07-lzx-compressed.jpg)
（2.5 MB 的 LZX 压缩产物在阅读器中的效果）。

内置打包器同样遵循标准 ITSF v3 顺序：`0x60` 字节 ITSF 头、固定 `0x18` 字节
Header Section 0、ITSP 目录、正文数据。回归测试会直接核对五个 64 位偏移字段和
ITSP 签名；正文不得放进 Section 0，否则 Windows 会报 `mk:@MSITStore` 无法打开。

## 5. 正文格式与链接（站点私有写法）

**现象**：部分页面（如"部署本地测试集群"）整段排版塌掉——`>` 引用、```` ``` ````
围栏代码块、有序列表都当普通文字铺在一起；正文里还会出现 `{{{ .company }}}`
这类模板标记；不少超链接指向官网，离线点不开。

**根因**：

1. `<div label="macOS">`、`<details>` 等 HTML 容器内的内容没有加 `markdown="1"`，
   容器里的 Markdown 根本没被渲染；
2. 即使渲染，Python-Markdown 的 `fenced_code` 也不认"列表项内缩进 4 空格"的围栏，
   会退化成行内 `code`；
3. `{{{ … }}}` 是官网站点的 Hugo 变量，构建时未做替换；
4. 官网 URL（`https://docs.pingcap.com/zh/tidb/<ver>/…`）未映射回本地页面。

**修复**（`tools/build_chm.py`）：

| 项 | 做法 | 实测 |
| --- | --- | --- |
| Hugo 变量 | 读仓库 `variables.json` 替换；未知键去掉标记 | 替换 74 处，未知 0 |
| HTML 容器 | `<div label=…>` / `<details>` 加 `markdown="1"` | 容器内列表/引用正常成块 |
| 代码围栏 | 统一转 `<pre><code>`（保留缩进与语言类名） | 7115 个代码块 |
| 官网链接 → 本地 | 目标在本 CHM 内才改内链 | 改内链 56 处 |
| 未收录页面链接 | 改指官网（GitHub/Cloud/K8s 等保持外链） | 1003 处改官网，1022 处保留外链 |
| 裸媒体链接 | `[x](/media/y.png)`：打包图片时保留，否则退化为纯文本 | 488 处（无图版） |

**证据**：
[`screenshots/08-page-format-fixed.jpg`](screenshots/08-page-format-fixed.jpg)（页面整体排版）、
[`screenshots/09-code-block-fixed.jpg`](screenshots/09-code-block-fixed.jpg)（代码块/列表/引用局部）。

**回归校验**（解包后扫描 HTML 里的 `href`/`src`）：

```
无图版：本地链接 9109，外链 3635，断链 0，缺失图片引用 0
含图版：本地链接 9111，外链 3635，断链 0，缺失图片引用 0
```

## 6. 直接打开与当前版式规则

- 每篇正文映射成 `p` + 16 位十六进制哈希的根目录文件名，目录和内链使用相同映射。
- 校验器解析 `/#SYSTEM`，强制默认页为 `index.html`、传统目录为 `toc.hhc`。
- 下载后的 CHM 如带 Windows `Zone.Identifier` 网络来源标记，可在文件属性中手动
  “解除锁定”，或运行 PowerShell `Unblock-File`，处理 `mk:@MSITStore` 拒绝访问。
- 正文不生成章节开头的重复目录；`.nav`、`.toc` 和 `[TOC]` 都会被清理。
- `{{< copyable ... >}}` 等网页短代码不会进入正文。
- 页面采用 1120 px 最大宽度及紧凑边距；列表、表格、代码块和引用块缩小空白。
- 标题内的 `<code>` 继承标题字号。
- 标题锚点保留中文并匹配官网规则，重复标题使用 `-1`、`-2` 后缀；跨版本官网链接不错误地指向当前 CHM。
- 有序列表按层级明确写入 `type="1"`、`type="a"`、`type="i"`，原 `start` 保留。
- 表格自动放入 `.tablewrap` 滚动容器。

v7.5 全量验收结果：773 个主题页全部使用短 ASCII 文件名；464/464 个有序列表
带明确类型；copyable 残留、页首导航、远程显示资源、本地断链和缺失锚点均为 0；LZX 解包后
777/777 个内容文件与打包前字节一致。

## 7. 通用复核清单

```bash
./build.sh && ./build.sh --images
python3 tools/verify_chm.py dist/tidb-docs-cn/tidb-docs-cn.chm
python3 tools/verify_chm.py dist/tidb-docs-cn-images/tidb-docs-cn-images.chm
7zz t dist/tidb-docs-cn/tidb-docs-cn.chm          # Everything is Ok
chmls extractall dist/tidb-docs-cn/tidb-docs-cn.chm /tmp/c   # 压缩包解包核对
```

期望结果：目录链接 0 缺失 / 无索引与全文数据库 / HTML 全部带 UTF-8 BOM /
主题文件名全部为短 ASCII 哈希 / 模板、页首导航和远程显示依赖为 0 /
本地链接与标题锚点 0 缺失 / 有序列表全部带层级类型 / LZX 解包字节一致。
阅读器侧请**先退出或用 ⌘W 关闭旧文档**，再打开新产物。

## 8. 2026-09-14 复评：参照产物比对与回归修复

对 PR #1 做了逐项复评，并补做了一轮**与真实 Windows CHM 的逐字段比对**。

### 8.1 参照产物比对（新增证据）

| CHM | 来源 | Section 0 | 目录偏移 | 写入顺序 | 索引层级 |
| --- | --- | --- | --- | --- | --- |
| `WiX.chm` | 微软 `hhc.exe` | `0x60` / `0x18` | `0x78` | 目录 → 正文 | depth 2 / root 11 |
| `DTFAPI.chm` | 微软 `hhc.exe` | `0x60` / `0x18` | `0x78` | 目录 → 正文 | depth 2 / root 26 |
| TiDB 官方 7.5 中文 CHM | 上游产物 | `0x60` / `0x18` | `0x78` | 目录 → 正文 | depth 2 / root 30 |
| `chmcmd`（FPC）单块产物 | 本机实测 | `0x60` / `0x18` | `0x78` | 目录 → 正文 | depth 1 / root -1 |
| 本项目内置打包器（修复前） | `master` 构建 | `0x60` / `0x984078` | 正文之后 | 正文 → 目录 | depth 2 / root PMGI |

结论：修复后的内置打包器的 ITSF 段序、多块 PMGL/PMGI 根索引、单块 `depth=1/root=-1`、
PMGL quickref（每 5 项、相对头部偏移、`2*(n//5)+2` 字节）与上述参照物逐字段一致；
修复前的 `master` 产物在 `windows_layout_ok()` / `windows_directory_ok()` 上三项全失败，
并声称有二进制目录却五个流全缺——`mk:@MSITStore` 的成因可复现。

对照命令：

```bash
# 三份参照 CHM 的段序（需自备 WiX / TiDB 官方 CHM）
python3 - <<'PY'
import struct, sys
d = open(sys.argv[1], "rb").read()
print(struct.unpack_from("<5Q", d, 0x38))          # section0_off/len, dir_off/len, data_off
PY

# 内置打包器产物的结构与内容核对
chmls extractall dist/tidb-docs-cn/tidb-docs-cn.chm /tmp/c
```

### 8.2 本轮修复

1. **回归阻断**：`--compiler chmcmd`（`build.sh` 在装了 FPC 时的默认路径）此前必然
   `exit 1`。LZX 产物正文在 `MSCompressed` 命名空间，内置解析器读不到 `/#TOCIDX`，
   旧代码却无条件回读并据此判定失败，`build.sh` 因此在 `set -e` 下中断。
   现改为：压缩产物只核对五个流齐全，判定逻辑抽成 `binary_toc_check()` 并补了单测。
2. **`--toc-mode` 恢复可用**：`binary`（默认）写二进制目录，`hhc` 只写 `toc.hhc`；
   `build_hhp()` 的 `Binary TOC` 随之联动，校验器也接受这种自洽形态。
3. **文档纠正**：原注释/文档称"Windows `hh.exe` 需要二进制目录流才能打开"，与事实不符
   （`WiX.chm`、TiDB 官方 7.5 CHM 都没有 `/#TOCIDX` 且能打开，区别只是没有目录页签）。
4. **构建可复现**：页脚日期与 `/#SYSTEM` 记录 10 的毫秒时间戳改写为 `SOURCE_DATE_EPOCH`
   或源码 HEAD 提交时间（原实现用"今天"+`time.time()`）。内置打包器两次构建的
   `sha256` 已核对一致；`chmcmd` 自身会写入时间戳，LZX 产物不保证字节一致。
5. 清理死参数/死代码（`wrap_page` 的 `note`/`subtitle`、`build_hhk()`）；
   `open-chm.cmd` 仅在内容确为本项目旧产物时才删除；README 克隆地址改回本仓库。

### 8.3 仍未闭环（需要 Windows 实机）

- **内置（未压缩）打包器产物的 Windows 打开**：本轮只能证明其结构与三份真实 Windows
  CHM 一致、且可被 FPC `chmls` 独立解包，但没有 Windows 实机打开记录。请在 Windows 上
  打开一份内置打包器产物（`./build.sh --no-compress`）。
- **下载来源标记**：若 CHM 是通过浏览器下载的，Windows 会加 `Zone.Identifier`，
  同样会在打开时被拒绝（文件属性里"解除锁定"或 `Unblock-File`）。这一条与文件格式无关，
  验收时请区分：先在本地复制一份再打开，确认是格式问题还是来源标记问题。
- **`/#TOPICS` 第 3 个 dword 的取值约定**：`hhc.exe` 产物解出来指向 `#URLSTR` 字符串，
  本项目内置写入器存的是 `#URLTBL` 记录偏移，`chmcmd` 两种痕迹都有。它影响 `hh.exe`
  侧栏解析，不影响能否打开；需要 Windows 实机确认侧栏是否正确。
- **内置打包器的 `::DataSpace`** 只有 `NameList` + `Storage/Uncompressed/Content`，
  而 `hhc.exe`/`chmcmd` 产物是 `MSCompressed/Content` 加 `ControlData`、`SpanInfo`、
  `Transform` 一整套。ITSF 允许未压缩形态，但同样建议随上面那条实机验收一并确认。

## 9. 2026-09-15：Windows"搜索"页签（全文搜索）

### 9.1 现象与根因

现象：CHM 在 Windows `hh.exe` 里只有"目录"，左侧没有"搜索"页签。

根因有两条，缺一不可：

1. `.hhp` 里写死了 `Full-text search=No` —— 主动关掉了全文搜索库
   （`$FIftiMain`），而这个库正是"搜索"页签的数据源；
2. 工程文件没有 `[WINDOWS]` 段 —— `hh.exe` 的左侧导航窗格**有哪些页签由窗口定义
   决定**，"搜索"页签对应 `fsWinProperties` 的 `HHWIN_PROP_TAB_SEARCH(0x400)`。
   没有窗口定义时查看器退回只有目录的内置默认窗口，**即使全文搜索库已经生成也
   不会出现"搜索"页签**。

`Binary Index`/`.hhk`（关键词索引）与"搜索"无关：它对应"索引"页签，而且第三方
阅读器会把索引条目平铺进目录树，因此继续保持关闭。

### 9.2 本轮修改

- `build_hhp()` 增加 `full_text_search` 参数（`Full-text search=Yes/No`），并固定
  写一段 `[WINDOWS]`：`0x63520`（含搜索页签位）/ `0x63120`（`--no-search` 时），
  工具栏 `0x384E`；`[OPTIONS]` 增加 `Default window=main`。
- 新增 `--search auto|fulltext|none`（默认 `auto`）：`auto` 在 `chmcmd` 可用时开启；
  `fulltext` 是强约束，`builtin` 或缺 `chmcmd` 时构建直接失败，禁止静默降级；
  `none` 保持无搜索。打包器解析提前到生成 `hhp` 之前，两份工程文件配置一致。
- 构建校验区分"关键词索引"与"全文搜索"：`.hhk`/`#IVB`/`#INDEX` 一律失败；
  全文搜索按三要件核对（`$FIftiMain`、`/#SYSTEM` 记录 4 标志、`/#WINDOWS` 搜索页签位）。
- `verify_chm.py` 增加 `--expect-search auto|yes|no`，并把三要件作为独立检查项输出。
- `build.sh` 增加 `--search=`/`--no-search` 透传，自检时按构建意图传 `--expect-search`。
- 判定逻辑集中到 `chmwriter.py`：`detect_full_text_search_entries()`、
  `detect_keyword_index_entries()`、`detect_auxiliary_index_entries()`、
  `system_fulltext_search_flag()`、`windows_search_tab_enabled()`。
- 新增 `tools/test_chm_search.py`：用真实 `chmcmd` 编译含 `TiKV`/`raftstore`/
  `TiFlash`/`learner` 的小样张，核对三要件与"关闭时无搜索结构"。
- 新增 `docs/windows-search.md`：成因、开关、中文限制、实机验收清单、排查顺序。

### 9.3 本机实测证据

- `chmcmd` + `Full-text search=Yes` 会写出 `/$FIftiMain`（以及 `/#TOPICS`、
  `/#STRINGS`、`/#URLTBL`、`/#URLSTR`），并把 `/#SYSTEM` 记录 4 的全文搜索标志
  置 1；`No` 时三者都没有。
- 带 `[WINDOWS]` 的工程编译出的 `/#WINDOWS` 是 204 字节、`fsValidMembers=0x536`，
  与微软 `hhc.exe` 产物（GaussDB 产品文档）逐字段一致；导航窗格样式
  `0x63520` 含 `0x400`，微软产物用的是 `0x62520`（同样含 `0x400`，少一个收藏页签）。
- `HHWIN_PROP_TAB_SEARCH = 0x400` 取自 Microsoft HTML Help SDK `htmlhelp.h`；
  Wine 的 `hhctrl.ocx`（`dlls/hhctrl.ocx/help.c`）也只在
  `fsWinProperties & HHWIN_PROP_TAB_SEARCH` 时才创建搜索页签，无窗口定义时默认值
  不含该位。
- **中文搜索：FPC 索引器不支持**。`htmlindexer.pas` 的词字符集合只有
  ASCII `a-z 0-9 _`（另有 `#$DE/#$FE`），中日韩字节一律当分隔符；实测索引里只有
  ASCII 词，且索引头代码页/语言 ID 固定为 cp1252/1033、`#SYSTEM` 的 DBCS 标志为 0。
  因此 chmcmd 产物可以搜 `TiDB`/`raftstore` 这类 ASCII 词，中文关键词搜不到；
  需要中文搜索时在 Windows 上用 `hhc.exe docs.hhp` 重编（`--keep-html` 同时保留 HTML
  与工程文件，`--keep-hhp` 只有工程文件、不足以重编）。

### 9.4 仍需 Windows 实机闭环

- "搜索"页签是否真的出现、能否返回结果、点击结果是否打开正文（清单见
  [`windows-search.md`](windows-search.md) 第 4 节）。
- `hh.exe` 是否接受 Free Pascal 生成的 `/$FIftiMain`：微软侧无公开文档，Free Pascal
  侧也没有实机记录，本轮只能证明结构齐备且与 chmcmd 自身读取一致。
- 若实机仍无"搜索"页签，按 `docs/windows-search.md` 第 5 节逐级兜底，首选"在
  Windows 上用 `hhc.exe` 重编 `docs.hhp`"。

## 10. 2026-09-15：Windows 字号 / DPI 适配

### 10.1 现象与成因

现象：CHM 在 Windows `hh.exe` 里左侧导航与正文整体偏小，1080p + 125%/150%
缩放下尤其明显。

成因是两条通路各自的问题：

- 正文样式表把 `14px`/`13px` 写死在每条规则里，没有统一基准，也没有跟随
  Windows/D 缩放放大的余地；
- 左侧 Contents/Search 导航是 Windows 原生控件，**根本不读正文 CSS**，
  它的字体只能来自 CHM 的 `Default Font`（`/#SYSTEM` 记录 16），而之前
  工程文件没有声明这一项，于是用系统默认值；
- 预览页的模拟侧栏写死 `13.5px`，与 Windows 真实 pt 语义不一致，看不出问题。

### 10.2 本轮修改

- 正文样式表改成"唯一基准 + 相对字号"：`html{font-size:100%}`、
  `body{font-size:15px;line-height:1.70}`，`h1/h2/h3/h4` 为
  `1.85em/1.45em/1.22em/1.08em`，`code .94em`、`pre .92em`、`table .94em`；
  保留 `.page{max-width:1120px}`；不使用 JS / `vw` / `clamp()`。
- 新增 `--body-font-size`（px，默认 15，范围 12~20）与 `--nav-font-size`
  （pt，默认 10，范围 8~14）；`build_css(body_font_size)` 动态生成样式表。
- 新增 `RenderOptions`（正文/导航字号 + `nav_default_font`）与
  `default_chm_font(lang, nav_font_size)`：中文 `Microsoft YaHei,<pt>,134`、
  英文 `Segoe UI,<pt>,0`。字体不放进 `BuildFeatures`：那是能力层。
- `build_hhp()` 增加 `default_font`，在 `[OPTIONS]` 写 `Default Font=`；
  `docs.hhp` 与 `docs.chmcmd.hhp` 同值；builtin `ChmWriter` 传同一个
  `default_font`（底层 record 16 写入早已具备，无需改二进制格式）。
- `build_preview()` 接收 `nav_font_size`，侧栏用 `font-size:<n>pt`。
- `chmwriter.py` 增加 `system_default_font()` 读取记录 16；构建校验比对
  "参数 vs 产物"，缺失或不一致直接失败；`verify_chm.py` 报告正文基准字号与
  导航字体；`build.sh` 透传两个字号参数。
- 新增 `docs/windows-font-dpi.md`（两条通路、默认规格、命令、Windows 实机
  测试矩阵、检查清单、明确不做的方案），README 增加第 9.3 节。

### 10.3 本机证据

- `build_css(15)`：基准 `15px`，标题/代码/表格全部 `em`，无写死的标题 px
  字号，无 `clamp`/`vw`；`build_css(16)` 只改基准，比例不变。
- `build_hhp(..., default_font="Microsoft YaHei,10,134")` 写出
  `Default Font=Microsoft YaHei,10,134`，且不改动 `[WINDOWS]` 的
  `0x63520` / `0x63120` 搜索页签位（字体与 Search 正交）。
- `chmcmd` 会把该行原样透传到 `/#SYSTEM` 记录 16（实测解包核对，
  `system_default_font()` 读回 `Microsoft YaHei,10,134`）；builtin writer
  写出的记录 16 同值；`test_render.py` 覆盖取值、范围校验与 record 16。
- `make test` 全绿（渲染用例 + 1239 篇文档扫描 + chmcmd 搜索集成测试）。

### 10.4 仍未闭环（需要 Windows 实机）

- Contents/Search 导航在 100%/125%/150% 缩放下是否清晰可读、无裁切；
- `Microsoft YaHei` 未安装时 Windows 的回退表现；
- `Default Font` 第三段（字符集 134/0）在不同 HTML Help 编译器里的差异——
  本阶段硬目标是"字体名 + 点数"生效。
