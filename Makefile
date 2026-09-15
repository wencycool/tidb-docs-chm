# tidb-docs-chm —— 常用动作快捷方式（等价于直接跑 ./build.sh）

PY   := .venv/bin/python
DIST := dist
CHM        := $(DIST)/tidb-docs-cn/tidb-docs-cn.chm
CHM_IMAGES := $(DIST)/tidb-docs-cn-images/tidb-docs-cn-images.chm

.PHONY: help build plain images test test-search verify preview clean

help:
	@echo "make build          纯文字版 + 压缩图片版"
	@echo "make plain          只生成纯文字版 -> $(CHM)"
	@echo "make images         含图片版 CHM（compact 档）-> $(CHM_IMAGES)"
	@echo "make test           渲染回归测试（用例 + 全量文档扫描）"
	@echo "make test-search    chmcmd 全文搜索集成测试（无 chmcmd 时跳过）"
	@echo "make verify         自检两个 CHM（结构 / 目录 / 编码 / 链接 / 版式 / 搜索）"
	@echo "make preview        保留 HTML 版并在浏览器打开预览页"
	@echo "make clean          删除 dist/（仅构建产物）"

build:
	./build.sh

plain:
	./build.sh --no-images

images:
	./build.sh --images

test:
	$(PY) tools/test_render.py
	$(PY) tools/test_chm_search.py

test-search:
	$(PY) tools/test_chm_search.py

verify:
	$(PY) tools/verify_chm.py $(CHM)
	@[ -f "$(CHM_IMAGES)" ] && $(PY) tools/verify_chm.py $(CHM_IMAGES) || true

preview:
	./build.sh --keep-html
	open $(DIST)/tidb-docs-cn/preview.html

clean:
	rm -rf $(DIST)
