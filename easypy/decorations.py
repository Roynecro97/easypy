"""
This module is about making it easier to create decorators
"""

from functools import wraps, partial, update_wrapper
from operator import attrgetter
from abc import ABCMeta, abstractmethod
import inspect

from .tokens import AUTO


def parametrizeable_decorator(deco):
    @wraps(deco)
    def inner(func=None, **kwargs):
        if func is None:
            return partial(deco, **kwargs)
        else:
            return wraps(func)(deco(func, **kwargs))
    return inner


def wrapper_decorator(deco):
    @wraps(deco)
    def inner(func):
        return wraps(func)(deco(func))
    return inner


def reusable_contextmanager(context_manager):
    """
    Allows the generator-based context manager to be used more than once
    """

    if not hasattr(context_manager, '_recreate_cm'):
        return context_manager  # context manager is already reusable (was not created usin yield funcion

    class ReusableCtx:
        def __enter__(self):
            self.cm = context_manager._recreate_cm()
            return self.cm.__enter__()

        def __exit__(self, *args):
            self.cm.__exit__(*args)

    return ReusableCtx()


class DecoratingDescriptor(metaclass=ABCMeta):
    """
    Base class for descriptors that decorate a function

    :param func: The function to be decorated.
    :param bool cached: If ``True``, the decoration will only be done once per instance.

    Use this as a base class for other descriptors. When used on class objects,
    this will return itself. When used on instances, it this will call ``_decorate``
    on the method created by binding ``func``.
    """

    def __init__(self, *, func, cached: bool):
        self._func = func
        self._cached = cached
        self.__property_name = '__property_%s' % id(self)
        update_wrapper(self, func, updated=())

    @abstractmethod
    def _decorate(self, method, instance, owner):
        """
        Override to perform the actual decoration.

        :param method: The method from binding ``func``.
        :params instance: The binding instance (same as in ``__get__``)
        :params owner: The owner class (same as in ``__get__``)
        """
        pass

    def __get__(self, instance, owner):
        method = self._func.__get__(instance, owner)
        if instance is None:
            return method
        else:
            if self._cached:
                try:
                    return instance.__dict__[self.__property_name]
                except KeyError:
                    bound = self._decorate(method, instance, owner)
                    instance.__dict__[self.__property_name] = bound
                    return bound
            else:
                return self._decorate(method, instance, owner)


class LazyDecoratorDescriptor(DecoratingDescriptor):
    def __init__(self, decorator_factory, func, cached):
        super().__init__(func=func, cached=cached)
        self.decorator_factory = decorator_factory

    def _decorate(self, method, instance, owner):
        decorator = self.decorator_factory(instance)
        return decorator(method)


def lazy_decorator(decorator_factory, cached=False):
    """
    Create and apply a decorator only after the method is instantiated::

    :param decorator_factory: A function that will be called with the ``self`` argument.
                              Should return a decorator for the method.
                              If ``string``, use an attribute of ``self`` with that name
                              as the decorator.
    :param bool cached: If ``True``, the decoration will only be done once per instance.

        class UsageWithLambda:
            @lazy_decorator(lambda self: some_decorator_that_needs_the_object(self))
            def foo(self):
                # ...

        class UsageWithAttribute:
            def decorator_method(self, func):
                # ...

            @lazy_decorator('decorator_method')
            def foo(self):
                # ...

        class UsageCached:
            # Without ``cached=True``, this will create a new ``timecache`` on every invocation.
            @lazy_decorator(lambda self: timecache(expiration=1, get_ts_func=lambda: self.ts), cached=True)
            def foo(self):
                # ...
    """

    if callable(decorator_factory):
        pass
    elif isinstance(decorator_factory, str):
        decorator_factory = attrgetter(decorator_factory)
    else:
        raise TypeError('decorator_factory must be callable or string, not %s' % type(decorator_factory))

    def wrapper(func):
        return LazyDecoratorDescriptor(decorator_factory, func, cached)
    return wrapper


def mirror_defaults(mirrored):
    """
    Copy the default values of arguments from another function.

    Set an argument's default to ``AUTO`` to copy the default value from the
    mirrored function.

    >>> from easypy.decorations import mirror_defaults

    >>> def foo(a=1, b=2, c=3):
    ...     print(a, b, c)

    >>> @mirror_defaults(foo)
    ... def bar(a=AUTO, b=4, c=AUTO):
    ...     foo(a, b, c)

    >>> bar()
    1 4 3
    """
    defaults = {
        p.name: p.default
        for p in inspect.signature(mirrored).parameters.values()
        if p.default is not inspect._empty}

    def new_params_generator(params, defaults_to_override):
        for param in params:
            if param.default is AUTO:
                try:
                    default_value = defaults[param.name]
                except KeyError:
                    raise TypeError('%s has no default value for %s' % (mirrored.__name__, param.name))
                defaults_to_override.add(param.name)
                yield param.replace(default=default_value)
            else:
                yield param

    def outer(func):
        orig_signature = inspect.signature(func)
        defaults_to_override = set()
        new_parameters = new_params_generator(orig_signature.parameters.values(), defaults_to_override)
        new_signature = orig_signature.replace(parameters=new_parameters)

        @wraps(func)
        def inner(*args, **kwargs):
            binding = new_signature.bind(*args, **kwargs)

            # NOTE: `apply_defaults` was added in Python 3.5, so we cannot use it
            for name in defaults_to_override - binding.arguments.keys():
                binding.arguments[name] = defaults[name]

            return func(*binding.args, **binding.kwargs)
        inner.signature = new_signature

        return inner
    return outer
