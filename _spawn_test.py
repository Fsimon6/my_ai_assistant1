import sys, subprocess

print("PARENT=" + sys.executable)
r = subprocess.run(
    [sys.executable, "-c", "import sys; print('CHILD=' + sys.executable)"],
    capture_output=True,
    text=True,
)
print("CHILD_OUT=" + r.stdout.strip())
print("CHILD_ERR=" + r.stderr.strip())
