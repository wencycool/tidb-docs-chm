# Windows 字号 / DPI 适配

> 面向 Windows 用户与维护者。解决的问题：CHM 在 Windows（尤其 1080p、125% /
> 150% 缩放、x86 `hh.exe`）里左侧导航与正文整体偏小，以及正文不随窗口变化。
>
> 代码位置：`tools/build_chm.py`（`CSS`/`build_css()`/`adaptive_font_css()`、
> `ADAPTIVE_FONT_STEPS`、`RenderOptions`、`default_chm_font()`、`build_hhp()`、
> `build_preview()`）、`tools/chmwriter.py`（`system_default_font()`、
> `/#SYSTEM` 记录 16）。
> 记录日期：2026-09-15（2026-09-15 追加"正文窗口自适应"）。

## 1. 三条通路必须分开

```text
┌──────────────────── hh.exe 窗口 ────────────────────┐
│  Contents / Search          │  正文 HTML 页面        │
│  Windows 原生控件            │  MSHTML 渲染            │
│  ↑ Default Font              │  ↑ style.css            │
│    （/#SYSTEM 记录 16）      │    基准字号 + em        │
│    固定 pt，不随窗口缩放       │    + 媒体查询分档        │
└─────────────────────────────┴────────────────────────┘
```

- **左侧导航**（目录树、搜索结果列表）是 Windows 原生控件，**正文 CSS 管不到它**，
  只能通过 CHM 的 `Default Font`（`.hhp` 的 `Default Font=`，落到 `/#SYSTEM`
  记录 16）指定"字体名,点数,字符集"。它只有一个固定 pt，**不可能随窗口缩放**。
- **右侧正文**是 HTML，由 `style.css` 的基准字号加 em 相对字号控制。
- **正文的窗口自适应**用 CSS 媒体查询分档放大基准字号：字号是 em 体系，所以只改
  `body` 一个字，标题/代码/表格整页等比放大。不用 JS、`vw`、`clamp()`——`hh.exe`
  里跑的是 MSHTML，媒体查询在 IE9+ 标准模式可用，旧文档模式会整段忽略、
  自动回落到基准字号，属于安全降级。

## 2. 默认视觉规格

| 区域 | 旧值 | 现在 |
| --- | --- | --- |
| 正文基准 | 14px | **15px**（`--body-font-size`） |
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

正文按窗口宽度分档放大（`--adaptive-font`，默认开启）：

| 正文区宽度 | <800 | ≥800 | ≥920 | ≥1040 | ≥1160 | ≥1280 | ≥1400 | ≥1520 | ≥1640 | ≥1760 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 基准字号 | 15px | 16px | 17px | 18px | 19px | 20px | 21px | 22px | 23px | 24px |

即 `ADAPTIVE_FONT_START_WIDTH=800`、`ADAPTIVE_FONT_STEP_WIDTH=120`、
`ADAPTIVE_FONT_MAX_DELTA=9`（`ADAPTIVE_FONT_STEPS` 由三者生成）；字号封顶在
`BODY_FONT_SIZE_RANGE` 上限（24px）；基准本身取到上限时不再写媒体查询。
`.page{max-width:1120px}` 保持不变：窗口再宽也只放大字号，行宽（字符数）反而更舒服——
15px 时约 70 字/行，24px 时约 44 字/行。

正文基准仍是 15px（而不是直接 17~18px）：左侧目录还要占 280~340px，
小窗口下 15px 在可读性与信息密度之间更平衡，窗口变大时再逐档放大。
`hh.exe` 的视口宽度 = 窗口宽 − 左侧导航宽度，所以分档触发点略晚于窗口尺寸。

> 阈值是实测校准过的：100% 缩放、2724px 宽的窗口里正文区约 1900px，
> 正文取到最高档 24px（截图逐像素测量：行距 33px ÷ 行高 1.70 ≈ 19px 时是旧上限
> 20px 档；正文栏 1055px ≈ `.page` 的 1056px 内容宽，证实系统缩放为 100%）。
> 屏幕更宽 / 缩放更高时，改这三个常量即可，测试跟着常量走。

## 3. 命令

```bash
./build.sh                                  # 默认：正文 15px 起、窗口自适应开启 + 导航 10pt
./build.sh --no-adaptive-font               # 关掉窗口自适应，正文恒定 15px
./build.sh --body-font-size 16 --nav-font-size 11   # 内网机器仍偏小时整体放大一档
./build.sh --body-font-size 12              # 需要更密的信息量时
```

直接调用 `build_chm.py` 时参数相同：

```bash
.venv/bin/python tools/build_chm.py \
  --repo repos/docs-cn --out dist/tidb-docs-cn --all --lang zh \
  --compiler chmcmd --search fulltext \
  --body-font-size 15 --nav-font-size 10 --adaptive-font
```

允许范围：`--body-font-size` 为 12~24，`--nav-font-size` 为 8~14，超范围直接以
非 0 退出，不会生成"参数写错但产物照出"的 CHM。

## 4. 落地位置（两条后端一致）

| 位置 | 内容 |
| --- | --- |
| `style.css` | `body{font-size:15px}` + 全部相对字号 + 5 条窗口自适应媒体查询 |
| `docs.hhp` / `docs.chmcmd.hhp` | `[OPTIONS]` 里 `Default Font=Microsoft YaHei,10,134` |
| `/#SYSTEM` 记录 16 | `Default Font` 同一取值（chmcmd 原样透传，builtin 由 `ChmWriter` 写入） |
| `preview.html` | 侧栏 `font-size:10pt`（与 Windows 语义对齐）；正文在 iframe 里，同样触发分档 |

中文构建用 `Microsoft YaHei,<pt>,134`（134 = GB2312/CP936），英文构建用
`Segoe UI,<pt>,0`。构建日志会打印：

```text
[4/6] 生成直接打开兼容的 hhp / hhc
      Windows 二进制目录：启用
      Windows 全文搜索：启用（由 chmcmd 生成）
      正文字号：15px（标题/代码/表格按 em 相对缩放，窗口自适应分档 16/17/18/19/20/21/22/23/24px）
      Windows 导航字体：Microsoft YaHei,10,134
```

## 5. Windows 实机测试矩阵

| Windows | DPI | 分辨率 | 窗口 | 重点 |
| --- | ---: | --- | --- | --- |
| Windows 10 | 100% | 1920×1080 | 默认尺寸 | 基准 15px |
| Windows 10 | 125% | 1920×1080 | 默认尺寸 | 常见办公环境 |
| Windows 11 | 125% | 1920×1080 | 最大化 | 分档放大 |
| Windows 11 | 150% | 2560×1440 | 最大化 | 高 DPI + 大字档 |
| Windows 10/11 | 100% | 2560×1440 及以上 | 最大化 | 正文区 ≥1760px → 24px 封顶 |
| x86 / 32 位 `hh.exe` 目标机 | 实际值 | 实际值 | 两种 | 真实投放环境 |

检查清单：

```text
[ ] Contents 导航不再明显偏小
[ ] Search 页签字体与 Contents 一致
[ ] 正文无需浏览器缩放即可阅读
[ ] 默认尺寸窗口 → 最大化，正文字号确实逐档变大（不是恒定）
[ ] 窗口从最大化拖回小尺寸，字号回落且不残留下拉条
[ ] 正文与导航字号比例协调
[ ] 代码块没有小得明显
[ ] 表格没有比正文缩小过度
[ ] 长 SQL 仍能容纳（代码块内横向滚动）
[ ] 最大化窗口正文没有超长行（.page 仍是 1120px 居中）
[ ] 小窗口没有因字号策略导致横向撑开
[ ] 125% / 150% DPI 下没有字体裁切
[ ] 中文微软雅黑正常
```

## 6. 明确不做的方案

```text
❌ JavaScript 自动缩放正文（CHM 里脚本受限，且没有回退保障）
❌ 按 window.innerWidth 用 JS 改 font-size
❌ CSS vw / clamp（clamp 在 MSHTML 里根本不支持；vw 依赖文档模式，风险最高）
❌ 让左侧导航树随窗口缩放（原生控件，做不到）
❌ 只改 preview.html 或只改 builtin 一条后端
❌ 给 toc.hhc 加 CSS 企图控制 Windows 导航树
❌ 修改 Windows 注册表的全局 hh.exe 字号
❌ 默认把正文直接提到 17~18px（改用"小窗口 15px + 分档放大"，兼顾密度与可读性）
```

## 7. 本机证据与仍未闭环

本机（macOS + FPC 3.2.2 + Python builtin writer）已核对：

- `build_css(15)` 输出 `font-size:15px` 基准，标题/代码/表格全部为 `em`，
  不再出现写死的标题 px 字号，也没有 `clamp`/`vw`；
- `build_css(15)` 还输出 9 条 `@media (min-width:...){body{font-size:...}}`
  （800/920/…/1760px → 16/17/…/24px），且**只**改 `body` 一个字；
  `build_css(15, adaptive=False)` 与 `build_css(24)` 都不含任何媒体查询；
- 预览页正文走 iframe + 同一份 `style.css`，本地拖动窗口即可复现分档；
- `build_hhp()` 在 `[OPTIONS]` 写出 `Default Font=Microsoft YaHei,10,134`，
  且不改动 `[WINDOWS]` / Search 页签位（两者正交）；
- `chmcmd` 会把该行原样透传到 `/#SYSTEM` 记录 16（实测解包核对，
  `system_default_font()` 读回 `Microsoft YaHei,10,134`）；builtin writer
  写出的记录 16 同值；构建校验会比对"参数 vs 产物"，不一致即失败；
- `verify_chm.py` 会打印正文基准字号、窗口自适应分档与导航字体，便于实机排查。

仍需 Windows 实机确认：导航树实际观感、`Microsoft YaHei` 是否命中（未安装时
Windows 会回退）、第三段字符集在不同 HTML Help 编译器里的历史差异、
125% / 150% 缩放下是否出现裁切。**媒体查询本身已在实机确认生效**（见第 2 节
引用的截图测量），剩下要校准的只是阈值是否匹配你的屏幕与窗口习惯。
