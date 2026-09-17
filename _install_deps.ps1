$venv = "C:\Users\Administrator\Desktop\my_ai_assistant\backend\.venv\Scripts\python.exe"
& $venv -m pip install markdown docx2txt 2>&1 | Select-Object -Last 15
