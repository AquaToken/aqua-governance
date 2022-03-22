import json

from rest_framework import serializers

from django_quill.quill import Quill


class QuillField(serializers.Field):
    def get_attribute(self, instance):
        return instance.text.html

    def to_representation(self, value):
        return value

    def to_internal_value(self, data):
        obj = {'delta': '', 'html': data}
        return Quill(json.dumps(obj))
