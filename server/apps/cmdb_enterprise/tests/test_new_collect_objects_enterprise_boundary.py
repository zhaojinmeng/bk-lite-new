from apps.cmdb.collection.plugins import get_collection_plugin
from apps.cmdb.constants.constants import COLLECT_OBJ_TREE
from apps.cmdb.node_configs.base import BaseNodeParams
from apps.cmdb.services.collect_object_tree import get_collect_obj_tree


NEW_COLLECT_OBJECTS = [
    ("nacos", "middleware", "protocol"),
    ("ibmmq", "middleware", "job"),
    ("oceanbase", "protocol", "protocol"),
    ("highgo", "protocol", "protocol"),
    ("server_bmc", "protocol", "protocol"),
    ("tonglinkq", "middleware", "job"),
    ("tonggtp", "middleware", "job"),
    ("ihs", "middleware", "job"),
    ("cics", "middleware", "job"),
    ("ibm_storwize", "cloud", "protocol"),
    ("ibm_ds", "cloud", "protocol"),
    ("emc_symmetrix", "cloud", "protocol"),
    ("hds_vsp", "cloud", "protocol"),
    ("macrosan", "cloud", "protocol"),
    ("pure_array", "cloud", "protocol"),
    ("netapp_cluster", "cloud", "protocol"),
    ("oraclezfs", "cloud", "protocol"),
    ("infinidat", "cloud", "protocol"),
    ("tape_library", "snmp", "protocol"),
    ("brocade_fc", "snmp", "job"),
    ("cisco_fc", "snmp", "job"),
    ("f5", "snmp", "protocol"),
    ("informix", "db", "job"),
    ("sybase", "db", "job"),
    ("couchbase", "protocol", "protocol"),
    ("mycat", "db", "job"),
    ("sap_hana", "protocol", "protocol"),
    ("iris", "protocol", "protocol"),
    ("aix", "host", "job"),
    ("hpux", "host", "job"),
    ("hmc", "host", "job"),
    ("hdfs", "middleware", "job"),
    ("yarn", "middleware", "job"),
    ("storm", "middleware", "job"),
    ("ambari", "middleware", "protocol"),
    ("redis_sentinel", "db", "job"),
    ("bes", "middleware", "job"),
    ("apusic", "middleware", "job"),
    ("inforsuite_as", "middleware", "job"),
    ("gbase8s", "db", "job"),
    ("oscar", "db", "job"),
    ("security_device", "snmp", "protocol"),
    ("domestic_linux", "host", "job"),
    ("tongrds", "protocol", "protocol"),
    ("tdsql", "protocol", "protocol"),
    ("zstack", "cloud", "protocol"),
    ("h3c_cas", "cloud", "protocol"),
    ("xsky", "cloud", "protocol"),
]

HOST_OBJECTS_MERGED_TO_HOST = {"aix", "hpux", "domestic_linux"}


def _tree_model_ids(tree):
    return {child.get("model_id") for group in tree for child in group.get("children", [])}


def test_new_collect_objects_are_not_in_community_base_tree():
    base_model_ids = _tree_model_ids(COLLECT_OBJ_TREE)

    for model_id, _task_type, _driver_type in NEW_COLLECT_OBJECTS:
        assert model_id not in base_model_ids


def test_new_collect_objects_are_added_by_enterprise_extension():
    merged_model_ids = _tree_model_ids(get_collect_obj_tree())

    for model_id, _task_type, _driver_type in NEW_COLLECT_OBJECTS:
        if model_id in HOST_OBJECTS_MERGED_TO_HOST:
            assert model_id not in merged_model_ids
            assert "host" in merged_model_ids
            continue
        assert model_id in merged_model_ids


def test_new_collect_plugins_are_enterprise_owned():
    for model_id, task_type, _driver_type in NEW_COLLECT_OBJECTS:
        plugin_cls = get_collection_plugin(task_type, model_id)
        assert plugin_cls.__module__.startswith("apps.cmdb_enterprise.collect.")


def test_new_node_params_are_enterprise_owned():
    import apps.cmdb.node_configs  # noqa: F401

    for model_id, _task_type, driver_type in NEW_COLLECT_OBJECTS:
        cls = BaseNodeParams._registry[(model_id, driver_type)]
        assert cls.__module__.startswith("apps.cmdb_enterprise.collect.")
