from rest_framework import serializers


class StrictBooleanField(serializers.BooleanField):
    def to_internal_value(self, data):
        if type(data) is not bool:
            raise serializers.ValidationError("必须是 JSON 布尔值")
        return data


class CustomReportingCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=128)
    team = serializers.ListField(child=serializers.IntegerField(), allow_empty=False)
    config = serializers.DictField()
    quick_model = serializers.DictField(required=False)
    is_enabled = serializers.BooleanField(default=True)


class CustomReportingUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=128, required=False)
    team = serializers.ListField(child=serializers.IntegerField(), required=False)
    config = serializers.DictField(required=False)
    quick_model = serializers.DictField(required=False)
    is_enabled = serializers.BooleanField(required=False)


class CustomReportingIngestSerializer(serializers.Serializer):
    instances = serializers.ListField(
        child=serializers.DictField(),
        required=False,
    )
    relations = serializers.ListField(
        child=serializers.DictField(),
        required=False,
    )
    snapshot_authoritative = StrictBooleanField(required=False)
