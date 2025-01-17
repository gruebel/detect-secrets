import inspect
from typing import Any
from typing import Callable
from typing import cast
from typing import Tuple
from typing import Union

from ..custom_types import SelfAwareCallable


def call_function_with_arguments(
    func: Union[Callable, SelfAwareCallable],
    **kwargs: Any,
) -> Any:
    """
    :raises: TypeError
    """
    # First, we ensure that the function we're going to inject values into is self-aware.
    if not isinstance(func, SelfAwareCallable):
        function = make_function_self_aware(func)
    else:
        function = func

    # If `function` is derived from a method, we add the instance of the class by default.
    # However, if `function` is a method itself, it will already carry the reference of the
    # instance, so we don't need to explicitly include it.
    if inspect.ismethod(func) and not inspect.ismethod(function):
        # We also use get_injectable_variables (instead of hardcoding "self") to make sure that
        # this also handles cases where the developer doesn't name the first parameter `self`.
        kwargs[get_injectable_variables(func)[0]] = func.__self__

    variables_to_inject = set(kwargs.keys())
    values = {
        key: kwargs[key]
        for key in (variables_to_inject & function.injectable_variables)
    }

    return function(**values)


def make_function_self_aware(func: Callable) -> SelfAwareCallable:
    """
    A SelfAwareCallable is one that is aware of its own injectable variables, through the
    `func.injectable_variables` attribute.
    """
    if hasattr(func, 'injectable_variables'):
        return cast(SelfAwareCallable, func)

    # We can't add arbitrary attributes to methods, but we can to functions. Therefore,
    # we need to reference the underlying function itself.
    if inspect.ismethod(func):
        klass = func.__self__.__class__
        function = getattr(klass, func.__name__)
        function.injectable_variables = set(get_injectable_variables(func))

        function.path = f'{klass}.{func.__name__}'
    else:
        function = func
        function.path = func.__name__

    return cast(SelfAwareCallable, function)


def get_injectable_variables(func: Callable) -> Tuple[str, ...]:
    """
    The easiest way to understand this is to see it as an example:
        >>> def func(a, b=1, *args, c, d=2, **kwargs):
        ...     e = 5
        >>>
        >>> print(func.__code__.co_varnames)
        ('a', 'b', 'c', 'd', 'args', 'kwargs', 'e')
        >>> print(func.__code__.co_argcount)    # `a` and `b`
        2
        >>> print(func.__code__.co_kwonlyargcount)  # `c` and `d`
        2
    """
    variable_names = func.__code__.co_varnames
    arg_count = func.__code__.co_argcount + func.__code__.co_kwonlyargcount

    return variable_names[:arg_count]
