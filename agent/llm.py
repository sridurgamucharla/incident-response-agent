"""Gemini client: fallback chain + severity priority queue, built for the free tier.

- Every call goes through ONE priority queue. Priority = severity (SEV1 = 1 first), so when we are
  rate-limited the SEV1 triage is served before a SEV3 or a postmortem draft.
- Model chain: GEMINI_MODEL, then each of GEMINI_FALLBACK_MODELS. On 429/503 (and timeouts) a
  request backs off 2s, 4s, 8s on the same model, then moves to the next model. A request that is
  backing off goes *back into the queue* instead of blocking it, so higher-priority work overtakes it.
- If every model fails, LLMUnavailable is raised and the caller takes the no-LLM path.
- Every attempt is logged (model, status, latency) so the dashboard can show which model answered.
"""
import asyncio
import itertools
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from openai import (APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI,
                    InternalServerError, RateLimitError)

from agent.config import get_settings

log = logging.getLogger("dejavu.llm")

BACKOFF_S = [2, 4, 8]  # retries per model on 429/503
SEVERITY_PRIORITY = {"SEV1": 1, "SEV2": 2, "SEV3": 3}
BACKGROUND_PRIORITY = 9  # postmortem drafts, memory-off comparisons


class LLMUnavailable(Exception):
    """Every model in the chain failed; take the no-LLM path."""


@dataclass
class LLMResult:
    message: Any  # openai ChatCompletionMessage
    model: str
    attempts: int
    latency_ms: int


@dataclass(order=True)
class _Request:
    priority: int
    seq: int
    messages: list = field(compare=False)
    tools: list | None = field(compare=False)
    tool_choice: str | None = field(compare=False)
    label: str = field(compare=False)
    future: asyncio.Future = field(compare=False)
    model_index: int = field(default=0, compare=False)
    retry: int = field(default=0, compare=False)
    attempts: int = field(default=0, compare=False)
    not_before: float = field(default=0.0, compare=False)
    errors: list = field(default_factory=list, compare=False)


class GeminiClient:
    def __init__(self, on_call: Callable[[dict], Awaitable[None]] | None = None) -> None:
        s = get_settings()
        self.models = s.gemini_models
        self.client = AsyncOpenAI(api_key=s.gemini_api_key, base_url=s.gemini_base_url,
                                  max_retries=0, timeout=45)
        self.min_interval = 60 / max(s.gemini_rpm, 1)
        self.on_call = on_call  # async hook, e.g. WebSocket broadcast
        self.calls: deque[dict] = deque(maxlen=200)  # recent attempts, for /llm/status
        self._queue: asyncio.PriorityQueue[_Request] = asyncio.PriorityQueue()
        self._seq = itertools.count()
        self._last_call = 0.0
        self._worker: asyncio.Task | None = None

    # ---------------- public ----------------

    async def chat(self, messages: list, *, priority: int, tools: list | None = None,
                   tool_choice: str | None = None, label: str = "") -> LLMResult:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run())
        future = asyncio.get_running_loop().create_future()
        await self._queue.put(_Request(priority, next(self._seq), messages, tools, tool_choice, label, future))
        return await future

    def status(self) -> dict:
        return {"models": self.models, "queue_depth": self._queue.qsize(), "recent_calls": list(self.calls)[-20:]}

    async def close(self) -> None:
        if self._worker:
            self._worker.cancel()
        await self.client.close()

    # ---------------- worker ----------------

    async def _run(self) -> None:
        while True:
            req = await self._queue.get()
            if req.future.done():
                continue
            wait = req.not_before - time.monotonic()
            if wait > 0:  # top request is backing off: put it back so anything more urgent can go first
                await self._queue.put(req)
                await asyncio.sleep(min(wait, 0.5))
                continue
            await self._pace()
            await self._attempt(req)

    async def _pace(self) -> None:
        wait = self._last_call + self.min_interval - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_call = time.monotonic()

    async def _attempt(self, req: _Request) -> None:
        model = self.models[req.model_index]
        req.attempts += 1
        start = time.monotonic()
        try:
            kwargs: dict = {"model": model, "messages": req.messages}
            if req.tools:
                kwargs.update(tools=req.tools, tool_choice=req.tool_choice or "auto")
            resp = await self.client.chat.completions.create(**kwargs)
        except (RateLimitError, InternalServerError, APITimeoutError, APIConnectionError) as e:
            status = getattr(e, "status_code", None) or type(e).__name__
            await self._record(req, model, status, start, retryable=True)
            req.errors.append(f"{model}: {status}")
            if req.retry < len(BACKOFF_S):
                req.not_before = time.monotonic() + BACKOFF_S[req.retry]
                req.retry += 1
            else:
                req.model_index, req.retry, req.not_before = req.model_index + 1, 0, 0.0
            await self._requeue_or_fail(req)
            return
        except APIStatusError as e:  # 400/404 etc: this model won't work, try the next one right away
            await self._record(req, model, e.status_code, start, retryable=False)
            req.errors.append(f"{model}: {e.status_code} {str(e.message)[:120]}")
            req.model_index, req.retry, req.not_before = req.model_index + 1, 0, 0.0
            await self._requeue_or_fail(req)
            return
        except Exception as e:  # noqa: BLE001 - never let the worker die
            await self._record(req, model, type(e).__name__, start, retryable=False)
            req.future.set_exception(LLMUnavailable(f"{model}: {e}"))
            return

        await self._record(req, model, "ok", start, retryable=False)
        req.future.set_result(LLMResult(resp.choices[0].message, model, req.attempts,
                                        int((time.monotonic() - start) * 1000)))

    async def _requeue_or_fail(self, req: _Request) -> None:
        if req.model_index >= len(self.models):
            req.future.set_exception(LLMUnavailable("; ".join(req.errors)))
        else:
            await self._queue.put(req)

    async def _record(self, req: _Request, model: str, status, start: float, retryable: bool) -> None:
        entry = {
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "label": req.label, "priority": req.priority, "model": model, "status": status,
            "latency_ms": int((time.monotonic() - start) * 1000), "retryable": retryable,
        }
        self.calls.append(entry)
        log.info("llm %s %s -> %s (%sms)", req.label, model, status, entry["latency_ms"])
        if self.on_call:
            try:
                await self.on_call(entry)
            except Exception:  # noqa: BLE001 - telemetry must never break a call
                log.exception("on_call hook failed")
