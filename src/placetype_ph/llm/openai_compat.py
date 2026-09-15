from __future__ import annotations

import os

from openai import OpenAI

from .base import ChatBackend


class OpenAICompatibleBackend(ChatBackend):
    def __init__(self, base_url: str, api_key: str, model: str):
        self.base_url = base_url.rstrip("/")
        self.client = OpenAI(base_url=self.base_url, api_key=api_key)
        self.model_name = model

    @property
    def cache_identity(self) -> str:
        # The same model name served by HF and a local Ollama quantization need not make
        # identical decisions. Provider/base URL is therefore part of cache provenance.
        return f"{self.base_url}|{self.model_name}"

    @classmethod
    def huggingface(cls, model: str = "Qwen/Qwen3-4B-Instruct-2507") -> OpenAICompatibleBackend:
        token = os.getenv("HF_TOKEN")
        if not token:
            raise RuntimeError("HF_TOKEN is required for the Hugging Face backend")
        return cls("https://router.huggingface.co/v1", token, model)

    @classmethod
    def ollama(cls, model: str = "qwen3:4b") -> OpenAICompatibleBackend:
        return cls("http://127.0.0.1:11434/v1", "ollama", model)

    def complete(self, system: str, user: str, temperature: float = 0.0) -> str:
        response = self.client.chat.completions.create(
            model=self.model_name,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
        )
        content = response.choices[0].message.content
        return content or ""
