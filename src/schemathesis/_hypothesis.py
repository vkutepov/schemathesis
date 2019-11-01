"""Provide strategies for given endpoint(s) definition."""
import asyncio
import re
from functools import partial
from typing import Any, Callable, Dict, Generator, Optional

import hypothesis
import hypothesis.strategies as st
from hypothesis_jsonschema import from_schema
from requests.exceptions import InvalidHeader  # type: ignore
from requests.utils import check_header_validity  # type: ignore

from schemathesis.exceptions import InvalidEndpoint

from ._compat import handle_warnings
from .models import Case, Endpoint

PARAMETERS = frozenset(("path_parameters", "headers", "cookies", "query", "body", "form_data"))


def create_test(
    endpoint: Endpoint, test: Callable, settings: Optional[hypothesis.settings] = None, skip_invalid: bool = True
) -> Callable:
    """Create a Hypothesis test."""
    strategy = endpoint.as_strategy()
    wrapped_test = hypothesis.given(case=strategy)(test)
    original_test = get_original_test(test)
    if asyncio.iscoroutinefunction(original_test):
        wrapped_test.hypothesis.inner_test = make_async_test(original_test)  # type: ignore
    if settings is not None:
        wrapped_test = settings(wrapped_test)
    return add_examples(wrapped_test, endpoint)


def get_original_test(test: Callable) -> Callable:
    """Get the original test function even if it is wrapped by `hypothesis.settings` decorator.

    Applies only to Hypothesis pre 4.42.4 versions.
    """
    # `settings` decorator is applied
    if getattr(test, "_hypothesis_internal_settings_applied", False) and hypothesis.__version_info__ < (4, 42, 4):
        # This behavior was changed due to a bug - https://github.com/HypothesisWorks/hypothesis/issues/2160
        # And since Hypothesis 4.42.4 is no longer required
        return test._hypothesis_internal_test_function_without_warning  # type: ignore
    return test


def make_async_test(test: Callable) -> Callable:
    def async_run(*args: Any, **kwargs: Any) -> None:
        loop = asyncio.get_event_loop()
        coro = test(*args, **kwargs)
        future = asyncio.ensure_future(coro, loop=loop)
        loop.run_until_complete(future)

    return async_run


def get_examples(endpoint: Endpoint) -> Generator[Case, None, None]:
    for name in PARAMETERS:
        parameter = getattr(endpoint, name)
        if "example" in parameter:
            with handle_warnings():
                strategies = {other: from_schema(getattr(endpoint, other)) for other in PARAMETERS - {name}}
                static_parameters = {name: parameter["example"]}
                yield _get_case_strategy(endpoint, static_parameters, strategies).example()


def add_examples(test: Callable, endpoint: Endpoint) -> Callable:
    """Add examples to the Hypothesis test, if they are specified in the schema."""
    for case in get_examples(endpoint):
        test = hypothesis.example(case)(test)
    return test


# Adapted from http.client._is_illegal_header_value
INVALID_HEADER_RE = re.compile(r"\n(?![ \t])|\r(?![ \t\n])")


def _is_latin_1_encodable(value: str) -> bool:
    """Header values are encoded to latin-1 before sending.

    We need to generate valid payload.
    """
    try:
        value.encode("latin-1")
        return True
    except UnicodeEncodeError:
        return False


def _has_invalid_characters(name: str, value: str) -> bool:
    try:
        check_header_validity((name, value))
        return bool(INVALID_HEADER_RE.search(value))
    except InvalidHeader:
        return True


def is_valid_header(headers: Dict[str, str]) -> bool:
    """Verify if the generated headers are valid."""
    for name, value in headers.items():
        if not _is_latin_1_encodable(value):
            return False
        if _has_invalid_characters(name, value):
            return False
    return True


def get_case_strategy(endpoint: Endpoint) -> Optional[st.SearchStrategy]:
    """Create a strategy for a complete test case.

    Path & endpoint are static, the others are JSON schemas.
    """
    static_kwargs = {"path": endpoint.path, "method": endpoint.method, "base_url": endpoint.base_url}
    try:
        strategies = {
            "path_parameters": from_schema(endpoint.path_parameters),
            "headers": from_schema(endpoint.headers).filter(is_valid_header),  # type: ignore
            "cookies": from_schema(endpoint.cookies),
            "query": from_schema(endpoint.query),
            "form_data": from_schema(endpoint.form_data),
        }
        return _get_case_strategy(endpoint, static_kwargs, strategies)
    except AssertionError:
        raise InvalidEndpoint


def _get_case_strategy(
    endpoint: Endpoint, extra_static_parameters: Dict[str, Any], strategies: Dict[str, st.SearchStrategy]
) -> st.SearchStrategy:
    static_parameters = {
        "path": endpoint.path,
        "method": endpoint.method,
        "base_url": endpoint.base_url,
        **extra_static_parameters,
    }
    if endpoint.method == "GET":
        static_parameters["body"] = None
        strategies.pop("body", None)
    elif "body" not in static_parameters:
        strategies["body"] = from_schema(endpoint.body)
    return st.builds(partial(Case, **static_parameters), **strategies)


def register_string_format(name: str, strategy: st.SearchStrategy) -> None:
    if not isinstance(name, str):
        raise TypeError(f"name must be of type {str}, not {type(name)}")
    if not isinstance(strategy, st.SearchStrategy):
        raise TypeError(f"strategy must be of type {st.SearchStrategy}, not {type(strategy)}")
    from hypothesis_jsonschema._impl import STRING_FORMATS  # pylint: disable=import-outside-toplevel

    STRING_FORMATS[name] = strategy
