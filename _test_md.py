import os, tempfile
d = tempfile.mkdtemp()
md = os.path.join(d, "t.md")
with open(md, "w", encoding="utf-8") as f:
    f.write("# 标题\n\n第一段正文，用于测试 markdown 解析。\n\n第二段内容。\n")

from unstructured.partition.md import partition_md

for strat in ["auto", "fast"]:
    try:
        els = partition_md(md, strategy=strat)
        print(f"strategy={strat} OK, elements={len(els)}")
        for e in els[:3]:
            print("   ", type(e).__name__, repr(str(e)[:40]))
    except Exception as ex:
        print(f"strategy={strat} FAIL: {ex}")
