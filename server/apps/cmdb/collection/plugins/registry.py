from apps.core.logger import cmdb_logger as logger

_NON_PRODUCTION_MODEL_IDS = {"h3c_cas", "tuxedo", "zstack"}


def emitted_model_ids(plugin_cls: type) -> tuple[str, ...]:
    final_model_ids = getattr(plugin_cls, "final_model_ids", ()) or ()
    if final_model_ids:
        return tuple(sorted({str(model_id) for model_id in final_model_ids}))

    names = set()
    model_id_aliases = getattr(plugin_cls, "MODEL_ID_ALIASES", {}) or {}
    for attr in ("field_mapping", "field_mappings", "related_field_mappings"):
        value = getattr(plugin_cls, attr, None)
        if isinstance(value, dict):
            names.update(str(key) for key, mapping in value.items() if isinstance(mapping, dict))
    for metric in getattr(plugin_cls, "metric_names", ()) or ():
        model_id = metric.removesuffix("_info_gauge")
        if model_id:
            names.add(model_id_aliases.get(model_id, model_id))
    if not names:
        names.add(plugin_cls.supported_model_id)
    return tuple(sorted(names))


class CollectionPluginRegistry:
    _registry = {}
    _initialized = False

    @classmethod
    def ensure_initialized(cls):
        if cls._initialized:
            return
        from apps.cmdb.collection.plugins.loader import CollectionPluginLoader

        cls._initialized = CollectionPluginLoader.load_plugins()

    @classmethod
    def register(cls, plugin_cls):
        task_type = getattr(plugin_cls, "supported_task_type", None)
        model_id = getattr(plugin_cls, "supported_model_id", None)
        if not task_type or not model_id:
            return

        task_plugins = cls._registry.setdefault(task_type, {})
        current_cls = task_plugins.get(model_id)
        if current_cls is None:
            task_plugins[model_id] = plugin_cls
            return

        current_priority = getattr(current_cls, "priority", 0)
        new_priority = getattr(plugin_cls, "priority", 0)

        if new_priority > current_priority:
            logger.info(
                "Collection plugin overridden: task_type=%s, model_id=%s, old=%s, new=%s",
                task_type,
                model_id,
                current_cls.__name__,
                plugin_cls.__name__,
            )
            task_plugins[model_id] = plugin_cls
            return

        if new_priority == current_priority:
            logger.error(
                "Collection plugin conflict: task_type=%s, model_id=%s, current=%s, new=%s",
                task_type,
                model_id,
                current_cls.__name__,
                plugin_cls.__name__,
            )

    @classmethod
    def get_plugin(cls, task_type: str, model_id: str):
        cls.ensure_initialized()
        plugin_cls = cls._registry.get(task_type, {}).get(model_id)
        if plugin_cls is None:
            raise ValueError(f"Unsupported collection plugin: task_type={task_type}, model_id={model_id}")
        return plugin_cls

    @classmethod
    def get_registry_snapshot(cls):
        cls.ensure_initialized()
        snapshot = []
        for task_type in sorted(cls._registry):
            for model_id in sorted(cls._registry[task_type]):
                plugin_cls = cls._registry[task_type][model_id]
                snapshot.append(
                    {
                        "task_type": task_type,
                        "model_id": model_id,
                        "class_name": plugin_cls.__name__,
                        "module": plugin_cls.__module__,
                        "plugin_source": getattr(plugin_cls, "plugin_source", "unknown"),
                        "priority": getattr(plugin_cls, "priority", 0),
                        "emitted_model_ids": emitted_model_ids(plugin_cls),
                        "is_production": (".archived." not in plugin_cls.__module__ and model_id not in _NON_PRODUCTION_MODEL_IDS),
                    }
                )
        return snapshot
