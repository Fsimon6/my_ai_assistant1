$sys = "C:\Program Files\Python310\python.exe"

Write-Host "=== 系统 python 是否缺依赖 ==="
& $sys -c "import unstructured" 2>&1 | Select-Object -First 1
& $sys -c "import docx2txt" 2>&1 | Select-Object -First 1
& $sys -c "import markdown" 2>&1 | Select-Object -First 1

Write-Host "=== 安装到系统 python（轻量，不引入 torch/PyMuPDF）==="
& $sys -m pip install markdown docx2txt "unstructured==0.18.32" 2>&1 | Select-Object -Last 20

Write-Host "=== 校验未引入 torch / PyMuPDF ==="
& $sys -m pip list 2>$null | Out-String -Stream | Select-String -Pattern 'torch|PyMuPDF|unstructured|docx2txt|markdown'

Write-Host "=== 复验系统 python 可导入 ==="
& $sys -c "import unstructured; from unstructured.partition.md import partition_md; import docx2txt; import markdown; print('ALL OK on system python')"
