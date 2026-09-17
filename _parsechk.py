import sys
sys.path.insert(0, r'C:\Users\Administrator\Desktop\my_ai_assistant')
from backend.services.table_representation import _parse_with_excel_parser

fp = r'C:\Users\Administrator\Desktop\my_ai_assistant\data\table_originals\1f1e9dc28e3f4d4faf9e140d55277b48.xlsx'
rows = _parse_with_excel_parser(fp)
print("PARSE_OK rows=", len(rows))
print("first_row_sample=", rows[0] if rows else None)
