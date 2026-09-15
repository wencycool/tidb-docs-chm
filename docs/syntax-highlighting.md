# 代码块语法高亮（构建期静态实现）

本文说明 CHM 里 SQL / Shell / TOML / YAML / JSON / Go / Python 等 fenced code block
的语法高亮是怎么做的、为什么这样做，以及在 TiDB 文档上的边界。

---

## 1. 为什么以前没有高亮

`tools/build_chm.py` 的 `convert_fences()` 一直能识别围栏语言，但它只把语言写成
CSS class：

```markdown
```sql
SELECT * FROM t WHERE id = 1;
```
```

```html
<pre><code class="language-sql">SELECT * FROM t WHERE id = 1;</code></pre>
```

`language-sql` 只是一个 class。CHM 的目标运行时是 Windows `hh.exe`（MSHTML 引擎）：
它只做 HTML/CSS 渲染，不会去解析 SQL 再着色，所以代码块里的文字全部同色是正常现象。

## 2. 方案：构建期静态高亮，不引入运行时

```text
docs-cn Markdown
       ↓
convert_fences()            ← 保留原有 fence 扫描与占位符架构
       ↓
raw code + fenced language
       ↓
highlight_code()            ← Pygments，HtmlFormatter(nowrap=True)
       ↓
静态 <span class="k">…</span> token
       ↓
%%CHM-CODE-n%% 占位符
       ↓
Python-Markdown
       ↓
restore_code_blocks()
       ↓
style.css（.highlight 浅色主题）
       ↓
UTF-8 HTML（正文带 BOM）
       ↓
chmcmd / 内置打包器
       ↓
Windows hh.exe
```

最终 HTML 结构：

```html
<pre class="highlight"><code class="language-sql"><span class="k">SELECT</span> *
<span class="k">FROM</span> t
<span class="k">WHERE</span> id = <span class="mi">1</span>;
</code></pre>
```

要点：

- **没有任何运行时依赖**：CHM 里不含 JS、不引用 CDN，也不加载任何高亮脚本，只有普通
  `<span>` 与 CSS 规则；
- **Pygments 只是构建依赖**：只在本机构建时把代码展开成静态 token，产物里没有它；
- **不改现有 fence 架构**：列表项、嵌套列表、引用块里的围栏依旧走
  `convert_fences() → CODE_TOKEN → Markdown → restore_code_blocks()`，这次只在
  `code_blocks.append()` 之前加了一层高亮；
- **外层标签由项目控制**：`HtmlFormatter(nowrap=True)` 让 Pygments 只输出内部 token，
  `<pre class="highlight"><code class="language-xxx">` 仍由 `build_chm.py` 生成，
  于是 `pre` 的字号、背景、边框、滚动条规则完全沿用原有版式。

不选 `highlight.js` / `Prism.js`：它们是运行时 JS，在 `hh.exe` 的 Local Machine Zone
里既有安全区/加载风险，也会让"离线单文件"这个前提变复杂。

## 3. 支持的语言

只信 Markdown 的 fenced language，**不做任何自动语言检测**：执行计划、日志里出现
`SELECT` 也不能猜成 SQL。

`build_chm.py` 里维护一张很小的别名表，其余名字原样交给 Pygments（它自己支持的语言
远多于此，不需要白名单）：

| fenced language | 实际使用的 lexer |
| --- | --- |
| `sh`、`shell` | `bash` |
| `yml` | `yaml` |
| `plaintext`、`txt` | `text`（不高亮） |
| `console` | `text`（不高亮） |
| `text`、`plain`、`none`、无语言 | 保持纯文本块 |
| 其他（`sql`、`mysql`、`toml`、`json`、`go`、`python` …） | 原样交给 Pygments，例如 `mysql` 用的是 Pygments 自己的 MySQL lexer |

首版重点验证语言：`sql`、`mysql`、`bash`、`shell`、`sh`、`toml`、`yaml`、`yml`、
`json`、`go`、`python`、`text`、`console`。

## 4. fallback 策略（任何一步失败都退回纯文本）

`highlight_code()` 的失败链是逐层兜底的，**单个代码块出问题只会让它自己不高亮，
绝不会让整份 CHM 构建失败**：

| 情况 | 结果 |
| --- | --- |
| 未安装 Pygments（直接跑 `tools/build_chm.py`） | 退化为原来的 `<pre><code class="language-xxx">`，构建照常完成 |
| 语言为空 / `text` / `plain` / `none` | 纯文本块 |
| Pygments 认不出这个语言（`ClassNotFound`） | 纯文本块，并计入构建日志的"未高亮语言" |
| lexer 抛异常 | 纯文本块 |
| 高亮结果与原文的可见文本不一致 | 纯文本块（见下一节） |

真实文档里的未高亮语言是正常现象，例如 `ebnf+diagram`、`railroad+diagram`、
`mermaid`、`dotenv`、`log`、`prisma`、`gradle`、`csv`、`haproxy`：Pygments 没有对应
lexer，它们就保持纯文本。构建日志会把分布打出来：

```text
代码块转换 8778 个：语法高亮 6713 个，纯文本 2065 个
未高亮语言 9 类：ebnf+diagram=163、dotenv=72、log=24、railroad+diagram=15、
mermaid=11、prisma=6、gradle=4、csv=3、haproxy=2
```

（统计键：`code_highlighted` / `code_plain` / `code_unknown_lang`。）

## 5. 高亮只能改外观，不能改内容

这是本功能最重要的一条约束，实现上有三道保险：

1. `get_lexer_by_name(language, stripnl=False, ensurenl=False)`：Pygments 的 lexer
   默认会 `stripnl` / `ensurenl`（悄悄增删首尾换行），关掉它们，代码块前面的空行、
   结尾的空行都原样保留；
2. `HtmlFormatter` 总会给最后一行补一个行终止符，原代码不以换行结束时把这**一个**
   换行去掉；
3. 最后再剥掉标签、反转义，核对"可见文本 == 原始代码"。只要不等就放弃高亮。

于是"高亮前后的可见 code text 完全一致"是硬约束，而不是依赖某个 lexer 的默认行为：

- `tools/test_render.py` 对每种重点语言逐字节比对；
- 全量扫描会对 `repos/docs-cn` 里的**每一个**代码块做同样比对（当前 1239 篇文档 /
  8778 个代码块，不一致 0 个）。

HTML 特殊字符同理：`<` `>` `&` 由 Pygments/`html.escape` 正常转义，
`SELECT '<tag>', a < 10, b > 5` 这类代码在 CHM 里的可见文本与原文完全一致，
也不会出现二次转义（`&amp;lt;`）。

## 6. TiDB 专有 SQL 的边界

验收目标不是"Pygments 完全懂 TiDB 方言"，而是下面四条：

```text
不能改变文本
不能删除 token
不能破坏 Hint
不能产生刺眼 Error 红底
```

- `ADMIN SHOW DDL JOBS;`、`ADMIN CHECK TABLE t;`、`ADMIN RECOVER INDEX t idx;`、
  `SHOW STATS_HEALTHY;`、`SHOW STATS_META;`、`SHOW PLACEMENT;` 在 Pygments 的 SQL
  lexer 下能正常着色，不产生 Error token；
- Optimizer Hint `/*+ HASH_JOIN(t1, t2) */` 被当作注释 token 整段保留，原文完整；
- 即使将来某个 lexer 把 TiDB 扩展语法判成 `err`，CSS 里也强制中性：

```css
.highlight .err{color:inherit;background:transparent}
```

Pygments 默认的 Error 样式是红底，在整页文档里非常刺眼，所以这里明确覆盖掉。

## 7. CHM / MSHTML 兼容原则

- token 样式**全部限定在 `.highlight` 作用域**，不影响正文行内 `code`，也不会和文档
  里其它 `<span>` 冲突；
- 只用普通类选择器，不用 flex/grid/var/clamp/vw，不改变 `pre` 的字号、背景、边框；
- 高亮在 Markdown → HTML 阶段完成，**不进 `page_bytes()`**、不碰
  `CHM_TITLE_ENCODINGS`、不改 `[WINDOWS]` 搜索页签位、不改 `BuildFeatures`、
  不动 `strip_inline_code()`；
- 正文仍然是 UTF-8 HTML + BOM；代码块正文绝不转 GBK（GBK/ANSI 只服务于 CHM 的
  标题、目录等 Windows 原生结构）；
- Search：静态高亮只是多了 `<span>` 标签，不影响 FPC 的全文索引（见下）。

## 8. 测试方法

```bash
# 渲染用例 + repos/docs-cn 全量扫描（含"每个代码块可见文本 == 原码"）
make test

# 只跑用例
.venv/bin/python tools/test_render.py --fast

# chmcmd 全文搜索集成测试
make test-search
```

`tools/test_render.py` 里新增的高亮用例覆盖：

- SQL 关键字 / 字符串 / 数字 / 注释 / 运算符 token 都能区分；
- HTML 特殊字符（`'<tag>'`、`a < 10`、`b > 5`、`'a&b'`）可见文本不变；
- 列表项内、列表项内引用块里的 SQL 高亮块仍然留在 `<li>` / `<blockquote>` 里，
  不会多出 `<p><pre>` 非法嵌套；
- 未知语言、无语言、`text` / `plain` / `console` 一律回落纯文本（内容里出现
  `SELECT FROM WHERE` 也不高亮）；
- TiDB 专有 SQL 与 Hint 原文完整、`.err` 中性；
- 语言别名归一化与 `code_highlighted` / `code_plain` / `code_unknown_lang` 统计；
- 模拟"未安装 Pygments"与"lexer 改动了内容"两种退化路径。

`tools/test_chm_search.py` 用**同一份可见文本**编译两版 CHM（一版带高亮 span、一版
纯文本块），断言两份 `/$FIftiMain` **逐字节相同**，并确认代码块里的标记词确实进了
FPC 词表——即 span 没有让代码内容从全文搜索里消失。

## 9. 维护提示

- 新增语言：什么都不用做，直接用 ` ```language ` 即可；Pygments 不认的名字会安全
  回落纯文本，并出现在构建日志的"未高亮语言"列表里。若某个语言有等价写法，加进
  `tools/build_chm.py` 的 `CODE_LANG_ALIASES`。
- 新增 token 类型：在 `tools/build_chm.py` 的 `CSS` 里补一条 `.highlight .xx{…}`，
  不要放宽到全局选择器。
- `build.sh` 会自动安装 `pygments`；手动调用 `tools/build_chm.py` 时若没装，
  构建不会失败，只是代码块退化为纯文本（构建日志会提示）。
