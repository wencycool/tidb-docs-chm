# TiDB 中文文档离线 CHM 生成工具

把 TiDB 官方中文文档仓库 [`pingcap/docs-cn`](https://github.com/pingcap/docs-cn)
打包成可在 Windows `hh.exe` 中直接打开的单文件 CHM。构建过程在 macOS 或 Linux
完成，不依赖 Microsoft HTML Help Workshop。

默认一次生成两个版本：

- **纯文字版**：去掉图片和视频，体积最小，正文可完全离线显示。
- **压缩图片版**：把图片一同打进 CHM，默认缩放并量化，在清晰度和体积之间取平衡。

当前生成规则已经针对 Windows HTML Help 的打开兼容性、中文编码、目录层级、离线资源、
正文间距、列表序号、表格和标题样式做过完整校验。构建成功后，`dist/` 默认只保留 CHM。

> **English summary:** `tidb-docs-chm` builds self-contained CHM files from the
> official Chinese TiDB documentation repository. It includes a pure-Python CHM
> writer, optional LZX compression through Free Pascal `chmcmd`, UTF-8 BOM pages,
> Windows binary TOC data, offline link rewriting, and optional image compression.
> Project code is MIT licensed. Bundled TiDB documentation remains CC BY-SA 3.0.

## 1. 项目目录结构

```text
tidb-docs-chm/
├── build.sh                 一键构建入口，自动准备环境、源码、产物和校验
├── Makefile                 build / plain / images / test / verify 等快捷命令
├── tools/
│   ├── build_chm.py         TOC 解析、Markdown 清洗、链接转换、HTML 和 CHM 流水线
│   ├── chmwriter.py         内置 CHM 写入器、二进制目录生成器和只读解析器
│   ├── test_render.py       渲染规则测试与全量 Markdown 语料扫描
│   ├── test_chm_search.py   chmcmd 全文搜索（Windows"搜索"页签）集成测试
│   └── verify_chm.py        CHM 结构、目录、编码、资源、链接、搜索和版式自检
├── docs/
│   ├── verification.md      兼容性与版式问题的验收记录
│   ├── windows-search.md    Windows"搜索"页签的说明与实机验收清单
│   ├── windows-font-dpi.md  Windows 字号 / DPI 适配说明与实机测试矩阵
│   └── screenshots/         目录、编码、压缩和排版验收截图
├── repos/
│   └── docs-cn/             官方文档仓库，由 build.sh 自动克隆或更新，不提交
├── dist/                    构建产物，不提交
├── .venv/                   Python 虚拟环境，由 build.sh 自动创建，不提交
├── .gitignore
└── LICENSE
```

仓库只提交构建工具与说明文档。官方文档源码、虚拟环境、HTML 中间文件和 CHM 成品都可
重新生成，因此不进入 Git 仓库。

## 2. 环境要求

### 2.1 必需环境

- macOS 或 Linux
- Python 3.9 或更高版本
- Git
- 可访问 GitHub，用于首次克隆和后续更新 `pingcap/docs-cn`

`build.sh` 会自动创建 `.venv`，并按需要安装：

- `markdown`：Markdown 转 HTML。
- `pillow`：含图片版的缩放、PNG 调色板量化和 JPEG 重编码。

### 2.2 可选工具

| 工具 | 用途 | 未安装时的行为 |
| --- | --- | --- |
| Free Pascal `chmcmd` | 生成 LZX 压缩 CHM | `--compiler=auto` 自动改用内置未压缩打包器 |
| Free Pascal `chmls` | 解包 LZX CHM，与打包输入逐字节核对 | 跳过该项独立解包核对 |
| 7-Zip `7zz` | 对成品执行额外完整性测试 | 自动跳过，不影响构建 |
| Pillow | 压缩图片 | `build.sh` 自动安装；手动构建时 macOS 可退回 `sips` 做有限缩放 |

macOS 安装 Free Pascal：

```bash
brew install fpc
```

安装后，`chmcmd` 和 `chmls` 应能从 `PATH` 中找到。默认 `auto` 模式检测到
`chmcmd` 就生成 LZX 压缩版；找不到时构建仍会完成，但文件会更大。

## 3. 一键构建

### 3.1 首次构建

```bash
git clone https://github.com/wencycool/tidb-docs-chm.git
cd tidb-docs-chm

# 默认构建最新版：纯文字版 + 压缩图片版
./build.sh
```

脚本会依次完成：

1. 创建或复用 `.venv`，安装必要的 Python 依赖。
2. 首次运行时把官方文档克隆到 `repos/docs-cn`。
3. 更新并切换到指定分支的最新提交。
4. 解析官方 `TOC.md`，转换全部文档和本地链接。
5. 生成纯文字版、含图片版，或用户指定的单一版本。
6. 有 `chmcmd` 时执行 LZX 压缩，否则使用内置打包器。
7. 有 `7zz` 时执行额外完整性测试，再运行 `tools/verify_chm.py`。
8. 按保留策略清理中间文件并打印最终路径。

### 3.2 常用示例

```bash
# 指定 TiDB 版本；分支名原样传入
./build.sh release-7.5

# 只生成纯文字版
./build.sh release-7.5 --no-images

# 只生成含图片版，默认 compact 图片压缩档
./build.sh release-7.5 --images

# 含图片版保留原图
./build.sh release-7.5 --images --image-profile=original

# 使用更小的图片压缩档
./build.sh release-7.5 --images --image-profile=tiny

# 强制使用内置未压缩打包器
./build.sh release-7.5 --no-compress

# 强制使用 chmcmd；没有安装时直接报错
./build.sh release-7.5 --compress

# 保留全部 HTML、预览页和工程文件
./build.sh release-7.5 --keep-html

# 只额外保留工程文件 HHP/HHC（要能在 Windows 重编，需用 --keep-html 保留 HTML）
./build.sh release-7.5 --keep-hhp
```

查看官方仓库当前可用的版本分支：

```bash
git ls-remote --heads https://github.com/pingcap/docs-cn.git "release-*"
```

### 3.3 `build.sh` 完整参数

`build.sh` 是推荐入口。版本分支是位置参数，其余选项可放在版本参数前后。

| 参数 | 别名 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `release-x.y` | 无 | `master` | 文档分支，例如 `release-7.5`、`release-8.5` |
| `--images` | `--keep-images` | 默认两版 | 只生成含图片版 |
| `--no-images` | `--plain` | 默认两版 | 只生成纯文字版 |
| `--compiler=auto` | `--compiler auto` | `auto` | 有 `chmcmd` 时 LZX 压缩，否则用内置打包器 |
| `--compiler=builtin` | `--no-compress` | 无 | 强制使用内置未压缩打包器 |
| `--compiler=chmcmd` | `--compress` | 无 | 强制使用 `chmcmd`；工具缺失时构建失败 |
| `--keep-html` | `--keep-all` | 无 | 对应 `--prune=none`，保留所有 HTML 和工程文件 |
| `--keep-hhp` | 无 | 无 | 对应 `--prune=hhp`，保留 CHM、`docs.hhp` 和 `toc.hhc` |
| `--only-chm` | `--prune-chm` | 开启 | 对应 `--prune=chm`，只保留 CHM |
| `--toc-mode=binary` | 可用空格传值 | `binary` | 写 `toc.hhc` 与 Windows 二进制目录（`hh.exe` 有目录页签） |
| `--toc-mode=hhc` | 可用空格传值 | 无 | 只写 `toc.hhc`，Windows 侧无目录页签 |
| `--body-font-size=N` | 可用空格传值 | `15` | 正文基准字号（px，12~24）；标题/代码/表格按 em 相对缩放 |
| `--adaptive-font` / `--no-adaptive-font` | 无 | 开启 | 正文按窗口宽度分档放大（纯 CSS 媒体查询）；关闭后恒定字号 |
| `--nav-font-size=N` | 可用空格传值 | `10` | Windows 左侧 Contents/Search 导航字号（pt，8~14），写进 CHM 的 Default Font |
| `--search=auto` | `--search auto` | `auto` | 有 `chmcmd` 时生成全文搜索库（`hh.exe` 有"搜索"页签），否则关闭并提示 |
| `--search=fulltext` | `--search fulltext` | 无 | 强制生成全文搜索：必须用 `chmcmd`，否则构建失败，不会静默降级 |
| `--no-search` | `--search=none` | 无 | 不生成全文搜索库（体积更小，`hh.exe` 无"搜索"页签） |
| `--image-profile=compact` | 可用空格传值 | `compact` | 图片最大宽 1200、PNG 256 色、JPEG 质量 82 |
| `--image-profile=tiny` | 可用空格传值 | 无 | 图片最大宽 1000、PNG 128 色、JPEG 质量 78 |
| `--image-profile=original` | 可用空格传值 | 无 | 原图入库，不主动重编码 |
| `--image-max-width=N` | 可用空格传值 | 取档位值 | 覆盖最大宽度；`0` 表示不缩放 |
| `--image-colors=N` | 可用空格传值 | 取档位值 | 覆盖 PNG 调色板色数；`0` 表示保持真彩 |
| `--image-jpeg-quality=N` | 可用空格传值 | 取档位值 | 覆盖 JPEG 质量；`0` 表示不重编码 |

参数组合示例：

```bash
./build.sh release-7.5 --images \
  --image-max-width=1100 \
  --image-colors=192 \
  --image-jpeg-quality=80
```

页脚日期默认取文档源码 HEAD 的提交日期；需要复刻某次构建时可用环境变量覆盖：

```bash
SOURCE_DATE_EPOCH=1700000000 ./build.sh release-7.5
```

## 4. 输出目录和文件保留策略

### 4.1 输出命名

`master` 默认产物：

```text
dist/tidb-docs-cn/tidb-docs-cn.chm
dist/tidb-docs-cn-images/tidb-docs-cn-images.chm
```

指定 `release-7.5` 后：

```text
dist/tidb-docs-7.5/tidb-docs-7.5.chm
dist/tidb-docs-7.5-images/tidb-docs-7.5-images.chm
```

项目不会生成额外的 Windows 启动脚本。最终 CHM 可直接复制到 Windows 使用。

### 4.2 清理策略

`build.sh` 默认选择 `chm`；直接调用 `build_chm.py` 时默认选择 `none`。

| `--prune` 值 | 最终保留 | 适用场景 |
| --- | --- | --- |
| `chm` | 仅 `*.chm` | 日常构建和分发 |
| `hhp` | `*.chm`、`docs.hhp`、`toc.hhc` | 保留工程文件存档（正文 HTML 已清理，不能直接重编） |
| `none` | CHM、HTML、CSS、预览页和工程文件 | 检查排版或调试链接 |

主要文件说明：

| 文件 | 用途 |
| --- | --- |
| `*.chm` | 完全自包含的离线文档本体 |
| `docs.hhp` | HTML Help 工程；使用 `--keep-hhp` 或 `--keep-html` 时保留 |
| `toc.hhc` | 传统目录源，已打入 CHM；配合未清理的 HTML 可供 `hhc.exe` 重编 |
| `index.html`、`p*.html`、`style.css` | CHM 的页面和样式输入；`--keep-html` 时保留 |
| `preview.html` | 模拟左侧目录和右侧正文的浏览器预览页，不写入 CHM |
| `license.html` | 文档来源与许可页，写入 CHM |

## 5. 手动构建

需要控制章节数、输出名称、语言或 BOM 时，可以直接调用构建流水线。

```bash
# 最新版全量纯文字 CHM
.venv/bin/python tools/build_chm.py \
  --repo repos/docs-cn \
  --out dist/tidb-docs-cn \
  --title "TiDB 中文文档" \
  --chm tidb-docs-cn.chm \
  --all --lang zh --compiler auto --prune chm

# 指定分支并生成含图片版
.venv/bin/python tools/build_chm.py \
  --repo repos/docs-cn \
  --out dist/tidb-docs-7.5-images \
  --title "TiDB 7.5 中文文档（含图片）" \
  --chm tidb-docs-7.5-images.chm \
  --ref release-7.5 --all --lang zh --images \
  --image-profile compact --compiler chmcmd --prune chm

# 只生成指定顶层章节
.venv/bin/python tools/build_chm.py \
  --repo repos/docs-cn \
  --out dist/tidb-docs-sample \
  --sections "快速上手,部署标准集群" \
  --limit 50 --lang zh --compiler builtin
```

直接运行脚本时不会自动创建虚拟环境或安装依赖。通常应先运行一次 `build.sh`，再使用：

```bash
.venv/bin/python tools/build_chm.py --help
```

## 6. `build_chm.py` 完整参数说明

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--repo DIR` | 必填 | 本地 `pingcap/docs-cn` 仓库路径；目录中应有可由 Git 读取的 `TOC.md` |
| `--out DIR` | 必填 | HTML 中间文件、工程文件和最终 CHM 的输出目录 |
| `--title TEXT` | `TiDB Documentation` | CHM 标题、封面标题和 `/#SYSTEM` 书名 |
| `--chm FILE` | `tidb-docs.chm` | 输出 CHM 文件名 |
| `--all` | 关闭 | 收录 `TOC.md` 中全部顶层章节 |
| `--sections "A,B"` | 空 | 只收录名称完全匹配的顶层章节；未指定 `--all` 或本参数时只取前 3 个顶层章节 |
| `--limit N` | `0` | 最多收录 N 篇文档；`0` 表示不限制，适合用小值快速试跑 |
| `--images` | 关闭 | 打包文档引用的图片；别名为 `--keep-images` |
| `--prune MODE` | `none` | `none` 保留全部；`hhp` 保留 CHM/HHP/HHC；`chm` 仅保留 CHM |
| `--compiler MODE` | `auto` | `auto`、`builtin` 或 `chmcmd`，详见第 8 节 |
| `--search MODE` | `auto` | `auto`、`fulltext` 或 `none`：Windows"搜索"页签用的全文搜索，详见第 8.3 节 |
| `--body-font-size N` | `15` | 正文基准字号（px，12~24）；其余字号是相对它的 `em`，详见第 9.3 节 |
| `--adaptive-font` / `--no-adaptive-font` | 开启 | 正文按窗口宽度分档放大（`--adaptive-font`）/ 恒定字号（`--no-adaptive-font`），详见第 9.3 节 |
| `--nav-font-size N` | `10` | Windows 导航窗格字号（pt，8~14）；写进 `.hhp` 的 `Default Font` 与 `/#SYSTEM` 记录 16 |
| `--image-profile PROFILE` | `compact` | `original`、`compact` 或 `tiny`，详见第 7 节 |
| `--image-max-width N` | 档位值 | 覆盖图片最大宽度；`0` 表示不缩放 |
| `--image-colors N` | 档位值 | 覆盖 PNG 色数；`0` 表示保持真彩 |
| `--image-jpeg-quality N` | 档位值 | 覆盖 JPEG 质量；`0` 表示不重编码 |
| `--ref BRANCH` | 空 | 执行浅层 `fetch` 并切换到指定分支，例如 `release-7.5` |
| `--toc-mode binary\|hhc` | `binary` | `binary` 额外写入 Windows `hh.exe` 用的二进制目录树；`hhc` 只写传统 `toc.hhc`（Windows 侧无目录树），详见第 9.2 节 |
| `--lang zh\|en` | `zh` | `zh` 使用 GBK 目录和语言 ID `0x0804`；`en` 使用英文语言设置 |
| `--utf8-bom` | 开启 | 正文 HTML/CSS 写入 UTF-8 BOM，让阅读器默认按 UTF-8 解码 |
| `--no-utf8-bom` | 关闭 | 不写 BOM，只依赖页面中的 `<meta charset>`；不建议用于中文 CHM |

`--source-ref` 是 `build.sh` 内部使用的来源标记参数，用于封面、页脚和官网链接版本映射，
不属于日常手动参数。它不会切换 Git 分支。

## 7. 图片压缩说明

文档图片以 UI 截图、监控面板和架构图为主。直接收入原图会明显增大 CHM，因此提供三档：

| 档位 | 最大宽度 | PNG | JPEG | 适用场景 |
| --- | ---: | --- | --- | --- |
| `original` | 不缩放 | 不量化 | 不重编码 | 需要保留原始画质，接受较大文件 |
| `compact`（默认） | 1200 px | 256 色 | 质量 82 | 截图文字清晰，兼顾体积 |
| `tiny` | 1000 px | 128 色 | 质量 78 | 优先减小文件，小字号可能稍糊 |

处理规则：

- `.png`、`.jpg`、`.jpeg`、`.bmp`、`.tif`、`.tiff` 会按档位处理。
- `.gif` 和 `.svg` 保持原格式。
- PNG 使用降采样和调色板量化；透明图片先合成到白色背景。
- JPEG 按指定质量重新编码。
- 如果处理后的文件反而更大，自动保留原图。
- `--image-colors=0` 可关闭 PNG 量化；`--image-max-width=0` 可关闭缩放。
- 纯文字版移除图片标签或把裸媒体链接降级为文字，不留下缺失的本地图片引用。
- 原始 Markdown 和原始图片不会被修改，所有处理只发生在输出目录。

历史 v7.5 验收中，`compact` 档将图片资源从约 111 MB 降至约 28 MB。实际大小会随
文档分支、图片数量、压缩器和参数变化，应以本次构建日志为准。

## 8. CHM 打包与 LZX 压缩

### 8.1 三种打包模式

| 模式 | 行为 | 适用场景 |
| --- | --- | --- |
| `auto`（默认） | 找到 `chmcmd` 时生成 LZX 压缩包，否则使用内置打包器 | 推荐 |
| `builtin` | 使用项目内置 ITSF 写入器，内容不做 LZX 压缩 | 无法安装 FPC、快速调试 |
| `chmcmd` | 强制使用 Free Pascal `chmcmd`，缺少时立即报错 | 必须获得较小成品 |

`build.sh --no-compress` 等同于 `--compiler=builtin`；`build.sh --compress` 等同于
`--compiler=chmcmd`。

### 8.2 实现与取舍

- 内置写入器不依赖 Windows 或 FPC，生成合法的未压缩 ITSF CHM。
- `chmcmd` 提供 LZX 压缩，主要压缩 HTML 文本；PNG/JPEG 本身已压缩，因此含图片版
  的二次压缩收益通常小于纯文字版。
- `chmcmd` 使用单独的临时 HHP 配置，关闭关键词索引和 CHI 文件；全文搜索按
  `--search` 决定（默认 `auto`，装了 `chmcmd` 就打开）。
- 两种打包器都按 `--toc-mode` 生成目录：默认 `binary`，即传统 `toc.hhc` 加
  Windows 二进制目录；`hhc` 则只写传统目录。
- 检测到 `chmls` 时，构建器会解包压缩 CHM，并把内容与打包输入逐字节比较。

历史 v7.5 验收中，纯文字版由约 9.9 MB 压缩到约 2.5 MB，`compact` 含图片版由
约 39.4 MB 压缩到约 30.7 MB。数据仅用于说明压缩量级。

### 8.3 Windows"搜索"页签

Windows `hh.exe` 左侧的"搜索"页签需要**三个**条件同时成立，缺一个都不会出现：

1. CHM 内有全文搜索库 `/$FIftiMain`（`.hhp` 的 `Full-text search=Yes`，由
   `chmcmd` 编译生成）；
2. `/#SYSTEM` 记录 4 的"全文搜索开启"标志已置位（编译器自动写）；
3. `/#WINDOWS` 窗口定义的导航窗格样式含 `HHWIN_PROP_TAB_SEARCH`（`0x400`）。

第 3 条容易漏：导航窗格有哪些页签由窗口定义决定，**不声明窗口定义时 `hh.exe`
会退回只有目录的内置默认窗口**——即使全文搜索库已经生成，也看不到"搜索"页签。
因此 `build_hhp()` 总是写一段 `[WINDOWS]`：

```ini
[WINDOWS]
main="TiDB 中文文档","toc.hhc","","index.html","index.html",,,,,0x63520,,0x384E,,,,,,,,0
```

`0x63520` 是 HTML Help Workshop 的默认导航窗格样式（三窗格 + 自动同步 +
搜索 + 收藏 + 增强搜索），`--no-search` 时改用不含搜索位的 `0x63120`。
关键词索引（`.hhk`、`Binary Index`）仍然保持关闭：它是"索引"页签而不是
"搜索"页签，而且第三方阅读器会把索引条目平铺进目录树。

正式发布建议显式要求搜索，这样缺 `chmcmd` 时会直接失败而不是悄悄退化：

```bash
./build.sh --compiler=chmcmd --search=fulltext
# 或直接调用
.venv/bin/python tools/build_chm.py \
  --repo repos/docs-cn --out dist/tidb-docs-cn --all --lang zh \
  --compiler chmcmd --search fulltext
```

**中文搜索的限制**：`chmcmd` 的全文索引器（Free Pascal）只认 ASCII 的
`a-z 0-9 _`，中日韩文字一律当分隔符，因此它生成的索引只收录 `TiDB`、`TiKV`、
`raftstore`、`tidb_mem_quota_query` 这类 ASCII 词，**中文关键词搜不到结果**。
微软 `hhc.exe` 的索引器支持中日韩，所以需要中文搜索时请临时保留 HTML 与工程
文件（`./build.sh --keep-html`，正文 HTML 也在时 `hhc.exe` 才能重编）并在 Windows 上重编：

```bat
hhc.exe docs.hhp
```

默认构建的输出位置与保留策略不变：`./build.sh` 只产出 `dist/tidb-docs-cn/`、
`dist/tidb-docs-cn-images/` 里的 CHM（`--prune chm`），`--keep-html` 只是需要
重编时才临时加的参数。完整说明、成因分析和实机验收清单见
[`docs/windows-search.md`](docs/windows-search.md)。

## 9. 编码与目录约定

### 9.1 中文编码

| CHM 内容 | 编码 | 原因 |
| --- | --- | --- |
| 正文 HTML | UTF-8 + BOM + `<meta charset>` | `hh.exe` 和第三方阅读器可直接判定 UTF-8 |
| 页面 `<title>` | GBK（CHM 的 ANSI 代码页） | `chmcmd` 把 `<title>` 的原始字节直接抄进 `#TOPICS`/`#STRINGS`，而 `hh.exe` 的"搜索结果"列表按系统 ANSI 显示这些字符串；标题若是 UTF-8 字节就会整列乱码 |
| `style.css` | UTF-8 + BOM | 与正文一致，避免非 ASCII 内容误判 |
| `toc.hhc` | GBK | 简体中文 Windows 的 HTML Help 按 ANSI 读取 |
| `/#STRINGS` | GBK | 目录树标题、搜索结果标题都按活动代码页读取 |
| `/#SYSTEM` | GBK | 书名、默认页、目录名与导航字体按活动代码页读取 |
| `docs.hhp` | GBK | Windows `hhc.exe` 按 ANSI 读取工程文件 |

页面正文仍是 UTF-8 + BOM，只有 `<title>` 按 ANSI 写：两者用途不同——正文由
MSHTML 按 BOM/meta 解码，标题只是给 CHM 的 ANSI 字符串表用（页面里不显示）。
构建结束时 `build_chm.py` 会逐页把 `<title>` 与期望的 ANSI 字节逐字节比对，
不一致直接失败。

关闭 UTF-8 BOM 可能导致中文正文在默认编码下乱码，除非有明确测试需求，否则不要使用
`--no-utf8-bom`。

### 9.2 两种目录与 `--toc-mode`

传统 `toc.hhc` 适合第三方阅读器，并可供 Windows `hhc.exe` 重编；Windows 自带 `hh.exe`
的"目录"页签来自 CHM 内的二进制目录流（`/#TOCIDX` 等五个流）。因此：

- `--toc-mode binary`（默认）：同时写 `toc.hhc` 和二进制目录。Windows `hh.exe`
  有原生左侧目录，第三方阅读器仍按 `toc.hhc` 显示层级，两者都指向同一组短 ASCII 页面。
- `--toc-mode hhc`：只写 `toc.hhc`，`/#SYSTEM` 也不再声明二进制目录。Windows 侧没有
  目录树但能正常打开、正常阅读正文；适合侧栏行为异常的第三方阅读器排查对比。

需要说明的是，**能否在 Windows 打开并不取决于二进制目录流**：`hhc.exe` 自己生成的
CHM 可以完全不带 `/#TOCIDX`（例如 WiX 3.14 文档随附的 `WiX.chm`、TiDB 官方 7.5 中文
CHM 都没有），而同一个 CHM 在 `hh.exe` 里仍然正常打开——区别只是没有目录页签。
真正决定 `mk:@MSITStore` 能否打开的是 ITSF 段序（见第 11 节与
[`docs/verification.md`](docs/verification.md)）。

目录卫生依靠"不生成关键词索引文件"保证，而不是删除二进制目录流；全文搜索库
（`/$FIftiMain`）不参与目录树，按第 8.3 节单独判定。

### 9.3 字号、DPI 与导航字体

正文和 Windows 左侧导航是两条独立通路，必须分开调：

| 区域 | 控制方式 | 参数 |
| --- | --- | --- |
| 正文 | `style.css`：`body` 唯一基准字号 + 标题/代码/表格的 `em` | `--body-font-size`（默认 15px） |
| 正文窗口自适应 | `style.css`：按正文区宽度的媒体查询分档放大基准字号 | `--adaptive-font`（默认开启）/ `--no-adaptive-font` |
| 左侧 Contents/Search 导航 | CHM 的 `Default Font`（`.hhp` 与 `/#SYSTEM` 记录 16） | `--nav-font-size`（默认 10pt） |

左侧导航是 Windows 原生控件，CSS 管不到它，所以只能通过 `Default Font` 指定
"字体名,点数,字符集"：中文构建写 `Microsoft YaHei,10,134`，英文构建写
`Segoe UI,10,0`。两条打包后端（`chmcmd`、builtin）写的是同一个取值，
`docs.hhp`、`docs.chmcmd.hhp` 与 builtin 的 `/#SYSTEM` 三者一致。
**它只能给一个固定 pt，不可能随窗口缩放**；能随窗口自适应的是右侧正文。

正文默认按窗口宽度分档放大（`--adaptive-font`），纯 CSS 媒体查询实现，
不使用 JS / `vw` / `clamp()`：

| 正文区宽度 | <800 | ≥800 | ≥920 | ≥1040 | ≥1160 | ≥1280 | ≥1400 | ≥1520 | ≥1640 | ≥1760 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 基准字号 | 15px | 16px | 17px | 18px | 19px | 20px | 21px | 22px | 23px | 24px |

即"≥800px 起每宽 120px 放大 1px，最多 +9px"。标题/代码/表格都是基准的 `em`，
所以只改 `body` 一个字就整页等比放大；字号封顶在 `BODY_FONT_SIZE_RANGE` 上限
（24px）。旧文档模式下媒体查询会被整段忽略，自动回落到基准字号，不会出错。
不想要这种分档时：

```bash
./build.sh --no-adaptive-font            # 正文恒定字号
./build.sh --body-font-size 16 --nav-font-size 11   # 内网机器仍偏小时整体调大一档
```

`hh.exe` 的视口宽度 = 窗口宽 − 左侧导航宽度，所以上面的阈值会略晚于窗口尺寸触发。
`make preview` 生成的预览页把正文放进 iframe，本地拖动浏览器窗口即可看到分档效果。

完整说明、Windows 实机测试矩阵与检查清单见
[`docs/windows-font-dpi.md`](docs/windows-font-dpi.md)。

## 10. Markdown、链接与资源处理

| 源文档内容 | 构建结果 |
| --- | --- |
| `{{{ .company }}}` 等变量 | 使用仓库 `variables.json` 替换；未知标记被清理 |
| `{{< copyable ... >}}` 等短代码 | 删除标记，保留其后的实际代码块 |
| `<div label="macOS">`、`<details>` | 允许容器内 Markdown 正常渲染 |
| 列表项内缩进的围栏代码 | 预处理成稳定的 `<pre><code>`，避免被解析为行内代码 |
| 同版本且已收录的 `.md` 或官网链接 | 改为本地哈希 HTML，并保留有效锚点 |
| 跨版本 TiDB 链接 | 保留原版本官网 URL，不错误映射到当前 CHM |
| 未收录的 TiDB 页面 | 改为对应官网页面，避免留下无效本地链接 |
| TiDB Cloud、Kubernetes、GitHub 等资料 | 保留外链 |
| Markdown 图片和 HTML `<img>` | 含图片版下载或读取后写入本地资源；纯文字版移除显示依赖 |
| 远程图片下载失败 | 降级为可读文本，不保留必须联网显示的 `<img>` |
| 视频和播放器 | 删除，不写入 CHM |
| `TOC.md` 条目名里的行内代码（`` `ADMIN` ``） | 目录树只显示 `ADMIN`：去掉反引号等 Markdown 定界符 |
| 条目名里的方括号（`` `ADMIN CHECK [TABLE|INDEX]` ``） | 正确解析链接、保留方括号文字，页面照常收录 |
| 前言 `title` 里的行内代码 | 页面 `<title>`（搜索结果、窗口标题）同样只保留文字 |

离线的含义是：正文和图片版中保留的图片无需联网即可显示。主动点击外部参考链接仍会尝试
打开浏览器；这不会影响当前 CHM 页面离线阅读。

### 10.1 纯文本出口的 Markdown 定界符

官方文档是 Markdown，条目名和前言 `title` 会用反引号标代码（如
`` `ADMIN ALTER DDL JOBS` ``、``通过系统变量 `tidb_read_staleness` 读取历史数据``）。
CHM 里有几处**不经过 Markdown 渲染**的纯文本出口，会把反引号原样显示出来：

| 出口 | 显示位置 |
| --- | --- |
| `toc.hhc`、二进制目录树（`/#TOCIDX`、`/#STRINGS`） | 左侧目录树 |
| `#TOPICS`/`#STRINGS`（页面 `<title>`） | Windows"搜索"结果列表、窗口标题 |

构建时统一用 `strip_inline_code()` 脱去行内代码定界符、只保留其中的文字，因此
`toc.hhc`、二进制目录、封面页、预览页和页面 `<title>` 拿到的都是干净文本。

只删定界符、不动内容：`ADMIN CHECK [TABLE|INDEX]`、`GRANT <privileges>` 里的
`| < >` 是 SQL 语法的一部分，必须原样保留。链接解析也按"从右往左找真正的 `](` 分界"
处理标题内的方括号，否则 `ADMIN CHECK [TABLE|INDEX]` 这类条目会整条解析失败、
页面随之从 CHM 里消失。

## 11. 校验方法

### 11.1 渲染回归测试

```bash
# 固定用例 + repos/docs-cn 全量 Markdown 扫描
make test

# 等价命令
.venv/bin/python tools/test_render.py

# 只跑固定用例，不扫描全部文档
.venv/bin/python tools/test_render.py --fast
```

测试覆盖列表、嵌套代码块、引用块、HTML 容器、模板清理、图片语法、官网链接、标题锚点、
有序列表类型、目录形态开关、二进制目录判定、构建日期可复现、目录条目名去 Markdown
定界符，以及 Windows CHM 二进制布局和启动目录、正文字号相对化与 Windows 导航字体。
`make test` 还会跑一遍 `test_chm_search.py`：它用真实的 `chmcmd`
编译含 `TiKV`、`raftstore`、`TiFlash`、`learner` 的小样张，核对全文搜索库、
`/#SYSTEM` 标志、窗口定义搜索页签位确实都已生成，且没有关键词索引；
没有安装 `chmcmd` 时该项自动跳过。

### 11.2 成品自检

```bash
# 默认 auto：只要求"声明与产物自洽"
.venv/bin/python tools/verify_chm.py \
  dist/tidb-docs-7.5/tidb-docs-7.5.chm

# 明确要求 / 不允许 Windows"搜索"页签
.venv/bin/python tools/verify_chm.py --expect-search yes dist/.../tidb-docs-7.5.chm
.venv/bin/python tools/verify_chm.py --expect-search no  dist/.../tidb-docs-7.5.chm
```

校验器会检查：

- ITSF Section 0、ITSP、PMGL/PMGI 目录块和 Windows 启动目录是否有效。
- `/#SYSTEM` 是否把启动页和传统目录声明为 `index.html`、`toc.hhc`。
- `toc.hhc` 和五个 Windows 二进制目录流是否齐全。
- 是否混入 `index.hhk` 等关键词索引（全文搜索库不参与目录树，按 `--expect-search` 单独判定）。
- 所有正文 HTML 是否带 UTF-8 BOM。
- 所有主题页和本地资源是否使用短 ASCII 文件名。
- 是否残留 Hugo 短代码、页首重复导航或必须联网显示的资源。
- 所有本地 `href` 和 `src` 是否存在。
- 带 `#fragment` 的本地链接能否找到对应标题锚点。
- 有序列表是否明确写入数字、字母或罗马数字类型。
- LZX 解包内容是否与打包输入一致。
- 全文搜索三要件是否一致：`/$FIftiMain`、`/#SYSTEM` 全文搜索标志、
  `/#WINDOWS` 窗口定义的搜索页签位（`--expect-search yes|no|auto`）。
- 正文基准字号（`style.css`）与 Windows 导航字体（`/#SYSTEM` 记录 16）是否写入。

### 11.3 可选的独立工具检查

```bash
7zz t dist/tidb-docs-7.5/tidb-docs-7.5.chm
7zz l dist/tidb-docs-7.5/tidb-docs-7.5.chm
chmls extractall dist/tidb-docs-7.5/tidb-docs-7.5.chm /tmp/tidb-chm
```

详细的问题现象、根因、修复和验收证据见
[`docs/verification.md`](docs/verification.md)。

## 12. Makefile 快捷命令

| 命令 | 等价行为 |
| --- | --- |
| `make help` | 显示快捷命令 |
| `make build` | 运行 `./build.sh`，生成纯文字版和含图片版 |
| `make plain` | 只生成纯文字版 |
| `make images` | 只生成 `compact` 含图片版 |
| `make test` | 执行渲染用例和全量语料扫描 |
| `make verify` | 校验默认的两个 CHM；不存在的含图片版会跳过 |
| `make preview` | 使用 `--keep-html` 构建并在 macOS 打开预览页 |
| `make clean` | 删除整个 `dist/` 构建目录 |

## 13. 已知限制

- LZX 压缩依赖 Free Pascal `chmcmd`；未安装时只能生成未压缩 CHM。
- 外部网站内容不会被镜像，外链在无网络环境中无法访问。
- 不生成关键词索引（`.hhk`），以保证目录干净和跨阅读器兼容性。
- 全文搜索库由 `chmcmd` 生成，它的索引器不支持中日韩文字：Windows"搜索"页签可用，
  但中文关键词搜不到（只能搜 `TiDB`、`raftstore` 这类 ASCII 词）。需要中文搜索请在
  Windows 上用 `hhc.exe` 重编 `docs.hhp`，详见 [`docs/windows-search.md`](docs/windows-search.md)。
- macOS/Linux 无法原生运行 Windows `hh.exe`。仓库内可比对的是**结构证据**：内置写入器
  的 ITSF 段序、多块 PMGL/PMGI 根索引、quickref 布局都与三份真实 Windows CHM
  （微软 `hhc.exe` 生成的 `WiX.chm`、`DTFAPI.chm`，以及 TiDB 官方 7.5 中文 CHM）
  以及 FPC `chmcmd` 的产物逐字段一致。内置打包器产物的 Windows 实机打开仍需人工验收，
  清单见 [`docs/verification.md`](docs/verification.md) 第 6 节。
- 构建可复现：页脚日期与 `/#SYSTEM` 记录 10 的时间戳都取 `SOURCE_DATE_EPOCH`（若设置）
  或文档源码 HEAD 的提交时间，不再使用"当前时间"。内置打包器产物因此可字节复现
  （两次构建 `sha256` 一致）；`chmcmd` 自己会写时间戳，LZX 产物不保证字节一致。
- 文档内容和图片压缩结果会随上游分支变化，历史体积与文件数量仅供参考。

## 14. License 与来源说明

- **本仓库代码**：`tools/`、`build.sh`、`Makefile` 等采用 MIT License，见
  [`LICENSE`](LICENSE)。
- **TiDB 文档内容**：来自 [`pingcap/docs-cn`](https://github.com/pingcap/docs-cn)，
  版权归 PingCAP 所有，采用
  [CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/) 许可。
- 生成的 CHM 会包含来源和许可页；分发时应保留署名与相同许可。
- `docs/screenshots/` 是 TiDB 文档的渲染结果，同样按文档许可使用。
- 本项目是第三方离线阅读工具，与 PingCAP 无隶属关系；文档内容以
  [TiDB 官方文档](https://docs.pingcap.com/zh/tidb/stable/) 为准。
