import sys
sys.path.insert(0, r'C:\Users\Administrator\Desktop\my_ai_assistant')
from backend.services.table_representation import parse_to_grids, col_letter
grids = parse_to_grids(r'C:\Users\Administrator\Desktop\my_ai_assistant\_test_order.xlsx')
g = grids[0]
rows = g['rows']
print('NCOLS', len(rows[0]) if rows else 0)
print('NROWS', len(rows))
for ri in range(min(3, len(rows))):
    print('ROW', ri)
    for ci in range(min(12, len(rows[ri]))):
        cell = rows[ri][ci]
        v = cell['value'] if cell else None
        print(f'  {col_letter(ci+1)}={v!r}')
