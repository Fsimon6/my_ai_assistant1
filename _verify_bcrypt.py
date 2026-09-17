import importlib.metadata as m
try:
    print("openai version:", m.version("openai"))
except Exception as e:
    print("openai metadata error:", e)
try:
    import openai
    print("openai.__version__:", getattr(openai, "__version__", "?"))
    print("has AsyncOpenAI:", hasattr(openai, "AsyncOpenAI"))
    print("has embeddings attr on client:", hasattr(openai.AsyncOpenAI, "embeddings") if hasattr(openai, "AsyncOpenAI") else "n/a")
except Exception as e:
    print("openai import error:", repr(e))
