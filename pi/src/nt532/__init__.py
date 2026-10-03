import sys

# Console Windows dùng cp1252 khi đầu ra bị chuyển hướng, không in được tiếng Việt.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")
