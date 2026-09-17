import sys
print("EXEC=" + sys.executable)
print("PREFIX=" + sys.prefix)
try:
    import excel_parser
    print("EXCEL_OK=" + excel_parser.__file__)
except Exception as e:
    print("EXCEL_FAIL=" + repr(e))
