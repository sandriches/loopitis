import os

# Keep tests offline: no tracing, and a placeholder key so the default agent can be built.
os.environ["LANGSMITH_TRACING"] = "false"
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
