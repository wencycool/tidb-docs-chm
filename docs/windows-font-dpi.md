# Windows 字号 / DPI 适配

> 面向 Windows 用户与维护者。解决的问题：CHM 在 Windows（尤其 1080p、125% /
> 150% 缩放、x86 `hh.exe`）里左侧导航与正文整体偏小，以及正文不随窗口变化。
>
> 代码位置：`tools/build_chm.py`（`CSS`/`build_css()`、`RenderOptions`、
> `default_chm_font()`、`build_hhp()`、`build_preview()`）、`tools/chmwriter.py`
> （`system_default_font()`、`/#SYSTEM` 记录 16）。
> 记录日期：2026-09-15（窗口自适应已移除，固定版式）。

## 1. 三条通路必须分开

```text
┌──────────────────── hh.exe 窗口 ────────────────────┐
│  Contents / Search          │  正文 HTML 页面        │
│  Windows 原生控件            │  MSHTML 渲染            │
│  ↑ Default Font              │  ↑ style.css            │
│    （/#SYSTEM 记录 16）      │    固定基准字号 + em     │
│    固定 pt，不随窗口缩放       │    版心 1120px 居中      │
└─────────────────────────────┴────────────────────────┘
```

- **左侧导航**（目录树、搜索结果列表）是 Windows 原生控件，**正文 CSS 管不到它**，
  只能通过 CHM 的 `Default Font`（`.hhp` 的 `Default Font=`，落到 `/#SYSTEM`
  记录 16）指定"字体名,点数,字符集"。它只有一个固定 pt，**不可能随窗口缩放**。
- **右侧正文**是 HTML 固定版式：`style.css` 的固定基准字号（`--body-font-size`，
  默认 15px）加 em 相对字号，版心 1120px 居中，不随窗口变化，不使用 JS、
  媒体查询、`zoom`、`vw`、`clamp()`，在各种文档模式下显示一致。
- 历史上的两版窗口自适应已移除（见第 6 节）：分档改 `font-size` 只放大文字、
  版式比例走样；分档改 `body` 的 `zoom` 会把两侧 `auto` 边距一起放大，
  导致右侧内容被裁、两侧留白不对称。

## 2. 默认视觉规格

| 区域 | 旧值 | 现在 |
| --- | --- | --- |
| 正文基准 | 14px | **15px**（`--body-font-size`，固定值） |
| 正文行高 | 1.65 | **1.70** |
| `h1` | 28px | **1.85em**（15px 下 ≈ 27.75px） |
| `h2` | 21px | **1.45em**（≈ 21.75px） |
| `h3` | 18px | **1.22em**（≈ 18.3px） |
| `h4` | 16px | **1.08em**（≈ 16.2px） |
| 行内 `code` | 13px | **.94em**（≈ 14.1px） |
| `pre` | 13px | **.92em**（≈ 13.8px，行高 1.55） |
| `table` | 13px | **.94em**（≈ 14.1px，行高 1.50） |
| Windows 导航 | 系统默认 | **10pt**（`--nav-font-size`，不随窗口缩放） |
| 预览页侧栏 | 13.5px 写死 | **10pt**（跟随 `--nav-font-size`） |

正文是固定版式（`--body-font-size` 只定基准，默认 15px）：版心 1120px 居中，
大窗口下两侧留白对称，窄窗口下自动流式占满、无横向滚动条。

正文基准仍是 15px（而不是直接 17~18px）：左侧目录还要占 280~340px，
小窗口下 15px 在可读性与信息密度之间更平衡。
`hh.exe` 的视口宽度 = 窗口宽 − 左侧导航宽度。

> 固定版式无阈值可校准：任何窗口下都是同一套 CSS。

## 3. 命令

```bash
./build.sh                                  # 默认：正文 15px 固定版式 + 导航 10pt
./build.sh --body-font-size 16 --nav-font-size 11   # 正文基准/导航整体放大一档
./build.sh --body-font-size 12              # 需要更密的信息量时
```

`--adaptive-zoom` / `--no-adaptive-zoom` / `--adaptive-font` /
`--no-adaptive-font` 为兼容参数，传入后忽略（窗口自适应已移除）。

直接调用 `build_chm.py` 时参数相同：

```bash
.venv/bin/python tools/build_chm.py \
  --repo repos/docs-cn --out dist/tidb-docs-cn --all --lang zh \
  --compiler chmcmd --search fulltext \
  --body-font-size 15 --nav-font-size 10
```

允许范围：`--body-font-size` 为 12~24，`--nav-font-size` 为 8~14，超范围直接以
非 0 退出，不会生成"参数写错但产物照出"的 CHM。

## 4. 落地位置（两条后端一致）

| 位置 | 内容 |
| --- | --- |
| `style.css` | `body{font-size:15px}` 固定基准 + 全部相对字号，无任何媒体查询/zoom 分档 |
| `docs.hhp` / `docs.chmcmd.hhp` | `[OPTIONS]` 里 `Default Font=Microsoft YaHei,10,134` |
| `/#SYSTEM` 记录 16 | `Default Font` 同一取值（chmcmd 原样透传，builtin 由 `ChmWriter` 写入） |
| `preview.html` | 侧栏 `font-size:10pt`（与 Windows 语义对齐）；正文在 iframe 里，同样触发分档 |

中文构建用 `Microsoft YaHei,<pt>,134`（134 = GB2312/CP936），英文构建用
`Segoe UI,<pt>,0`。构建日志会打印：

```text
[4/6] 生成直接打开兼容的 hhp / hhc
      Windows 二进制目录：启用
      Windows 全文搜索：启用（由 chmcmd 生成）
      正文版式：固定版式（基准 15px，版心 1120px 居中，不随窗口变化）
      Windows 导航字体：Microsoft YaHei,10,134
```

## 5. Windows 实机测试矩阵

| Windows | DPI | 分辨率 | 窗口 | 重点 |
| --- | ---: | --- | --- | --- |
| Windows 10 | 100% | 1920×1080 | 默认尺寸 | 基准 15px，版心居中 |
| Windows 10 | 125% | 1920×1080 | 默认尺寸 | 常见办公环境 |
| Windows 11 | 125% | 1920×1080 | 最大化 | 版心居中、两侧对称 |
| Windows 11 | 150% | 2560×1440 | 最大化 | 高 DPI 下版式不变 |
| x86 / 32 位 `hh.exe` 目标机 | 实际值 | 实际值 | 两种 | 真实投放环境 |

检查清单：

```text
[ ] Contents 导航不再明显偏小
[ ] Search 页签字体与 Contents 一致
[ ] 正文无需浏览器缩放即可阅读
[ ] 默认尺寸窗口 → 最大化，版式不变、两侧留白始终对称
[ ] 全程无横向滚动条、无右侧裁剪
[ ] 正文与导航字号比例协调
[ ] 代码块没有小得明显
[ ] 表格没有比正文缩小过度
[ ] 长 SQL 仍能容纳（代码块内横向滚动）
[ ] 最大化窗口正文没有超长行（版心 1120px 居中）
[ ] 小窗口下正文流式占满、无横向撑开
[ ] 125% / 150% DPI 下没有字体裁切
[ ] 中文微软雅黑正常
```

## 6. 明确不做的方案

```text
❌ 按窗口宽度分档改 font-size（只放大文字，图片/表格/间距比例走样）
❌ 按窗口宽度分档改 body zoom（把两侧 auto 边距一起放大，右侧被裁、两侧留白不对称）
❌ JavaScript 自动缩放正文（CHM 里脚本受限，且没有回退保障）
❌ 按 window.innerWidth 用 JS 改 zoom / font-size
❌ CSS vw / clamp（clamp 在 MSHTML 里根本不支持；vw 依赖文档模式，风险最高）
❌ 让左侧导航树随窗口缩放（原生控件，做不到）
❌ 只改 preview.html 或只改 builtin 一条后端
❌ 给 toc.hhc 加 CSS 企图控制 Windows 导航树
❌ 修改 Windows 注册表的全局 hh.exe 字号
❌ 默认把正文直接提到 17~18px（固定 15px 兼顾密度与可读性，不够用时用 --body-font-size 调）
```

## 7. 本机证据与仍未闭环

本机（macOS + FPC 3.2.2 + Python builtin writer）已核对：

- `build_css(15)` 输出 `font-size:15px` 固定基准，标题/代码/表格全部为 `em`，
  不再出现写死的标题 px 字号，也没有 `clamp`/`vw`；
- `build_css(15)` 与 `build_css(15, adaptive=False)` 输出一致，均不含任何
  `@media` 与 `zoom:`，废弃的四个自适应函数一律返回空；
- 预览页正文走 iframe + 同一份 `style.css`；
- `build_hhp()` 在 `[OPTIONS]` 写出 `Default Font=Microsoft YaHei,10,134`，
  且不改动 `[WINDOWS]` / Search 页签位（两者正交）；
- `chmcmd` 会把该行原样透传到 `/#SYSTEM` 记录 16（实测解包核对，
  `system_default_font()` 读回 `Microsoft YaHei,10,134`）；builtin writer
  写出的记录 16 同值；构建校验会比对"参数 vs 产物"，不一致即失败；
- `verify_chm.py` 报告固定版式，检出任何 `@media` 自适应分档或 `zoom:`
  直接判失败，便于实机排查。

仍需 Windows 实机确认：导航树实际观感、`Microsoft YaHei` 是否命中（未安装时
Windows 会回退）、第三段字符集在不同 HTML Help 编译器里的历史差异、
125% / 150% 缩放下是否出现裁切；以及大/小窗口下版心居中是否对称、有无裁剪。
