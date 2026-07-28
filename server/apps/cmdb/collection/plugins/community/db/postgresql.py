from apps.cmdb.collection.plugins.community.db.base import BaseDBCollectionPlugin


class PostgreSQLCollectionPlugin(BaseDBCollectionPlugin):
    supported_model_id = "postgresql"
    metric_names = ("postgresql_info_gauge",)
    field_mapping = {
        "inst_name": lambda x: f"{x['ip_addr']}-pg-{x['port']}",
        "ip_addr": "ip_addr",
        "port": "port",
        "version": "version",
        "config": "conf_path",
        "data_path": "data_path",
        "max_connect": "max_conn",
        "shared_buffer": "cache_memory_mb",
        "log_directory": "log_path",
    }
