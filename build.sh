#!/usr/bin/env bash
#
# build.sh —— 一键生成 TiDB 中文离线文档（CHM）
#
# 用法:
#   ./build.sh                # 默认一次出两版：无图版 + 含图片版（均为压缩产物）
#   ./build.sh release-8.5    # 生成指定版本（分支名）
#   ./build.sh --images       # 只出含图片版（默认 compact 压缩档）
#   ./build.sh --no-images    # 只出无图版
#   ./build.sh --images --image-profile original   # 含图片但保留原图
#   ./build.sh --keep-html    # 额外保留 HTML 版与 hhc/hhp 工程文件
#   ./build.sh --no-compress  # 强制使用未压缩的内置打包器
#   ./build.sh --toc-mode hhc # 只写传统目录（Windows 侧无目录页签，便于排查侧栏）
#   ./build.sh --search fulltext  # 必须生成 Windows"搜索"页签（缺 chmcmd 就报错退出）
#   ./build.sh --no-search    # 不写全文搜索库（hh.exe 没有"搜索"页签）
#   ./build.sh --body-font-size 16 --nav-font-size 11  # 正文基准/左侧导航整体调大一号
#
# 产物默认只保留 CHM；HTML/工程文件构建成功后自动清理。
# 含图片版默认同时做两层压缩：图片 compact 档（宽≤1200 + PNG 256 色）+ CHM LZX。
# 默认优先用 FPC chmcmd 做 LZX 压缩；未安装时自动使用内置未压缩打包器。
# Windows"搜索"页签需要全文搜索库（chmcmd 生成）+ 窗口定义里的搜索页签位：
# --search auto（默认）在 chmcmd 可用时开启，否则关闭并打印提示；
# --search fulltext 是强约束，缺 chmcmd 直接失败，不会静默退化成没有搜索。
# 正文固定版式：style.css 基准字号（px，默认 15）+ em 相对字号，版心 1120px 居中，
# 不随窗口变化（窗口自适应已移除：--adaptive-zoom / --no-adaptive-zoom /
# --adaptive-font / --no-adaptive-font 保留为兼容参数，传入后忽略）；
# 导航是 Windows 原生控件，只能由 CHM 的 Default Font 给固定 pt，不随窗口缩放。
# 页脚日期取文档源码 HEAD 提交日期，可用 SOURCE_DATE_EPOCH 覆盖以保证可复现。
# 幂等可重复执行：venv、源码仓库、产物均自动准备/更新。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
REPO="$ROOT/repos/docs-cn"
DIST="$ROOT/dist"
VENV="$ROOT/.venv"
PY="$VENV/bin/python"

# ---------------------------------------------------------------- 0. 参数
REF="master"
BUILD_MODE="both"        # both=无图版+含图版；images=只出含图版；plain=只出无图版
HAS_PROFILE=0
PRUNE="chm"
COMPILER="auto"
TOC_MODE="binary"
SEARCH="auto"            # auto=chmcmd 可用时开启全文搜索；fulltext=强制；none=关闭
IMAGE_ARGS=()
FONT_ARGS=()             # --body-font-size / --nav-font-size 透传
while [ "$#" -gt 0 ]; do
    arg="$1"
    case "$arg" in
        --images|--keep-images)
            BUILD_MODE="images"
            ;;
        --no-images|--plain)
            BUILD_MODE="plain"
            ;;
        --compiler=*)
            COMPILER="${arg#--compiler=}"
            ;;
        --compiler)
            if [ "$#" -lt 2 ]; then
                echo "--compiler 需要 auto、builtin 或 chmcmd" >&2
                exit 2
            fi
            COMPILER="$2"
            shift
            ;;
        --toc-mode=*)
            TOC_MODE="${arg#--toc-mode=}"
            ;;
        --search=*)
            SEARCH="${arg#--search=}"
            ;;
        --search)
            if [ "$#" -lt 2 ]; then
                echo "--search 需要 auto、fulltext 或 none" >&2
                exit 2
            fi
            SEARCH="$2"
            shift
            ;;
        --no-search)
            SEARCH="none"
            ;;
        --toc-mode)
            if [ "$#" -lt 2 ]; then
                echo "--toc-mode 需要 binary 或 hhc" >&2
                exit 2
            fi
            TOC_MODE="$2"
            shift
            ;;
        --no-compress)
            COMPILER="builtin"
            ;;
        --compress)
            COMPILER="chmcmd"
            ;;
        --keep-html|--keep-all)
            PRUNE="none"
            ;;
        --keep-hhp)
            PRUNE="hhp"
            ;;
        --only-chm|--prune-chm)
            PRUNE="chm"
            ;;
        --image-profile=*)
            HAS_PROFILE=1
            IMAGE_ARGS+=("$arg")
            ;;
        --image-profile)
            if [ "$#" -lt 2 ]; then
                echo "--image-profile 需要 compact、tiny 或 original" >&2
                exit 2
            fi
            HAS_PROFILE=1
            IMAGE_ARGS+=("--image-profile=$2")
            shift
            ;;
        --image-max-width=*|--image-colors=*|--image-jpeg-quality=*)
            IMAGE_ARGS+=("$arg")
            ;;
        --body-font-size=*|--nav-font-size=*)
            FONT_ARGS+=("$arg")
            ;;
        --body-font-size|--nav-font-size)
            if [ "$#" -lt 2 ]; then
                echo "$arg 需要一个数值" >&2
                exit 2
            fi
            FONT_ARGS+=("$arg=$2")
            shift
            ;;
        --adaptive-zoom|--no-adaptive-zoom|--adaptive-font|--no-adaptive-font)
            FONT_ARGS+=("$arg")
            ;;
        --image-max-width|--image-colors|--image-jpeg-quality)
            if [ "$#" -lt 2 ]; then
                echo "$arg 需要一个数值" >&2
                exit 2
            fi
            IMAGE_ARGS+=("$arg=$2")
            shift
            ;;
        --*)
            echo "未知参数：${arg}（参数说明见 README.md）" >&2
            exit 2
            ;;
        *)
            REF="$arg"
            ;;
    esac
    shift
done

case "$COMPILER" in
    auto|builtin|chmcmd) ;;
    *) echo "无效打包器：$COMPILER（应为 auto、builtin 或 chmcmd）" >&2; exit 2 ;;
esac

case "$TOC_MODE" in
    binary|hhc) ;;
    *) echo "无效目录形态：$TOC_MODE（应为 binary 或 hhc）" >&2; exit 2 ;;
esac

case "$SEARCH" in
    auto|fulltext|none) ;;
    *) echo "无效搜索模式：$SEARCH（应为 auto、fulltext 或 none）" >&2; exit 2 ;;
esac

if [ "$SEARCH" = "fulltext" ] && [ "$COMPILER" = "builtin" ]; then
    echo "--search fulltext 需要 chmcmd，不能与 --no-compress / --compiler=builtin 同时使用" >&2
    exit 2
fi

log() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

# 未显式指定图片档位时用 compact（体积约为原图的 1/3，文字仍清晰）
if [ "$HAS_PROFILE" = 0 ]; then
    IMAGE_ARGS+=("--image-profile=compact")
fi

# ---------------------------------------------------------------- 1. Python
log "检查 Python 环境"
if [ ! -x "$PY" ]; then
    log "创建虚拟环境 $VENV"
    python3 -m venv "$VENV"
fi
if ! "$PY" -c "import markdown" 2>/dev/null; then
    log "安装依赖 markdown"
    PIP_DISABLE_PIP_VERSION_CHECK=1 "$PY" -m pip install --quiet markdown
fi
if ! "$PY" -c "import pygments" 2>/dev/null; then
    log "安装依赖 pygments（代码语法高亮）"
    PIP_DISABLE_PIP_VERSION_CHECK=1 "$PY" -m pip install --quiet pygments
fi
if [ "$BUILD_MODE" != "plain" ] && ! "$PY" -c "import PIL" 2>/dev/null; then
    log "安装依赖 pillow（图片压缩用）"
    PIP_DISABLE_PIP_VERSION_CHECK=1 "$PY" -m pip install --quiet pillow
fi

if [ "$COMPILER" = "chmcmd" ] && ! command -v chmcmd >/dev/null 2>&1; then
    echo "缺少 chmcmd。请先安装 Free Pascal（macOS: brew install fpc），" >&2
    echo "或改用 --compiler=auto / --no-compress。" >&2
    exit 1
fi
if [ "$COMPILER" = "auto" ] && ! command -v chmcmd >/dev/null 2>&1; then
    log "未找到 chmcmd，自动使用内置未压缩打包器"
fi

# ---------------------------------------------------------------- 2. 源仓库
if [ ! -d "$REPO/.git" ]; then
    log "克隆文档仓库（体积过滤，只取 Markdown）"
    git clone --depth 1 --filter=blob:limit=200k \
        "https://github.com/pingcap/docs-cn.git" "$REPO"
fi

log "更新源码到 $REF"
git -C "$REPO" fetch --depth 1 origin "$REF"
git -C "$REPO" checkout --force FETCH_HEAD
git -C "$REPO" clean -fdq

# ------------------------------------------------- 3~5. 构建 / 校验 / 自检
# $1 = 文件名后缀（"" 或 "-images"），$2 = 是否含图片（0/1）
build_target() {
    local suffix="$1" images="$2" title out chm chm_path
    if [ "$REF" = "master" ]; then
        title="TiDB 中文文档"
        out="$DIST/tidb-docs-cn${suffix}"
        chm="tidb-docs-cn${suffix}.chm"
    else
        title="TiDB ${REF#release-} 中文文档"
        out="$DIST/tidb-docs-${REF#release-}${suffix}"
        chm="tidb-docs-${REF#release-}${suffix}.chm"
    fi
    if [ "$images" = 1 ]; then
        title="${title}（含图片）"
    fi

    log "构建 CHM: ${title}"
    mkdir -p "$out"
    local args=(--repo "$REPO" --out "$out" --title "$title" --chm "$chm"
                --prune "$PRUNE" --compiler "$COMPILER" --all --lang zh
                --toc-mode "$TOC_MODE" --search "$SEARCH"
                --source-ref "$REF")
    if [ "${#FONT_ARGS[@]}" -gt 0 ]; then
        args+=("${FONT_ARGS[@]}")
    fi
    if [ "$images" = 1 ]; then
        args+=(--images)
        if [ "${#IMAGE_ARGS[@]}" -gt 0 ]; then
            args+=("${IMAGE_ARGS[@]}")
        fi
    fi
    "$PY" "$ROOT/tools/build_chm.py" "${args[@]}"

    chm_path="$out/$chm"
    if command -v 7zz >/dev/null 2>&1; then
        log "7-Zip 完整性校验"
        7zz t "$chm_path" | grep -E "Everything is Ok|ERROR" || true
    elif [ -x /tmp/7zz ]; then
        log "7-Zip 完整性校验"
        /tmp/7zz t "$chm_path" | grep -E "Everything is Ok|ERROR" || true
    else
        log "未找到 7zz，跳过独立校验（可选: brew install p7zip）"
    fi

    log "产物自检（目录卫生 / 编码 / 压缩 / 全文搜索）"
    # --expect-search 反映本次构建意图：声明要搜索就必须真的带上搜索结构
    local expect_search="auto"
    case "$SEARCH" in
        fulltext) expect_search="yes" ;;
        none) expect_search="no" ;;
        auto)
            if [ "$COMPILER" = "chmcmd" ] \
                || { [ "$COMPILER" = "auto" ] && command -v chmcmd >/dev/null 2>&1; }; then
                expect_search="yes"
            fi
            ;;
    esac
    "$PY" "$ROOT/tools/verify_chm.py" --expect-search "$expect_search" "$chm_path" || exit 1

    log "完成：${title}"
    echo "  离线文档 : $chm_path"
    if [ "$expect_search" = "yes" ]; then
        echo "  搜索页签 : Windows hh.exe 左侧有\"搜索\"页签（全文索引只收 ASCII 词，"
        echo "             中文关键词要用 hhc.exe 重编 docs.hhp，需 --keep-html 保留 HTML）"
    elif [ "$SEARCH" = "none" ]; then
        echo "  搜索页签 : 未生成（--no-search）"
    else
        echo "  搜索页签 : 未生成（当前打包器不支持全文搜索）"
    fi
    if [ "$PRUNE" = "none" ]; then
        echo "  效果预览 : $out/preview.html"
        echo "  官方工程 : $out/docs.hhp（含 HTML 正文，Windows 上 hhc.exe docs.hhp 可重编）"
    elif [ "$PRUNE" = "hhp" ]; then
        echo "  官方工程 : $out/docs.hhp、toc.hhc（正文 HTML 已清理，重编请改用 --keep-html）"
    else
        echo "  仅保留   : CHM（HTML/工程文件已清理）"
    fi
    CHM_LIST+=("$chm_path")
}

CHM_LIST=()
if [ "$BUILD_MODE" != "images" ]; then
    build_target "" 0
fi
if [ "$BUILD_MODE" != "plain" ]; then
    build_target "-images" 1
fi

log "全部完成"
if [ "${#CHM_LIST[@]}" -gt 0 ]; then
    for f in "${CHM_LIST[@]}"; do
        printf '  %6s  %s\n' "$(du -h "$f" | cut -f1)" "$f"
    done
fi
if [ "$PRUNE" = "chm" ]; then
    echo "  需要 HTML 版 / Windows 工程文件时：./build.sh --keep-html"
fi
