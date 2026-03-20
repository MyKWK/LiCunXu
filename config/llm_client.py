"""LLM 客户端（全部通过维纳斯 Venus 平台调用）

venus_llm: 用于 Cypher 生成 / 数据摄入等内部任务（默认 deepseek-v3.2）
deepseek_llm: 用于知识库问答 / AI 总结（面向用户，默认 glm-5）
  ↑ 变量名保持 deepseek_llm 以兼容已有引用
"""

import json
import re
import time

import requests
from loguru import logger

from config.settings import settings


# ━━━━━━━━━━━━━━━ 限流异常 ━━━━━━━━━━━━━━━
# 被 Venus 平台限流时抛出此异常，上层（enhanced_pipeline）可捕获并降级
RATE_LIMIT_STATUS_CODES = {429, 503}
RATE_LIMIT_KEYWORDS = [
    "rate limit", "too many requests", "quota exceeded",
    "throttl", "resource exhausted", "overloaded",
    "请求过于频繁", "限流", "配额",
]


class VenusRateLimitError(RuntimeError):
    """Venus 平台限流异常，上层可据此决定降级策略"""
    pass


def _check_rate_limit(status_code: int, body_text: str):
    """检查 HTTP 响应是否为限流，如果是则立即抛出 VenusRateLimitError"""
    if status_code in RATE_LIMIT_STATUS_CODES:
        raise VenusRateLimitError(
            f"Venus 限流 (HTTP {status_code}): {body_text[:300]}"
        )
    body_lower = body_text.lower()
    for kw in RATE_LIMIT_KEYWORDS:
        if kw.lower() in body_lower:
            raise VenusRateLimitError(
                f"Venus 限流 (关键词 '{kw}'): {body_text[:300]}"
            )


class _VenusModelBusyError(RuntimeError):
    """内部异常：模型繁忙，触发 fallback（不对外暴露）"""
    pass


# ━━━━━━━━━━━━━━━ Fallback 模型配置 ━━━━━━━━━━━━━━━
# 按优先级排列的备选模型列表（主模型失败时依次尝试）
FALLBACK_MODELS = ["glm-5", "minimax-m2.5", "deepseek-v3.1-terminus"]
# 使用备选模型成功处理多少次后，尝试切回主模型
FALLBACK_RECOVERY_THRESHOLD = 15
# 触发 fallback 的错误码/关键词（模型繁忙、限流等）
FALLBACK_TRIGGER_KEYWORDS = [
    "模型服务繁忙", "4029", "rate limit", "too many requests",
    "quota exceeded", "overloaded", "请求过于频繁", "限流", "配额",
]


class VenusLLM:
    """维纳斯平台 LLM 封装

    支持自动 Fallback：当主模型（如 deepseek-v3.2）因繁忙/限流失败时，
    自动切换到备选模型继续工作，一段时间后再尝试切回主模型。
    """

    def __init__(self):
        self.url = f"{settings.LLM_API_BASE}/chat/completions"
        self.token = settings.LLM_API_KEY
        self.model = settings.LLM_MODEL_NAME
        self.temperature = settings.LLM_TEMPERATURE
        self.max_tokens = settings.LLM_MAX_TOKENS
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.token}",
        }

        # ── Fallback 状态 ──
        self._primary_model = self.model           # 主模型名称（不变）
        self._active_model = self.model            # 当前实际使用的模型
        self._is_using_fallback = False             # 是否正在使用备选模型
        self._fallback_success_count = 0            # 备选模型连续成功次数
        self._total_fallbacks = 0                   # 累计 fallback 次数

    def _is_fallback_trigger(self, error_text: str) -> bool:
        """判断错误是否应该触发 fallback（模型繁忙/限流等）"""
        error_lower = error_text.lower()
        return any(kw.lower() in error_lower for kw in FALLBACK_TRIGGER_KEYWORDS)

    def _switch_to_fallback(self, failed_model: str) -> str | None:
        """切换到下一个可用的备选模型，返回新模型名；无可用则返回 None"""
        candidates = [m for m in FALLBACK_MODELS if m != failed_model]
        if not candidates:
            return None
        # 选第一个备选模型
        fallback = candidates[0]
        self._active_model = fallback
        self._is_using_fallback = True
        self._fallback_success_count = 0
        self._total_fallbacks += 1
        logger.warning(
            f"🔄 模型 [{failed_model}] 不可用，自动切换到备选模型 [{fallback}]"
            f"（第 {self._total_fallbacks} 次 fallback）"
        )
        return fallback

    def _try_recover_primary(self):
        """备选模型连续成功一定次数后，尝试切回主模型"""
        if not self._is_using_fallback:
            return
        self._fallback_success_count += 1
        if self._fallback_success_count >= FALLBACK_RECOVERY_THRESHOLD:
            logger.info(
                f"🔁 备选模型已连续成功 {self._fallback_success_count} 次，"
                f"尝试切回主模型 [{self._primary_model}]"
            )
            self._active_model = self._primary_model
            self._is_using_fallback = False
            self._fallback_success_count = 0

    def _call_model(
        self,
        model_name: str,
        messages: list[dict],
        temperature: float,
        max_tokens: int,
        retry: int = 3,
    ) -> str:
        """对指定模型发送请求（含重试），返回回复文本

        如果遇到 fallback 触发条件（模型繁忙等），抛出 VenusModelBusyError。
        """
        payload = {
            "model": model_name,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        for attempt in range(retry):
            try:
                response = requests.post(
                    self.url,
                    headers=self.headers,
                    data=json.dumps(payload),
                    timeout=300,
                )

                if response.status_code != 200:
                    error_detail = response.text[:500]

                    # 检查是否应触发 fallback（模型繁忙等）
                    if self._is_fallback_trigger(error_detail):
                        raise _VenusModelBusyError(
                            f"模型 [{model_name}] 繁忙: {error_detail[:200]}"
                        )

                    # 限流错误立即抛出，不浪费重试次数
                    _check_rate_limit(response.status_code, error_detail)

                    logger.warning(
                        f"维纳斯 API 返回 {response.status_code} "
                        f"(模型={model_name}, 尝试 {attempt + 1}/{retry}): {error_detail}"
                    )
                    if attempt < retry - 1:
                        time.sleep(2 ** attempt)
                        continue
                    raise RuntimeError(
                        f"维纳斯 API 错误 {response.status_code}: {error_detail}"
                    )

                data = response.json()
                content = data["choices"][0]["message"]["content"]

                # 记录 token 用量
                usage = data.get("usage", {})
                if usage:
                    logger.debug(
                        f"Token 用量 (模型={model_name}): "
                        f"prompt={usage.get('prompt_tokens', '?')}, "
                        f"completion={usage.get('completion_tokens', '?')}"
                    )

                return content

            except _VenusModelBusyError:
                raise  # 直接上抛，由 chat() 处理 fallback
            except requests.exceptions.Timeout:
                logger.warning(
                    f"请求超时 (模型={model_name}, 尝试 {attempt + 1}/{retry})"
                )
                if attempt < retry - 1:
                    time.sleep(2 ** attempt)
                    continue
                # 超时也触发 fallback，而不是直接抛出异常
                raise _VenusModelBusyError(
                    f"模型 [{model_name}] 连续 {retry} 次超时"
                )
            except requests.exceptions.ConnectionError as e:
                logger.warning(
                    f"连接错误 (模型={model_name}, 尝试 {attempt + 1}/{retry}): {e}"
                )
                if attempt < retry - 1:
                    time.sleep(2 ** attempt)
                    continue
                # 连接错误也触发 fallback
                raise _VenusModelBusyError(
                    f"模型 [{model_name}] 连接错误: {str(e)[:100]}"
                )

        raise RuntimeError(f"维纳斯 API 调用失败 (模型={model_name})，已耗尽重试次数")

    def chat(
        self,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
        retry: int = 3,
    ) -> str:
        """发送聊天请求（支持自动 Fallback）

        流程：
        1. 使用当前活跃模型发送请求
        2. 如果模型繁忙/限流，自动切换到备选模型重试
        3. 备选模型连续成功一定次数后，自动切回主模型

        Args:
            messages: [{"role": "system"|"user"|"assistant", "content": "..."}]
            temperature: 温度参数
            max_tokens: 最大 token 数
            retry: 重试次数

        Returns:
            LLM 回复文本
        """
        temp = temperature or self.temperature
        tokens = max_tokens or self.max_tokens
        current_model = self._active_model

        try:
            content = self._call_model(current_model, messages, temp, tokens, retry)
            # 成功：如果正在用备选模型，累计成功次数，适时切回
            self._try_recover_primary()
            return content

        except _VenusModelBusyError as e:
            logger.warning(f"⚠️ {e}")
            # 尝试 fallback 到备选模型
            fallback_model = self._switch_to_fallback(current_model)
            if fallback_model is None:
                # 没有备选模型了，作为限流错误上抛
                raise VenusRateLimitError(str(e)) from e

            # 用备选模型重试
            try:
                content = self._call_model(
                    fallback_model, messages, temp, tokens, retry
                )
                logger.info(
                    f"✅ 备选模型 [{fallback_model}] 调用成功"
                )
                return content
            except _VenusModelBusyError:
                # 备选也繁忙，抛出限流错误
                raise VenusRateLimitError(
                    f"主模型 [{current_model}] 和备选模型 [{fallback_model}] 均不可用"
                ) from e

    def simple_chat(self, user_message: str, system_message: str = "") -> str:
        """简单的单轮对话

        Args:
            user_message: 用户消息
            system_message: 系统消息

        Returns:
            LLM 回复
        """
        messages = []
        if system_message:
            messages.append({"role": "system", "content": system_message})
        messages.append({"role": "user", "content": user_message})
        return self.chat(messages)


# 全局实例
venus_llm = VenusLLM()


class VenusQALLM:
    """维纳斯平台 QA LLM 封装（用于知识库问答 / AI 总结）

    与 VenusLLM 共享 Venus 平台，但使用独立的模型配置，
    保持变量名 deepseek_llm 以兼容已有引用。
    """

    def __init__(self):
        self.url = f"{settings.QA_LLM_API_BASE}/chat/completions"
        # 优先使用独立的 QA key，若为空则复用主 LLM key
        self.api_key = settings.QA_LLM_API_KEY or settings.LLM_API_KEY
        self.model = settings.QA_LLM_MODEL
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

    def chat(
        self,
        messages: list[dict],
        temperature: float = 0.3,
        max_tokens: int = 4096,
        retry: int = 3,
    ) -> str:
        """发送聊天请求"""
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        for attempt in range(retry):
            try:
                response = requests.post(
                    self.url,
                    headers=self.headers,
                    data=json.dumps(payload),
                    timeout=120,
                )

                if response.status_code != 200:
                    error_detail = response.text[:500]
                    logger.warning(
                        f"Venus QA API 返回 {response.status_code} (尝试 {attempt + 1}/{retry}): {error_detail}"
                    )
                    if attempt < retry - 1:
                        time.sleep(2 ** attempt)
                        continue
                    raise RuntimeError(f"Venus QA API 错误 {response.status_code}: {error_detail}")

                data = response.json()
                content = data["choices"][0]["message"]["content"]

                # 去除 <think> 标签
                content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()

                usage = data.get("usage", {})
                if usage:
                    logger.debug(
                        f"Venus QA Token: prompt={usage.get('prompt_tokens', '?')}, "
                        f"completion={usage.get('completion_tokens', '?')}"
                    )

                return content

            except requests.exceptions.Timeout:
                logger.warning(f"Venus QA 请求超时 (尝试 {attempt + 1}/{retry})")
                if attempt < retry - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise
            except requests.exceptions.ConnectionError as e:
                logger.warning(f"Venus QA 连接错误 (尝试 {attempt + 1}/{retry}): {e}")
                if attempt < retry - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise

        raise RuntimeError("Venus QA API 调用失败，已耗尽重试次数")


# 保持变量名 deepseek_llm 以兼容 rag/engine.py 和 api/routes.py 中的引用
deepseek_llm = VenusQALLM()
