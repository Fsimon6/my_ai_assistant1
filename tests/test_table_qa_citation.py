"""Unified Table QA 最终 Citation 完整化测试。

仅覆盖 Citation 上提逻辑（不修改 retrieval / SQL / routing）：
- Test A: Phase 1 语义检索 source 不再把 range/row 置空（底层已有真实坐标时）
- Test B/C: Structured Access / 全量枚举的真实 row/range/column 一路传到统一 Source
- Test D: Phase 2 DuckDB source 不被破坏（新增字段为 None，原字段保持）
"""
import pytest

from backend.services.table_qa_service import TableQAService
from backend.services import rag_service as rs_mod


def _chunk(meta, score=0.9):
    return {"id": "x", "content": "c", "metadata": meta, "score": score}


# ---------------- Test A: Phase 1 semantic source 携带真实坐标 ----------------
def test_phase1_semantic_source_carries_range_row_column():
    raw = rs_mod.RagService._extract_sources([_chunk({
        "document_id": "d1", "filename": "a.xlsx", "sheet_name": "Sheet1",
        "table_id": "Sheet1#0", "chunk_type": "row_group",
        "range": "A5:H12", "row_start": 5, "row_end": 12,
        "column_start": 1, "column_end": 8,
    }, score=0.9)])
    sources = TableQAService._phase1_sources("d1", raw)
    assert len(sources) == 1
    s = sources[0]
    assert s["filename"] == "a.xlsx"
    assert s["sheet_name"] == "Sheet1"
    assert s["range"] == "A5:H12"        # 关键：不再是 None
    assert s["row_index"] == 5           # 关键：不再是 None
    assert s["row_start"] == 5
    assert s["row_end"] == 12
    assert s["column"] == "A:H"          # 新增 column 字段
    assert s["column_start"] == 1
    assert s["column_end"] == 8
    assert s["match_mode"] == "semantic"


# ---------------- Test B: Structured Access 保留真实 row/range/column ----------------
def test_structured_access_source_carries_row_range_column(monkeypatch):
    fake_rep = {
        "workbook": {"sheets": [{
            "sheet_name": "OrderSKUList",
            "tables": [{"table_id": "OrderSKUList#0", "col_count": 63, "row_count": 21}],
        }]},
    }
    monkeypatch.setattr(rs_mod, "load_representation", lambda doc_id: fake_rep)
    raw = rs_mod.RagService._extract_sources([_chunk({
        "document_id": "d1", "filename": "直邮一店 8.20号订单.xlsx", "sheet_name": "OrderSKUList",
        "table_id": "OrderSKUList#0", "chunk_type": "table_structured_access",
        "row_start": 17, "row_end": 17, "columns": "SKU", "operation": "lookup",
    })])
    sources = TableQAService._phase1_sources("d1", raw)
    assert len(sources) == 1
    s = sources[0]
    assert s["filename"] == "直邮一店 8.20号订单.xlsx"
    assert s["sheet_name"] == "OrderSKUList"
    assert s["row_start"] == 17 and s["row_end"] == 17
    assert s["row_index"] == 17
    assert s["range"] == "A17:BK17"      # col_count=63 -> BK
    assert s["column"] == "A:BK"
    assert s["match_mode"] == "structured"


def test_full_enum_source_span_all_rows(monkeypatch):
    fake_rep = {
        "workbook": {"sheets": [{
            "sheet_name": "S",
            "tables": [{"table_id": "S#0", "col_count": 10, "row_count": 58}],
        }]},
    }
    monkeypatch.setattr(rs_mod, "load_representation", lambda doc_id: fake_rep)
    raw = rs_mod.RagService._extract_sources([_chunk({
        "document_id": "d1", "filename": "f.xlsx", "sheet_name": "S",
        "table_id": "S#0", "chunk_type": "table_structured_access",
        "row_start": 2, "row_end": 59, "columns": "ALL", "operation": "full_column",
    })])
    s = TableQAService._phase1_sources("d1", raw)[0]
    assert s["range"] == "A2:J59"        # 跨全部数据行
    assert s["row_start"] == 2 and s["row_end"] == 59
    assert s["column"] == "A:J"
    assert s["match_mode"] == "structured"


# ---------------- Test C: column 字母推导 ----------------
def test_source_from_chunk_column_letters():
    m = {
        "document_id": "d1", "filename": "a.xlsx", "sheet_name": "S",
        "table_id": "S#0", "chunk_type": "row_group",
        "range": "A1:C3", "row_start": 1, "row_end": 3,
        "column_start": 1, "column_end": 3,
    }
    src = rs_mod.RagService._source_from_chunk(m)
    assert src["column"] == "A:C"
    assert src["range"] == "A1:C3"
    assert src["row_index"] == 1


def test_source_from_struct_without_rep_is_safe(monkeypatch):
    monkeypatch.setattr(rs_mod, "load_representation", lambda doc_id: None)
    m = {
        "document_id": "d1", "filename": "a.xlsx", "sheet_name": "S",
        "table_id": "S#0", "chunk_type": "table_structured_access",
        "row_start": 5, "row_end": 5,
    }
    src = rs_mod.RagService._source_from_struct(m)
    # rep 缺失时 range/column 可为 None，但 row 仍保留（不编造）
    assert src["row_start"] == 5
    assert src["row_index"] == 5
    assert src["range"] is None


# ---------------- Test D: Phase 2 regression（sources 不被破坏） ----------------
def test_phase2_normalize_sources_not_degraded():
    phase2_sources = [{
        "document_id": "d1", "filename": "f.xlsx", "sheet_name": "S",
        "row_index": 5, "range": "A5:J5",
    }]
    norm = TableQAService._normalize_sources(phase2_sources, "precise")
    assert len(norm) == 1
    s = norm[0]
    assert s["filename"] == "f.xlsx"
    assert s["sheet_name"] == "S"
    assert s["range"] == "A5:J5"
    assert s["row_index"] == 5
    assert s["match_mode"] == "precise"
    # 新增字段在 Phase 2 为 None（DuckDB 无 column 信息）-> 不退化原字段
    assert s["column"] is None
    assert s["row_start"] is None
    assert s["row_end"] is None
