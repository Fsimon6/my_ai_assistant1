# 第33阶段最终报告 —— 通用二维表格 RAG 第一阶段

> 测试样本：`直邮一店 8.20号订单.xlsx`（1 Sheet `OrderSKUList`，63 列 × 21 行，2 层表头，全文本型存储）
> 判定：**PHASE_1_TABLE_RAG_COMPLETE**

---

## 1. 实际架构

在**不改动现有文本链路**（`DocumentProcessor` / `DocumentService` / `RagService` / `VectorService` / `LLMService`）的前提下，新增一条独立的 Table Document 处理路径：

```
.xlsx / .xls / .csv / .tsv
        │  (rag_service.process_and_store_document 按扩展名分流)
        ▼
Table Parser Layer  (excel-parser 0.2.1 解析 .xlsx/.xls；内置 csv 读取器解析 .csv/.tsv)
        │
        ▼
Unified Table Representation  (backend/services/table_representation.py)
   - 解析为统一网格 → 检测表头层数 → 列/行/单元格 + 坐标 + 类型
   - §21 校验：独立 openpyxl/csv 扫描原始列数，对比 Representation 列数，<50% 即判定失败
        │
        ▼
Table-aware Chunk Builder
   - Workbook Summary（1 块）
   - Sheet Schema（每表 1 块，字段结构 + table_id）
   - Row Group（每 8 行 1 块，字段映射仅写一次，不与每行重复）
        │
        ▼
Remote Embedding（沿用现有 AIAssistantEmbeddings，未改）
        │
        ▼
Chroma 集合 ai_assistant_docs（与文本文档同集合，metadata 增加表格字段）
        │
        ▼
现有 Retrieval → 现有 LLM → Answer（含 source_uri 引用）
```

- 文本文档仍走原 `DocumentProcessor`，零改动。
- 表格文档走 `_process_and_store_table`，与原文本分支并列。

## 2. Unified Table Representation

文件：`backend/services/table_representation.py: build_representation(...)`

结构（节选）：

```json
{
  "document_id": "uuid",
  "filename": "直邮一店 8.20号订单.xlsx",
  "file_type": "xlsx",
  "user_id": 1,
  "original_path": "data/table_originals/<id>.xlsx",
  "generated_at": "ISO",
  "workbook": {
    "sheets": [{
      "sheet_id": 0,
      "sheet_name": "OrderSKUList",
      "tables": [{
        "table_id": "OrderSKUList#0",
        "range": "A1:BK21",
        "header_rows": [1, 2],
        "n_header_rows": 2,
        "columns": [
          {"col_index":1,"col_letter":"A","technical_name":"Order ID","display_name":"Order ID","description":"Platform unique order ID.","data_type":"text"},
          ...
          {"col_index":12,"col_letter":"L","technical_name":"Quantity","description":"...","data_type":"numeric"}
        ],
        "data_row_start": 3, "data_row_end": 21,
        "row_count": 19, "col_count": 63,
        "rows": [{"row_index":3,"range":"A3:BK3","cells":[{"col_letter":"A","value":"5775...","type":"text"}, ...]}]
      }]
    }]
  }
}
```

- 支持单层 / 两层 / 表头+描述（§9）：`technical_name` / `display_name` / `description` 三层字段元数据。
- 支持 Sheet（§10）：`workbook.sheets[]`，`sheet_name` / `sheet_id` 保留，多 Sheet 不会拼成无边界文本。
- 支持 Row / Cell / Range（§11）：每行有 `row_index` 与 `range`（如 `A3:BK3`），每单元格有 `col_letter` 与坐标。

## 3. excel-parser 接入方式

- 正式项目 venv 为 **Python 3.10**，excel-parser 官方要求 `>=3.11`。
- 解决方案：**直接 `pip install git+...excel-parser --ignore-requires-python` 安装到项目 venv**，实测可在 3.10.11 正常 `import` 与解析（无 fork、无子进程、无额外 venv）。
- 解析核心用 `excel_parser.parse_workbook`，从 `res.workbook.sheets[].cells`（逐单元格扫描，对 `<dimension>` 损坏文件健壮）读取坐标→值网格，再归一化为统一表示。
- CSV/TSV 用内置 `csv` 读取器产出相同网格，保证 Representation 与格式无关。
- `.xls` 已安装 `xlrd`，走 excel-parser 同为 best-effort 支持。

## 4. Chunk 策略（解决“Schema + Row”问题，§13/§14）

| Chunk 类型 | 数量 | 内容 | 关联字段 |
|---|---|---|---|
| `workbook_summary` | 1 | 文件概述 + 全部字段（列字母=字段名）列表 | — |
| `sheet_schema` | 每表 1 | 字段结构：列字母 字段名 — 描述（类型） | `table_id` |
| `row_group` | ⌈数据行/8⌉ | “工作表 + 数据范围 + 字段映射（仅一次） + 行数据” | `table_id`, `sheet_name`, `range`, `row_start/end` |

- 本样本（19 数据行）共 **5 块**：1 summary + 1 schema + 3 row_group（8/8/3）。
- 相比 benchmark 中 excel-parser 原生“按行切块 20 块 × ~1.3k token”，本方案行内不再重复 63 个表头，且 Row 与 Schema 通过 `table_id` 关联，检索到 Row 时可回溯 Schema。
- Row Group 每块自带字段映射，单独检索也能自解释（不会出现“1 / golden eye / 20 / 500”无上下文）。

## 5. Chroma metadata（§16，均为标量，Chroma 兼容）

每个 chunk 的 metadata（实测键）：
`document_id, user_id, filename, file_type, chunk_type, sheet_name, table_id, row_start, row_end, column_start, column_end, range, source_uri, source, processed_at, chunk_index, file_size`

- `user_id` **始终保留**，检索严格按 `where: {document_id, user_id}` 过滤。
- `source_uri` = `文件名#Sheet!Range`（如 `直邮一店 8.20号订单.xlsx#OrderSKUList!A3:BK10`），供引用与 `_build_context` 展示。

## 6. Citation（§12，一等公民）

- 文件：`filename`
- Sheet：`sheet_name`（如 `OrderSKUList`）
- Range：`range`（如 `A3:BK10` 或整表 `A1:BK21`）
- Cell：`col_letter` + `row_index`（如 `L3`）
- 最终 `source_uri`：`直邮一店 8.20号订单.xlsx#OrderSKUList!A17:BK17`

实测 Q5 回答成功返回：文件 → OrderSKUList → A1:BK21，并列出各 row_group 范围（A3:BK10 / A11:BK18 / A19:BK21）。

## 7. 用户隔离（§17）

- 沿用现有 `user_id` 过滤；表格 chunk metadata 含 `user_id`，`get_document_chunks` / `search` / `delete_documents` 均按 `user_id` 隔离。
- 实测：user1 存入表格文档后，以 user2 查询 → **0 块**；user1 查询 → 5 块。跨用户零泄漏。
- 预览接口 `/documents/{id}/table-structure` 也先做 user_id 归属校验，否则 404。

## 8. 前端变化（§23，最小修改）

- `FileUploader.vue`：扩展允许类型（`.xlsx/.xls/.csv/.tsv`），MIME 兜底 + 扩展名兜底；二进制表格类不在前端以文本读取（直接发原文件）。
- `KnowledgeBase.vue`：文件图标支持表格类型；预览对话框新增“表格结构”面板（工作表 / 范围 / 列数 / 数据行 / 字段映射），并调用新增的 `getTableStructure` 接口。
- `api.ts`：新增 `ragApi.getTableStructure(documentId)`。
- Chat 链路未改动（§24）；表格 chunk 经现有 RAG 路径被检索。

## 9. 测试文件

- 后端核心：`backend/services/table_representation.py`（新建）
- 后端改动：`backend/services/rag_service.py`（分流 + `_process_and_store_table` + `get_table_structure`）、`backend/services/document_service.py`（扩展名白名单）、`backend/api/v1/rag.py`（允许类型 + 表格结构预览接口）
- 前端改动：`FileUploader.vue`、`KnowledgeBase.vue`、`api.ts`
- 临时验证脚本均已删除，样本原始文件未被移动/修改。

## 10. Q1–Q5 实测结果

端到端（解析→Chroma→remote Embedding→检索→LLM）：

- **Q1 这个表主要记录什么？** ✅ 正确概括为订单明细（订单/商品/物流/财务五类信息）。
- **Q2 Seller SKU 是什么？** ✅ 正确定位到列 G，并引用字段描述。
- **Q3 golden eye 对应的是什么商品？** ✅ 本样本无该商品，LLM 正确返回“未找到”并列出真实商品名（证明 Product Name 可被检索）。
- **Q4 这个字段信息在哪个 Sheet？** ✅ 正确回答 `OrderSKUList`。
- **Q5 这条记录来自哪里？** ✅ 返回 文件 → OrderSKUList → A1:BK21，并给出具体 Row Group 范围。

## 11. TXT/MD/DOCX 回归（§22/§31）

- 用临时 `.md` 走原 `process_and_store_document`：解析/Embedding/Chroma 正常（`success: True, chunks: 1`），文本链路未被影响。
- 现有 `DocumentService`/`RagService`/`VectorService` 仅新增分支与方法，文本代码路径零改动。

## 12. 性能（§34，非压测）

- 解析 + §21 校验：亚秒级（63×21 全扫描）。
- 本样本生成 5 个 chunk，remote Embedding + Chroma 写入在数秒内完成（受远程 Embedding 网络影响）。
- Chroma 写入：5 条向量，含完整 metadata。

## 13. 修改文件清单

新建：
- `backend/services/table_representation.py`

修改：
- `backend/services/rag_service.py`
- `backend/services/document_service.py`
- `backend/api/v1/rag.py`
- `frontend/src/components/chat/FileUploader.vue`
- `frontend/src/views/knowledge/KnowledgeBase.vue`
- `frontend/src/services/api.ts`

依赖新增（项目 venv，Python 3.10）：`excel-parser`(git, --ignore-requires-python)、`openpyxl`、`tiktoken`、`xxhash`、`xlrd`。

## 14. 已知问题

- `excel-parser` 官方要求 Python ≥3.11，当前用 `--ignore-requires-python` 装在 3.10 venv；若未来升级项目到 3.11+ 可直接正常安装。
- Chroma 元数据值须为标量，嵌套结构（如完整 cells 列表）仅存于 `data/table_originals/<id>.json`，不进 Chroma（符合设计）。
- `.xls` 为 best-effort（依赖 xlrd），无对应样本回归。

## 15. 当前限制（第一阶段边界）

- 不支持：DuckDB / NL2SQL / Pandas 查询 Agent / Table Tool / 动态 Sheet/Row/Range 提取 / 公式计算 / 多表关联 / 统计聚合（按计划留待第二阶段）。
- 单 Sheet 内默认识别为 1 个逻辑表；多逻辑表（同一 Sheet 内多个不连续区域）未拆分。
- 表头检测为启发式（前导无数值行，最多 3 行）；极端无表头表格会按 1 行表头处理。
- CSV/TSV 仅单层表头（Case A）；多描述行 CSV 不特殊处理。

## 16. 下一阶段建议

- **第二阶段**：在 Unified Table Representation 之上接入 DuckDB / NL2SQL / Table Tool（表示层已可扩展，无需重构解析）。
- 多 Sheet 多逻辑表拆分、公式保留、单元格级精确提取可作为增强。
- 可对 row_group 大小按列数自适应（超宽表减小每组行数，控制单块 token）。
- 预览接口可进一步增强为“点击单元格显示 A1 坐标 + 原始值”。

---

## 判定

**PHASE_1_TABLE_RAG_COMPLETE**

满足全部完成条件：Excel 正确解析（63 列无丢列）、Representation 正确、Chroma 写入正确、语义检索有效、LLM 可理解、Citation 有效（文件/Sheet/Range）、user_id 隔离正常、TXT/MD/DOCX 文本链路不受影响。
