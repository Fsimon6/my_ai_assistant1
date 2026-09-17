$venv = "C:\Users\Administrator\Desktop\my_ai_assistant\backend\.venv\Scripts\python.exe"
& $venv -m pip list | Out-String -Stream | Select-String -Pattern 'docx2txt|unstructured|torch|PyMuPDF|markdown|python-docx|langchain'
