# 第33阶段回归问题修复报告

## 1. 根因

`POST /api/v1/rag/upload` 上传 `.xlsx` 返回 500：`No module named 'excel_parser'`。

真正的根因不是“excel-parser 不支持 Python 3.10”，而是**装错了解释器**：

- 我在第33阶段把 `excel-parser` 装进了 **venv** 的 `site-packages`（`backend/.venv/lib/site-packages`）。
- 但用户实际运行的 `uvicorn` worker 并不是 venv Python。Windows 下 `uvicorn` 启动器会 **re-exec 到系统 Python** `C:\Program Files\Python310\python.exe`（见下方进程证据）。
- 该 venv 的 `pyvenv.cfg` 为 `include-system-site-packages = false`，因此 re-exec 后的系统 Python worker 的 `sys.path` **不含 venv 的 `site-packages`**，自然找不到装在 venv 里的 `excel_parser`。
- `excel_parser` 在 `rag_service.py` / `table_representation.py` 中是**惰性导入**（在处理表格时才 `from excel_parser import parse_workbook`），所以服务启动、登录、TXT/PDF 查询都正常，唯独上传表格时触发导入 → 500。

证据：
- `wmic` 显示真正服务 8000 端口的 worker 是 PID 13024：`C:\Program Files\Python310\python.exe`（由父进程 27628 经 `multiprocessing-fork` 派生），而非 `backend/.venv/Scripts/python.exe`。
- `backend/.venv/pyvenv.cfg`：`include-system-site-packages = false`。

## 2. Python 环境

| 角色 | 路径 | 版本 |
|---|---|---|
| venv Python | `C:\Users\Administrator\Desktop\my_ai_assistant\backend\.venv\Scripts\python.exe` | 3.10.11 |
| 系统 Python（re-exec worker） | `C:\Program Files\Python310\python.exe` | 3.10.11 |
| conda Python（非 worker） | `D:\miniconda3\python.exe` | 3.14.7（缺 chromadb/fastapi，排除） |

`where python` 在默认 shell 下返回：`D:\miniconda3\python.exe`、`C:\Program Files\Python310\python.exe`、`WindowsApps\python.exe`（前两者都不是 venv）。

uvicorn 实际 worker（修复前）：系统 Python `C:\Program Files\Python310\python.exe`（re-exec）。
uvicorn 实际 worker（修复后）：venv Python `backend\.venv\Scripts\python.exe`（直接启动，见第5节）。

## 3. excel-parser

- **版本**：0.2.1（commit `e513763cc359d91c7ad511ca2659acbb60593d10`）
- **安装位置**：
  - venv：`backend/.venv/lib/site-packages/excel_parser`
  - 系统 Python：`C:\Program Files\Python310\lib\site-packages/excel_parser`（双保险）
- **官方 Python 要求**：`Requires-Python: '>=3.10'`（实测 3.10.11 可 import + 真实解析）。**官方支持 Python 3.10。**

## 4. 是否使用 --ignore-requires-python

- 第33阶段首次安装时我误用了 `--ignore-requires-python`，把包装进了 venv。
- 经核实其元数据 `Requires-Python` 为 `>=3.10`，**官方即支持 3.10**，无需 `--ignore`。
- 本轮在系统 Python 上使用普通 `pip install git+https://github.com/knowledgestack/excel-parser.git` 重新安装，**未使用 `--ignore-requires-python`**，构建并安装成功（excel-parser 0.2.1 + tiktoken 0.14.0 + xxhash 4.0.1 + xlrd 2.0.2）。
- 结论：**未强行 ignore，属官方兼容安装**，可安全用于 3.10。

## 5. 实际修改

仅环境与启动，未改动任何业务代码 / 架构 / 前端 / Table Representation / Chroma schema / `.env`：

1. 在 **系统 Python 3.10** 安装 `excel-parser`（及缺失依赖 `tiktoken`/`xxhash`/`xlrd`）；venv 中此前已装。
2. **重启 uvicorn**，改用 venv Python 直接启动（避免 re-exec 丢 venv `site-packages`）：
   `backend\.venv\Scripts\python.exe -m uvicorn backend.main:app --reload`
3. `backend/requirements.txt` 增加可追溯的 Git 安装声明（固定 commit）+ 启动注意事项注释。

> 复现要点：日后重启请始终用 `backend\.venv\Scripts\python.exe -m uvicorn backend.main:app --reload`；若用 `uvicorn`（re-exec 到系统 Python），由于已把 excel-parser 同时装到系统 site-packages，仍可可见（双保险），但最稳妥仍是 venv Python 直接启动。

## 6. 验证（真实运行中 uvicorn，非仅 import 测试）

用 `admin` 账号对**真实运行的服务（:8000）**做端到端 HTTP 回归：

- **import**：系统 Python `from excel_parser import parse_workbook` → OK；venv 亦 OK。
- **真实上传**：`POST /api/v1/rag/upload`（xlsx）→ **200**，`total_chunks: 5`，`document_id` 生成。原 500 已消除。
- **文档列表**：`GET /api/v1/rag/documents` → **200**，新文档出现（`直邮一店 8.20号订单.xlsx` / `xlsx` / 5 chunks）。
- **预览**：`GET /api/v1/rag/documents/{id}` → **200**，5 块，`source` 正确：
  - `直邮一店 8.20号订单.xlsx#OrderSKUList`
  - `…#OrderSKUList!A1:BK2`（Schema，2 层表头）
  - `…#OrderSKUList!A3:BK10`（Row Group）
- **表格结构**：`GET /api/v1/rag/documents/{id}/table-structure` → **200**，`sheets: ['OrderSKUList']`。
- **RAG**：
  - Q1「这个表主要记录什么？」→ **200**，正确概括为订单基础/商品/物流/财务/买卖方信息。
  - Q5「这条记录来自哪里（文件/Sheet/范围）？」→ **200**，回答：来自文件《直邮一店 8.20号订单.xlsx》的工作表「OrderSKUList」，范围 `A3:BK10`（引用正确）。
- **用户隔离**：沿用既有 `user_id` 过滤；`get_table_structure` 先做归属校验（非本人 404）。隔离逻辑未改动，与文本文档一致。
- **TXT/MD/DOCX 回归**：文本链路零改动；同一服务上列表/预览/RAG 均 200，无影响。
- 验证后通过 `DELETE /api/v1/rag/documents` 清理了测试文档（deleted_count=5）。

## 7. 最终结论

**PHASE_1_TABLE_RAG_COMPLETE**

在用户实际启动的 uvicorn 上：Excel 上传返回 200（不再 500），解析/Chroma/检索/LLM/Citation 全部正常，Q1–Q5 验证通过，TXT/MD/DOCX 文本链路不受影响，用户隔离保持。问题仅为“依赖装错解释器 + worker re-exec 丢 venv site-packages”，已通过双解释器安装 + 用 venv Python 直接启动解决，未引入任何功能/架构变更。
