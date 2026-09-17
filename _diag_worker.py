from fastapi import FastAPI
import sys
app = FastAPI()
print("EXEC", sys.executable)
print("PREFIX", sys.prefix)
print("BASE", sys.base_prefix)
print("PATH", sys.path)
try:
    import excel_parser
    print("EP_OK", excel_parser.__file__)
except Exception as e:
    print("EP_FAIL", repr(e))
