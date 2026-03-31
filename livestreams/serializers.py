from rest_framework import serializers
from .models import LiveSession

class LiveSessionSerializer(serializers.ModelSerializer):
    is_live = serializers.ReadOnlyField()

    class Meta:
        model = LiveSession
        fields = '__all__'
        read_only_fields = ['vendor', 'started_at', 'ended_at', 'created_at']