import os

MODEL = os.getenv("MODEL_NAME", "claude-haiku-5-5")


def get_llm(max_tokens: int = 700):
    if os.getenv("ADFORGE_FAKE_LLM") == "1":
        from .fake_llm import FakeChat
        return FakeChat()
    from langchain_anthropic import ChatAnthropic
    kwargs = {}
    if os.getenv("TEMPERATURE"):          # unset by default: some newer models restrict sampling params
        kwargs["temperature"] = float(os.environ["TEMPERATURE"])
    return ChatAnthropic(model=MODEL, max_tokens=max_tokens, timeout=45, max_retries=2, **kwargs)
