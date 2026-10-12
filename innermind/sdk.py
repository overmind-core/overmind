"""
Trace an agent so innermind can score its spans and trajectories.

    from innermind.decorators import Kind, init, observe, record_llm_call, session

    init()

    @observe(kind=Kind.TOOL)
    def search_flights(origin: str, destination: str) -> list[dict]: ...

    @observe(kind=Kind.LLM, expectations=["picks a flight from the search results"])
    def choose(question: str, flights: list[dict]) -> dict:
        response = client.chat.completions.create(model=MODEL, messages=...)
        record_llm_call(response.model, response.usage.total_tokens)
        return response.choices[0].message.model_dump()

    @observe(
        kind=Kind.WORKFLOW,
        expectations=["names the booked flight and its price"],
        reference_trajectory=["search_flights", "book_flight"],
    )
    def book(question: str) -> str: ...

    with session(session_id=user.id, conversation_id=chat.id):
        book("cheapest flight from London to Lisbon on Friday")

What each argument feeds:
    kind                  Picks the span processor. Signals and trajectory metrics read tool
                          spans (failures, loops, reference match) and llm spans (latency,
                          tokens, cost, model changes, requested tools) by kind.
    expectations          One metric per item, judged against intent, input and output.
                          A span without expectations is not scored but still counts as a
                          step of its trajectory.
    intent                What the step is for. Omitted, it is inherited from the parent span;
                          a root span uses its own input.
    input_key             The argument that is the input. Omitted, a single argument is the
                          input, otherwise every argument as JSON.
    ignore_keys           Arguments never captured.
    reference_trajectory  On a root span: the tool names a correct run calls, in order.

Capture:
    - The return value is the output; a generator's output is its yielded items, joined
      when they are all strings. Non-string values are stored as JSON.
    - Return an LLM's assistant message (or the whole completion) rather than only its text
      so the tool calls it requested are visible.
    - Raising marks the span as an error. Returning an error string does not.
    - session() stamps every span started inside it, so signals can group a user's spans
      by session and turns by conversation.
    - Each trace is one trajectory: the outermost observed call is its root.
"""

import inspect
import json
import os
import sys
import time
import warnings
from contextlib import contextmanager
from contextvars import ContextVar
from enum import StrEnum
from functools import wraps

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import StatusCode


class ATTR:
    KIND = "overmind.kind"
    INTENT = "overmind.intent"
    INPUT = "overmind.input"
    OUTPUT = "overmind.output"
    EXPECTATIONS = "overmind.expectations"
    REFERENCE_TRAJECTORY = "overmind.reference_trajectory"
    SESSION_ID = "overmind.session_id"
    CONVERSATION_ID = "overmind.conversation_id"

    FILENAME = "overmind.filename"
    FIRSTLINENO = "overmind.firstlineno"
    QUALNAME = "overmind.qualname"

    LLM_MODEL = "genai.model"
    LLM_TOTAL_TOKENS = "genai.total_tokens"
    LLM_COST = "genai.cost"
    LLM_ELAPSED_SECONDS = "genai.elapsed_seconds"


class Kind(StrEnum):
    WORKFLOW = "workflow"
    FUNCTION = "function"
    LLM = "llm"
    TOOL = "tool"


_session: ContextVar[tuple[str, str]] = ContextVar("overmind_session", default=("", ""))


@contextmanager
def session(session_id: str, conversation_id: str = ""):
    token = _session.set((session_id, conversation_id))
    try:
        yield
    finally:
        _session.reset(token)


def record_llm_call(model: str, total_tokens: int | None = None, cost: float | None = None):
    """Call inside an observed llm function, once its response is back."""
    span = trace.get_current_span()
    _set(span, ATTR.LLM_MODEL, model)
    if total_tokens is not None:
        _set(span, ATTR.LLM_TOTAL_TOKENS, total_tokens)
    if cost is not None:
        _set(span, ATTR.LLM_COST, cost)


def _jsonable(value):
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return repr(value)


def _text(value) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=_jsonable)
    except (TypeError, ValueError):
        return repr(value)


def _set(span, key, value):
    # A capture must never fail the traced call.
    try:
        span.set_attribute(key, value)
    except Exception:
        return


def _use(span):
    """Make ``span`` current for the body only.

    The token is detached on the way out, on the same thread or task that
    attached it. Holding it across ``yield`` would leak this span into the
    caller and break if the generator resumes on another thread.
    """
    return trace.use_span(
        span,
        end_on_exit=False,
        record_exception=False,
        set_status_on_exception=False,
    )


def observe(
    kind: Kind = Kind.FUNCTION,
    name: str | None = None,
    intent: str | None = None,
    input_key: str | None = None,
    ignore_keys: tuple[str, ...] = (),
    expectations: tuple[str, ...] = (),
    reference_trajectory: tuple[str, ...] = (),
):
    def decorator(func):
        signature = inspect.signature(func)
        target = inspect.unwrap(func)
        code = target.__code__
        span_name = name or func.__name__
        if input_key is not None and input_key not in signature.parameters:
            raise ValueError(f"{span_name} has no argument {input_key!r}")
        if expectations and not signature.parameters:
            warnings.warn(f"{span_name} has expectations but takes no input", stacklevel=2)

        def call_input(args, func_kwargs) -> str | None:
            bound = signature.bind(*args, **func_kwargs)
            bound.apply_defaults()
            if input_key is not None:
                return _text(bound.arguments[input_key])
            arguments = {
                key: value
                for key, value in bound.arguments.items()
                if key not in ignore_keys and key not in ("self", "cls")
            }
            if not arguments:
                return None
            if len(arguments) == 1:
                (value,) = arguments.values()
                return _text(value)
            return _text(arguments)

        def start(args, func_kwargs):
            # Per call, not at decoration: init() may run after the function is wrapped.
            span = trace.get_tracer(__name__).start_span(span_name)
            try:
                session_id, conversation_id = _session.get()
                _set(span, ATTR.KIND, str(kind))
                _set(span, ATTR.FILENAME, code.co_filename)
                _set(span, ATTR.FIRSTLINENO, code.co_firstlineno)
                _set(span, ATTR.QUALNAME, target.__qualname__)
                for key, value in (
                    (ATTR.INTENT, intent),
                    (ATTR.EXPECTATIONS, tuple(expectations)),
                    (ATTR.REFERENCE_TRAJECTORY, tuple(reference_trajectory)),
                    (ATTR.SESSION_ID, session_id),
                    (ATTR.CONVERSATION_ID, conversation_id),
                    (ATTR.INPUT, call_input(args, func_kwargs)),
                ):
                    if value:
                        _set(span, key, value)
            except BaseException as exc:
                finish(span, time.perf_counter(), exc, None)
                raise
            return span, time.perf_counter()

        def finish(span, started, exc, output):
            try:
                if kind == Kind.LLM:
                    _set(span, ATTR.LLM_ELAPSED_SECONDS, time.perf_counter() - started)
                if output is not None:
                    _set(span, ATTR.OUTPUT, _text(output))
                elif expectations and exc is None:
                    warnings.warn(
                        f"{span_name} has expectations but no output; it will not be scored",
                        stacklevel=2,
                    )
                if exc is None:
                    span.set_status(StatusCode.OK)
                else:
                    span.record_exception(exc)
                    span.set_status(StatusCode.ERROR, f"{type(exc).__name__}: {exc}")
            finally:
                span.end()

        def joined(items: list):
            if not items:
                return None
            if all(isinstance(item, str) for item in items):
                return "".join(items)
            return items

        if inspect.isasyncgenfunction(func):

            @wraps(func)
            async def async_gen_wrapper(*args, **func_kwargs):
                span, started = start(args, func_kwargs)
                agen = func(*args, **func_kwargs)
                items = []
                recorded = None
                try:
                    sent = None
                    to_throw = None
                    while True:
                        with _use(span):
                            try:
                                item = (
                                    await agen.asend(sent)
                                    if to_throw is None
                                    else await agen.athrow(*to_throw)
                                )
                            except StopAsyncIteration:
                                break
                        items.append(item)
                        try:
                            sent = yield item
                            to_throw = None
                        except GeneratorExit:
                            raise
                        except BaseException:
                            sent = None
                            to_throw = sys.exc_info()
                except BaseException as exc:
                    if not isinstance(exc, GeneratorExit):
                        recorded = exc
                    raise
                finally:
                    try:
                        await agen.aclose()
                    finally:
                        finish(span, started, recorded, joined(items))

            return async_gen_wrapper

        if inspect.iscoroutinefunction(func):

            @wraps(func)
            async def async_wrapper(*args, **func_kwargs):
                span, started = start(args, func_kwargs)
                try:
                    with _use(span):
                        result = await func(*args, **func_kwargs)
                except BaseException as exc:
                    finish(span, started, exc, None)
                    raise
                finish(span, started, None, result)
                return result

            return async_wrapper

        if inspect.isgeneratorfunction(func):

            @wraps(func)
            def gen_wrapper(*args, **func_kwargs):
                span, started = start(args, func_kwargs)
                gen = func(*args, **func_kwargs)
                items = []
                returned = None
                recorded = None
                try:
                    sent = None
                    to_throw = None
                    while True:
                        with _use(span):
                            try:
                                item = gen.send(sent) if to_throw is None else gen.throw(*to_throw)
                            except StopIteration as stop:
                                returned = stop.value
                                break
                        items.append(item)
                        try:
                            sent = yield item
                            to_throw = None
                        except GeneratorExit:
                            raise
                        except BaseException:
                            sent = None
                            to_throw = sys.exc_info()
                except BaseException as exc:
                    if not isinstance(exc, GeneratorExit):
                        recorded = exc
                    raise
                finally:
                    try:
                        gen.close()
                    finally:
                        output = returned if returned is not None else joined(items)
                        finish(span, started, recorded, output)
                return returned

            return gen_wrapper

        @wraps(func)
        def wrapper(*args, **func_kwargs):
            span, started = start(args, func_kwargs)
            try:
                with _use(span):
                    result = func(*args, **func_kwargs)
            except BaseException as exc:
                finish(span, started, exc, None)
                raise
            finish(span, started, None, result)
            return result

        return wrapper

    return decorator


def init(service_name: str = "overmind") -> None:
    exporter = OTLPSpanExporter(
        endpoint=os.environ["OVERMIND_TRACES_ENDPOINT"],
        headers={"Authorization": f"Bearer {os.environ['OVERMIND_TRACES_KEY']}"},
    )
    span_processor = BatchSpanProcessor(
        exporter,
        schedule_delay_millis=int(os.environ.get("OVERMIND_SPAN_FLUSH_INTERVAL_MS", "2000")),
        max_export_batch_size=int(os.environ.get("OVERMIND_SPAN_MAX_EXPORT_BATCH_SIZE", "256")),
    )
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(span_processor)
    provider._overmind_processors_attached = True  # noqa: SLF001
    trace.set_tracer_provider(provider)
