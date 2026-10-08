"""Representation 表头/类型推断回归测试。

锁定已确认行为：带描述行的【两行表头】Excel
  - 正确识别 n_header_rows = 2（字段名行 + 描述行）
  - 描述行不得泄漏进 data_rows
  - 数值列保持 numeric（不因为描述行而整列变 text）

不依赖任何具体业务文件（如「直邮5店」），使用最小通用 fixture。
"""
import os
import pytest

from backend.services.table_representation import build_representation

pytestmark = pytest.mark.skipif(
    __import__("importlib").util.find_spec("openpyxl") is None,
    reason="openpyxl not available",
)


def _make_two_row_header_xlsx(path: str):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    # 第一行：字段名
    ws.append(["Order ID", "Qty", "Fee", "Note"])
    # 第二行：描述（必须被识别为表头，不能进数据）
    ws.append([
        "Unique order identifier",
        "Units sold in this order",
        "Shipping fee amount",
        "Free text note",
    ])
    # 真实数据行
    ws.append([1001, 5, 12.5, "hello"])
    ws.append([1002, 3, 9.0, "world"])
    ws.append([1003, 10, 7.25, "x"])
    wb.save(path)


def test_two_row_header_excluded_from_data_and_numeric_preserved(tmp_path):
    fp = os.path.join(str(tmp_path), "t.xlsx")
    _make_two_row_header_xlsx(fp)

    rep = build_representation(
        file_path=fp, document_id="test_doc", user_id="test",
        filename="t.xlsx", file_type="xlsx", original_path=fp,
        embedding_model=None,
    )
    table = rep["workbook"]["sheets"][0]["tables"][0]

    # 1) 两行表头被正确识别
    assert table["n_header_rows"] == 2, table["n_header_rows"]

    # 2) 数据行数 = 3（不含描述行）
    assert table["row_count"] == 3, table["row_count"]

    # 3) 描述行未泄漏进 data_rows
    desc_texts = {
        "Unique order identifier", "Units sold in this order",
        "Shipping fee amount", "Free text note",
    }
    leaked = False
    for row in table["rows"]:
        for cell in row["cells"]:
            if cell["value"] in desc_texts:
                leaked = True
    assert not leaked, "描述行泄漏进了 data_rows"

    # 4) 数值列保持 numeric
    types = {c["technical_name"]: c["data_type"] for c in table["columns"]}
    assert types["Qty"] == "numeric", types
    assert types["Fee"] == "numeric", types
    # 5) 真实文本列仍是 text（不应被误判 numeric）
    assert types["Note"] == "text", types
