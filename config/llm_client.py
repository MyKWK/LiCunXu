"""LLM 客户端

维纳斯平台: 用于数据摄入/清洗等内部任务
DeepSeek: 用于知识库问答（面向用户）
"""

import json
import re
import time

import requests
from loguru import logger

from config.settings import settings


class VenusLLM:
    """维纳斯平台 LLM 封装"""

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

    def chat(
        self,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
        retry: int = 3,
    ) -> str:
        """发送聊天请求

        Args:
            messages: [{"role": "system"|"user"|"assistant", "content": "..."}]
            temperature: 温度参数
            max_tokens: 最大 token 数
            retry: 重试次数

        Returns:
            LLM 回复文本
        """
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature or self.temperature,
            "max_tokens": max_tokens or self.max_tokens,
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
                    logger.warning(
                        f"维纳斯 API 返回 {response.status_code} (尝试 {attempt + 1}/{retry}): {error_detail}"
                    )
                    if attempt < retry - 1:
                        time.sleep(2 ** attempt)
                        continue
                    raise RuntimeError(f"维纳斯 API 错误 {response.status_code}: {error_detail}")

                data = response.json()
                content = data["choices"][0]["message"]["content"]
                
                # 记录 token 用量
                usage = data.get("usage", {})
                if usage:
                    logger.debug(
                        f"Token 用量: prompt={usage.get('prompt_tokens', '?')}, "
                        f"completion={usage.get('completion_tokens', '?')}"
                    )

                return content

            except requests.exceptions.Timeout:
                logger.warning(f"请求超时 (尝试 {attempt + 1}/{retry})")
                if attempt < retry - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise
            except requests.exceptions.ConnectionError as e:
                logger.warning(f"连接错误 (尝试 {attempt + 1}/{retry}): {e}")
                if attempt < retry - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise

        raise RuntimeError("维纳斯 API 调用失败，已耗尽重试次数")

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


class DeepSeekLLM:
    """DeepSeek LLM 封装（OpenAI 兼容接口）"""

    def __init__(self):
        self.url = f"{settings.DEEPSEEK_API_BASE}/chat/completions"
        self.api_key = settings.DEEPSEEK_API_KEY
        self.model = settings.DEEPSEEK_MODEL
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
                        f"DeepSeek API 返回 {response.status_code} (尝试 {attempt + 1}/{retry}): {error_detail}"
                    )
                    if attempt < retry - 1:
                        time.sleep(2 ** attempt)
                        continue
                    raise RuntimeError(f"DeepSeek API 错误 {response.status_code}: {error_detail}")

                data = response.json()
                content = data["choices"][0]["message"]["content"]

                # 去除 <think> 标签
                content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()

                usage = data.get("usage", {})
                if usage:
                    logger.debug(
                        f"DeepSeek Token: prompt={usage.get('prompt_tokens', '?')}, "
                        f"completion={usage.get('completion_tokens', '?')}"
                    )

                return content

            except requests.exceptions.Timeout:
                logger.warning(f"DeepSeek 请求超时 (尝试 {attempt + 1}/{retry})")
                if attempt < retry - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise
            except requests.exceptions.ConnectionError as e:
                logger.warning(f"DeepSeek 连接错误 (尝试 {attempt + 1}/{retry}): {e}")
                if attempt < retry - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise

        raise RuntimeError("DeepSeek API 调用失败，已耗尽重试次数")


deepseek_llm = DeepSeekLLM()
