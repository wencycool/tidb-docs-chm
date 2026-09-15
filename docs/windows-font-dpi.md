# Windows 字号 / DPI 适配

> 面向 Windows 用户与维护者。解决的问题：CHM 在 Windows（尤其 1080p、125% /
> 150% 缩放、x86 `hh.exe`）里左侧导航与正文整体偏小。
>
> 代码位置：`tools/build_chm.py`（`CSS`/`build_css()`、`RenderOptions`、
> `default_chm_font()`、`build_hhp()`、`build_preview()`）、
> `tools/chmwriter.py`（`system_default_font()`、`/#SYSTEM` 记录 16）。
> 记录日期：2026-09-15。

## 1. 两条通路必须分开

```text
┌──────────────────── hh.exe 窗口 ────────────────────┐
│  Contents / Search          │  正文 HTML 页面        │
│  Windows 原生控件            │  MSHTML 渲染            │
│  ↑ Default Font              │  ↑ style.css            │
│    （/#SYSTEM 记录 16）      │    （body 基准字号 + em）│
└─────────────────────────────┴────────────────────────┘
```

- **左侧导航**（目录树、搜索结果列表）是 Windows 原生控件，**正文 CSS 管不到它**，
  只能通过 CHM 的 `Default Font`（`.hhp` 的 `Default Font=`，落到 `/#SYSTEM`
  记录 16）指定"字体名,点数,字符集"。
- **右侧正文**是 HTML，由 `style.css` 的基准字号加 em 相对字号控制。

因此本项目**不做**"按窗口宽度动态缩放字体"这类方案：CHM 跑在 `hh.exe`/MSHTML 里，
正确策略是"自适应 Windows DPI"，而不是"自适应窗口字号"。

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
| Windows 导航 | 系统默认 | **10pt**（`--nav-font-size`） |
| 预览页侧栏 | 13.5px 写死 | **10pt**（跟随 `--nav-font-size`） |

正文没有直接提到 16px：CHM 左侧还要占 280~340px，15px 在"可读性"和"信息密度"
之间更平衡。基准字号是唯一变量，标题/代码/表格都是相对它的 `em`，
以后整体调整不会破坏比例。`.page{max-width:1120px}` 保持不变。

## 3. 命令

```bash
./build.sh                                  # 默认：正文 15px + 导航 10pt
./build.sh --body-font-size 16 --nav-font-size 11   # 内网机器仍偏小时放大一档
./build.sh --body-font-size 12              # 需要更密的信息量时
```

直接调用 `build_chm.py` 时参数相同：

```bash
.venv/bin/python tools/build_chm.py \
  --repo repos/docs-cn --out dist/tidb-docs-cn --all --lang zh \
  --compiler chmcmd --search fulltext \
  --body-font-size 15 --nav-font-size 10
```

允许范围：`--body-font-size` 为 12~20，`--nav-font-size` 为 8~14，超范围直接以
非 0 退出，不会生成"参数写错但产物照出"的 CHM。

## 4. 落地位置（两条后端一致）

| 位置 | 内容 |
| --- | --- |
| `style.css` | `body{font-size:15px}` + 全部相对字号 |
| `docs.hhp` / `docs.chmcmd.hhp` | `[OPTIONS]` 里 `Default Font=Microsoft YaHei,10,134` |
| `/#SYSTEM` 记录 16 | `Default Font` 同一取值（chmcmd 原样透传，builtin 由 `ChmWriter` 写入） |
| `preview.html` | 侧栏 `font-size:10pt`（与 Windows 语义对齐） |

中文构建用 `Microsoft YaHei,<pt>,134`（134 = GB2312/CP936），英文构建用
`Segoe UI,<pt>,0`。构建日志会打印：

```text
[4/6] 生成直接打开兼容的 hhp / hhc
      Windows 二进制目录：启用
      Windows 全文搜索：启用（由 chmcmd 生成）
      正文字号：15px（标题/代码/表格按 em 相对缩放）
      Windows 导航字体：Microsoft YaHei,10,134
```

## 5. Windows 实机测试矩阵

| Windows | DPI | 分辨率 | 重点 |
| --- | ---: | --- | --- |
| Windows 10 | 100% | 1920×1080 | 基准 |
| Windows 10 | 125% | 1920×1080 | 常见办公环境 |
| Windows 11 | 125% | 1920×1080 | 主验收 |
| Windows 11 | 150% | 2560×1440 | 高 DPI |
| x86 / 32 位 `hh.exe` 目标机 | 实际值 | 实际值 | 真实投放环境 |

检查清单：

```text
[ ] Contents 导航不再明显偏小
[ ] Search 页签字体与 Contents 一致
[ ] 正文无需浏览器缩放即可阅读
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
❌ JavaScript 自动缩放正文
❌ 按 window.innerWidth 改 font-size
❌ CSS vw / clamp
❌ 只改 preview.html 或只改 builtin 一条后端
❌ 给 toc.hhc 加 CSS 企图控制 Windows 导航树
❌ 修改 Windows 注册表的全局 hh.exe 字号
❌ 默认把正文直接提到 17~18px
```

## 7. 本机证据与仍未闭环

本机（macOS + FPC 3.2.2 + Python builtin writer）已核对：

- `build_css(15)` 输出 `font-size:15px` 基准，标题/代码/表格全部为 `em`，
  不再出现写死的标题 px 字号，也没有 `clamp`/`vw`；
- `build_hhp()` 在 `[OPTIONS]` 写出 `Default Font=Microsoft YaHei,10,134`，
  且不改动 `[WINDOWS]` / Search 页签位（两者正交）；
- `chmcmd` 会把该行原样透传到 `/#SYSTEM` 记录 16（实测解包核对，
  `system_default_font()` 读回 `Microsoft YaHei,10,134`）；builtin writer
  写出的记录 16 同值；构建校验会比对"参数 vs 产物"，不一致即失败；
- `verify_chm.py` 会打印正文基准字号与导航字体，便于实机排查。

仍需 Windows 实机确认：导航树实际观感、`Microsoft YaHei` 是否命中（未安装时
Windows 会回退）、第三段字符集在不同 HTML Help 编译器里的历史差异，以及
125% / 150% 缩放下是否出现裁切。字体名与点数生效是本阶段的硬目标。
