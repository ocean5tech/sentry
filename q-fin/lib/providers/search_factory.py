"""Search provider 工厂."""

from .search_duckduckgo import DuckDuckGoSearch
from .search_anthropic_tool import AnthropicToolSearch
from .search_dummy import DummySearch


_REGISTRY = {
    "duckduckgo": DuckDuckGoSearch,
    "anthropic_tool": AnthropicToolSearch,
    "dummy": DummySearch,
}


def get_search(providers_cfg: dict, override: str | None = None, llm_active: str | None = None):
    """返回 search provider 实例.
    providers_cfg = config["providers"]["search"]
    override = CLI --search <name>
    llm_active = 当前 llm provider, 校验 anthropic_tool requires_llm
    """
    active = override or providers_cfg.get("active", "duckduckgo")
    if active not in _REGISTRY:
        raise ValueError(f"unknown search provider: {active}. valid: {list(_REGISTRY)}")

    sub_cfg = providers_cfg.get(active, {})

    # 校验 anthropic_tool requires_llm 一致性
    if active == "anthropic_tool":
        required_llm = sub_cfg.get("requires_llm", "anthropic")
        if llm_active and llm_active != required_llm:
            raise RuntimeError(
                f"search.active={active} requires llm={required_llm}, but llm.active={llm_active}"
            )

    return _REGISTRY[active](sub_cfg)
