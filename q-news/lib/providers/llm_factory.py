"""LLM provider 工厂. 读 config.providers.llm.active → 返回实例."""

from .llm_anthropic import AnthropicLLM
from .llm_openai_compat import OpenAICompatLLM
from .llm_dummy import DummyLLM, DummyPricedLLM


_REGISTRY = {
    "anthropic": AnthropicLLM,
    "openai_compat": OpenAICompatLLM,
    "deepseek": OpenAICompatLLM,   # DeepSeek 兼容 OpenAI SDK, 走相同实现
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
    cls = _REGISTRY[active]
    # OpenAICompatLLM 接受 provider_name 参数，让实例 name 反映实际配置
    if cls is OpenAICompatLLM:
        return cls(sub_cfg, provider_name=active)
    return cls(sub_cfg)
