import inspect

from apps.cmdb.collection.collect_plugin.base import CollectBase
from apps.cmdb.collection.plugins.registry import CollectionPluginRegistry


def _bind_collection_callable(instance, value):
    if inspect.ismethod(value):
        return value

    if not callable(value) or not hasattr(value, "__get__"):
        return value

    # 映射表中的匿名函数不会作为类属性暴露，无法通过函数名在 MRO 中回查。
    # 其首参明确声明为 self 时仍应绑定到当前采集器，否则运行时只传 data
    # 会触发 “missing 1 required positional argument: data”。
    if inspect.isfunction(value):
        parameters = tuple(inspect.signature(value).parameters.values())
        if parameters and parameters[0].name == "self":
            return value.__get__(instance, instance.__class__)

    func_name = getattr(value, "__name__", "")
    if not func_name:
        return value

    for cls in instance.__class__.__mro__:
        descriptor = inspect.getattr_static(cls, func_name, None)
        if descriptor is None:
            continue
        if isinstance(descriptor, staticmethod):
            return value
        return value.__get__(instance, instance.__class__)

    return value


def bind_collection_mapping(instance, mapping):
    bound_mapping = {}
    for field, value in mapping.items():
        if isinstance(value, tuple):
            func, *rest = value
            func = _bind_collection_callable(instance, func)
            bound_mapping[field] = (func, *rest)
            continue

        if callable(value):
            bound_mapping[field] = _bind_collection_callable(instance, value)
            continue

        bound_mapping[field] = value

    return bound_mapping


class AutoRegisterCollectionPluginMixin:
    supported_task_type = None
    supported_model_id = None
    plugin_source = "community"
    priority = 10

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if cls is AutoRegisterCollectionPluginMixin:
            return

        task_type = getattr(cls, "supported_task_type", None)
        model_id = getattr(cls, "supported_model_id", None)
        if task_type and model_id:
            CollectionPluginRegistry.register(cls)


class BaseCollectionPlugin(AutoRegisterCollectionPluginMixin, CollectBase):
    pass
