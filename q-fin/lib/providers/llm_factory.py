"""LLM provider 工厂. 读 config.providers.llm.active → 返回实例."""

from .llm_anthropic import AnthropicLLM
from .llm_openai_compat import OpenAICompatLLM
from .llm_dummy import DummyLLM, DummyPricedLLM


_REGISTRY = {
    "anthropic": AnthropicLLM,
    "openai_compat": OpenAICompatLLM,
    "dummy": DummyLLM,
    "dummy_priced": DummyPricedLLM,
}


def get_llm(providers_cfg: dict, override: str | None = None):
    """返回 LLM provider 实例.
    providers_cfg = config["providers"]["llm"] (含 active 和各 provider 子配置)
    override = CLI --llm <name> 临时切换
    """
    active = override or providers_cfg.get("active", "anthropic")
    if active not in _REGISTRY:
        raise ValueError(f"unknown llm provider: {active}. valid: {list(_REGISTRY)}")
    sub_cfg = providers_cfg.get(active, {})
    return _REGISTRY[active](sub_cfg)
