"""核心异常类层级. Bob review 提出的 Medium 风险项 (replace generic except Exception).

用法:
  try:
      df = load_daily(symbol)
  except StockDelistedError:
      logger.info(f"{symbol} 已退市, 跳过")
      continue
  except DataFetchError as e:
      logger.warn(f"{symbol} 数据拉取失败 (可重试): {e}")
      retry()
  except QuantError as e:
      logger.error(f"未预期的 quant 错误: {e}")
      raise
"""


class QuantError(Exception):
    """所有 quant 项目自定义异常的基类."""
    pass


# ─────── 数据相关 ───────
class DataError(QuantError):
    """数据读取/解析问题."""
    pass


class DataFetchError(DataError):
    """数据网络拉取失败 (akshare/baostock/RSS), 可重试."""
    pass


class DataParseError(DataError):
    """数据格式错误, 不可重试 (脏数据)."""
    pass


class StockDelistedError(DataError):
    """股票已退市/暂停上市. 应跳过, 不报警."""
    pass


class StockSuspendedError(DataError):
    """股票当日停牌. 应跳过."""
    pass


class DataQualityError(DataError):
    """数据通过基本读取但 validate_ohlc 检测有严重问题."""
    pass


# ─────── 网络相关 ───────
class NetworkError(QuantError):
    """通用网络错误."""
    pass


class ProxyBlockedError(NetworkError):
    """IBM 内网 proxy 拦截 (e.g. data.tdx.com.cn timeout)."""
    pass


class APIRateLimitError(NetworkError):
    """API 限流 (akshare/akshare/anthropic). 可 backoff 重试."""
    pass


# ─────── LLM 相关 ───────
class LLMError(QuantError):
    """LLM 调用相关."""
    pass


class LLMBudgetExceededError(LLMError):
    """LLM 预算超限."""
    pass


class LLMResponseInvalidError(LLMError):
    """LLM 返回非法 (无法解析 JSON 等)."""
    pass


# ─────── 配置相关 ───────
class ConfigError(QuantError):
    """配置文件错误."""
    pass


class TemplateNotFoundError(ConfigError):
    """请求的 template (e.g. xiangnong) 不在 config.templates."""
    pass
