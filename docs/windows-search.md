# Windows"搜索"页签：成因、实现与实机验收

> 面向 Windows 用户与维护者。解决的问题：`.chm` 在 Windows `hh.exe` 里只有
> "目录（Contents）"，左侧没有"搜索（Search）"页签。
>
> 代码位置：`tools/build_chm.py`（`build_hhp()`、`resolve_build_features()`）、
> `tools/chmwriter.py`（`detect_*` / `system_fulltext_search_flag()` /
> `windows_search_tab_enabled()`）、`tools/verify_chm.py`（`--expect-search`）。
> 记录日期：2026-09-15。

## 1. 为什么会没有"搜索"页签

早期版本在 `.hhp` 里写死了两行，等于主动关掉了全文搜索：

```ini
Binary Index=No
Full-text search=No
```

后来发现这只解释了一半。`hh.exe` 的左侧导航窗格**有哪些页签，是由 CHM 内的
窗口定义（`/#WINDOWS`，来自 `.hhp` 的 `[WINDOWS]` 段）决定的**：

- "搜索"页签对应窗口定义里 `fsWinProperties` 的 `HHWIN_PROP_TAB_SEARCH`（`0x400`）；
- 没有 `[WINDOWS]` 段时，查看器退回内置默认窗口（只有目录），
  **即使 CHM 里已经有 `/$FIftiMain` 全文搜索库也不会出现"搜索"页签**。

所以"搜索"页签需要三个条件同时成立：

| # | 条件 | 谁负责 | 校验方式 |
| --- | --- | --- | --- |
| 1 | CHM 内有全文搜索库 `/$FIftiMain` | `.hhp` 的 `Full-text search=Yes`，由 `chmcmd` 编译生成 | `--search` / `--expect-search yes` |
| 2 | `/#SYSTEM` 记录 4 的"全文搜索开启"标志置位 | 编译器自动写 | `system_fulltext_search_flag()` |
| 3 | `/#WINDOWS` 的导航窗格样式含 `0x400` | `.hhp` 的 `[WINDOWS]` 段 | `windows_search_tab_enabled()` |

第 3 条是早期实现真正漏掉的一条：当时刻意不写 `[WINDOWS]`（为了"最小工程文件"
和第三方阅读器侧栏干净），而这正好也去掉了"搜索"页签。

## 2. 本项目现在的行为

生成的工程文件固定包含：

```ini
[OPTIONS]
...
Default window=main
Binary Index=No
Full-text search=Yes

[WINDOWS]
main="TiDB 中文文档","toc.hhc","","index.html","index.html",,,,,0x63520,,0x384E,,,,,,,,0
```

- `0x63520` = 三窗格 + 自动同步 + **搜索** + 收藏 + 可改标题 + 增强搜索
  （HTML Help Workshop 的默认值）；关掉全文搜索时用 `0x63120`（不含搜索位）。
- `0x384E` = 工具栏按钮：折叠展开、后退、前进、主页、同步、选项、打印。
- 关键词索引（`.hhk`、`Binary Index`）仍然关闭：它对应"索引"页签而不是
  "搜索"页签，而且第三方阅读器会把索引条目平铺进目录树。

命令行开关：

```bash
./build.sh --search=auto      # 默认：装了 chmcmd 就生成全文搜索，否则关闭并提示
./build.sh --search=fulltext  # 强约束：必须用 chmcmd 生成，否则构建失败
./build.sh --no-search        # 关闭全文搜索（体积更小）
```

`--search fulltext` 故意不允许静默降级：缺 `chmcmd` 时构建以非 0 退出，
避免"构建成功但功能缺失"。构建日志与 `verify_chm.py --expect-search yes`
都会明确报出三要件是否齐全。

## 3. 中文搜索的限制（重要）

`chmcmd` 的全文索引器来自 Free Pascal，它把**除 ASCII `a-z 0-9 _` 以外的所有字节
都当分隔符**（`htmlindexer.pas` 的词字符集合里没有 DBCS 处理，`#SYSTEM` 里的
DBCS 标志恒为 0，索引头里的代码页/语言 ID 恒为 cp1252/1033）。

实测结论（`tools/test_chm_search.py` 每次都会复核）：

- 索引里能查到 `TiDB`、`TiKV`、`TiFlash`、`raftstore`、`learner` 这类 ASCII 词；
- 中文/日文一个词也进不了索引，因此**输入"执行计划""慢查询""备份恢复"不会有结果**。

TiDB 文档的关键检索词有相当一部分是 ASCII（`region is unavailable`、
`raftstore.store-pool-size`、`tidb_mem_quota_query`、`TiFlash learner`、
`EXPLAIN`……），所以带搜索页签的 chmcmd 产物仍然有用；但要中文关键词可用，
只有微软自己的编译器能做到：

```bat
:: Windows 上、已安装 HTML Help Workshop
:: 先用 ./build.sh --keep-html 保留 HTML 正文与工程文件，再在产物目录里执行
hhc.exe docs.hhp
```

默认构建**不会**保留 HTML 与工程文件：`./build.sh` 仍然只产出
`dist/tidb-docs-cn/`、`dist/tidb-docs-cn-images/` 两个目录里的 CHM（输出位置与
以前完全一致）。`--keep-html` 只是需要重编时才临时加的一次性参数，它会在同一个
输出目录里额外留下 HTML 正文、`docs.hhp`、`toc.hhc`，重编完可以删掉。

`hhc.exe` 会重新生成 `docs.hhp`/`toc.hhc` 声明的全部内容，包括它自己的全文索引
（支持中日韩）与 `/#IDXHDR` 等微软产物专有结构。

## 4. Windows 实机验收清单

```text
[ ] Windows 10 x64 / Windows 11 x64（32 位 hh.exe 场景也各测一次）
[ ] 文件从"下载"目录复制到本地磁盘，必要时属性里"解除锁定"（Unblock-File）
[ ] 双击打开，Contents 目录树正常、层级正确
[ ] 左侧出现"搜索"页签
[ ] 搜索 TiFlash 有结果
[ ] 搜索 TiKV 有结果
[ ] 搜索 raftstore 有结果
[ ] 搜索 tiDB（大小写混合）有结果
[ ] 搜索"执行计划"：记录实际行为（预期 chmcmd 产物无结果，hhc.exe 产物有结果）
[ ] 点击搜索结果能打开对应正文
[ ] 中文标题不乱码，页面内链正常
[ ] 用 hhc.exe 重编后的 CHM 再重复一遍上面各项
```

判定"搜索可用"不能只看页签：要能输入关键字、返回结果、点击结果能打开正文。

## 5. 仍然看不到"搜索"时的排查顺序

1. **先看结构**：`.venv/bin/python tools/verify_chm.py --expect-search yes <chm>`。
   三要件任一为否（`[失败]` 行）说明构建产物本身缺东西。
2. **确认用的是哪个打包器**：构建日志里的
   `Windows 全文搜索：启用（由 chmcmd 生成）`。内置（未压缩）打包器不支持全文
   搜索，它的产物不会有"搜索"页签；用 `--keep-html` 保留 HTML 与
   `docs.hhp`，在 Windows 上用 `hhc.exe` 重编即可。
3. **确认窗口缓存**：`hh.exe` 会把窗口尺寸/页签选择记在
   `%USERPROFILE%\AppData\Local\Microsoft\Help\Hh.dat`。页签异常时关掉所有帮助
   窗口、删除 `Hh.dat` 再打开。
4. **确认没有被拦**：微软的"文件来源标记"会阻止打开；先 `Unblock-File` 或复制到
   本地磁盘。
5. **仍然没有**：用第 3 节的 `hhc.exe docs.hhp` 重编一次。微软编译器的产物同时带
   `/#IDXHDR`、DBCS 全文索引等 chmcmd 不写的结构，是"完全对齐微软产物"的路径。
6. 还想在 Windows 侧多一个"索引"页签（关键词索引）时，才需要生成 `.hhk` 并打开
   `Binary Index=Yes`；这会重新引入第三方阅读器把索引条目平铺进目录树的问题，
   因此没有默认开启。

## 6. 参照证据

本机对三份 CHM 的内部流做了逐字段比对（`chmls list` / `chmls printsystem`）：

| CHM | 来源 | `/$FIftiMain` | `/#IDXHDR` | `/#WINDOWS` | 导航窗格样式 |
| --- | --- | --- | --- | --- | --- |
| TiDB 官方 7.5 中文 | 微软 `hhc.exe` | 有 | 有 | 无 | 退回默认窗口 |
| GaussDB 产品文档 | 微软 `hhc.exe` | 有 | 有 | 有（204 B） | `0x62520`（含 `0x400`） |
| 本项目（旧版） | `chmcmd` | 有 | 无 | 无 | 退回默认窗口 |
| 本项目（本次修复后） | `chmcmd` | 有 | 无 | 有（204 B） | `0x63520`（含 `0x400`） |

`/#WINDOWS` 的条目长度、`fsValidMembers`（`0x536`）等字段与微软产物一致。
`HHWIN_PROP_TAB_SEARCH = 0x400` 取自 Microsoft HTML Help SDK 的 `htmlhelp.h`；
Wine 的 `hhctrl.ocx` 重实现同样只在 `fsWinProperties & HHWIN_PROP_TAB_SEARCH`
时才创建搜索页签（`dlls/hhctrl.ocx/help.c`），无窗口定义时默认值里不含该位。

仍然需要 Windows 实机确认的是：`hh.exe` 是否接受 `chmcmd` 生成的
`/$FIftiMain`（微软侧无公开文档、Free Pascal 侧无实机记录）。本文第 4 节清单
就是为此准备的；一旦实机结论与预期不同，第 5 节给出了逐级兜底方案。
