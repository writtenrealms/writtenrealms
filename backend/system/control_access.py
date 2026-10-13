"""Staff-only configuration and invitation management for an installation."""
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404

from rest_framework import serializers, status
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from system.access import ACCESS_CACHE_SECONDS
from system.models import AccessAllowlistEntry, SiteControl, StaffAuditEvent


def record_staff_action(actor, action, target='', details=None):
    return StaffAuditEvent.objects.create(
        actor=actor, actor_email=actor.email, action=action,
        target=str(target), details=details or {})


class StrictBooleanField(serializers.BooleanField):
    def to_internal_value(self, data):
        if type(data) is not bool:
            self.fail('invalid', input=data)
        return data


class StrictWorldIDField(serializers.IntegerField):
    def to_internal_value(self, data):
        if type(data) is not int:
            self.fail('invalid')
        return super().to_internal_value(data)


class SettingsSerializer(serializers.ModelSerializer):
    maintenance_mode = StrictBooleanField(required=False)
    building_enabled = StrictBooleanField(required=False)
    main_world_id = StrictWorldIDField(required=False, allow_null=True, min_value=1)
    class Meta:
        model = SiteControl
        fields = ('access_mode', 'maintenance_mode', 'building_enabled', 'main_world_id')

    def validate(self, attrs):
        unknown = set(self.initial_data) - set(self.fields)
        if unknown:
            raise serializers.ValidationError({name: 'Unknown setting.' for name in unknown})
        return attrs

    def validate_main_world_id(self, value):
        # Fresh installations default to world 1 before a builder creates it.
        # Echoing that unchanged default must not block access policy edits.
        if self.instance and value == self.instance.main_world_id:
            return value
        if value is not None:
            from worlds.models import World
            if not World.objects.filter(pk=value, context__isnull=True, instance_of__isnull=True).exists():
                raise serializers.ValidationError('Choose an existing root world, or leave blank for the multi-world lobby.')
        return value


def settings_data(control):
    return {
        **SettingsSerializer(control).data,
        'access_propagation_seconds': ACCESS_CACHE_SECONDS,
    }


class StaffSettingsView(APIView):
    permission_classes = (IsAdminUser,)

    def get(self, request):
        control = SiteControl.objects.filter(name='prod').first() or SiteControl(name='prod')
        return Response(settings_data(control))

    def patch(self, request):
        with transaction.atomic():
            control, _ = SiteControl.objects.select_for_update().get_or_create(name='prod')
            serializer = SettingsSerializer(control, data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            changes = {key: {'before': getattr(control, key), 'after': value}
                       for key, value in serializer.validated_data.items()
                       if getattr(control, key) != value}
            if changes:
                serializer.save()
                record_staff_action(request.user, 'settings.updated', target='platform', details=changes)
        return Response(settings_data(control))


class AllowlistSerializer(serializers.ModelSerializer):
    created_by_email = serializers.CharField(source='created_by.email', read_only=True, default=None)
    email = serializers.EmailField(max_length=254)

    class Meta:
        model = AccessAllowlistEntry
        fields = ('id', 'email', 'note', 'created_ts', 'created_by_email')
        read_only_fields = ('id', 'created_ts', 'created_by_email')

    def validate(self, attrs):
        unknown = set(self.initial_data) - {'email', 'note'}
        if unknown:
            raise serializers.ValidationError({name: 'Unknown field.' for name in unknown})
        return attrs

    def validate_email(self, value):
        value = value.strip().casefold()
        matches = AccessAllowlistEntry.objects.filter(email=value)
        if self.instance:
            matches = matches.exclude(pk=self.instance.pk)
        if matches.exists():
            raise serializers.ValidationError('This email is already on the allowlist.')
        return value


class StaffAllowlistView(APIView):
    permission_classes = (IsAdminUser,)

    def get(self, request):
        query = request.query_params.get('q', '').strip()[:254]
        try:
            page = max(1, int(request.query_params.get('page', 1)))
        except (TypeError, ValueError):
            raise serializers.ValidationError({'page': 'Must be an integer.'})
        entries = AccessAllowlistEntry.objects.select_related('created_by')
        if query:
            entries = entries.filter(email__icontains=query)
        count = entries.count()
        start = (page - 1) * 25
        return Response({'count': count, 'page': page, 'page_size': 25,
                         'results': AllowlistSerializer(entries[start:start + 25], many=True).data})

    def post(self, request):
        serializer = AllowlistSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            with transaction.atomic():
                entry = serializer.save(created_by=request.user)
                record_staff_action(request.user, 'allowlist.added', target=entry.email,
                                    details={'id': entry.pk})
        except IntegrityError:
            raise serializers.ValidationError({'email': 'This email is already on the allowlist.'})
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class StaffAllowlistDetailView(APIView):
    permission_classes = (IsAdminUser,)

    def patch(self, request, pk):
        try:
            with transaction.atomic():
                entry = get_object_or_404(AccessAllowlistEntry.objects.select_for_update(), pk=pk)
                serializer = AllowlistSerializer(entry, data=request.data, partial=True)
                serializer.is_valid(raise_exception=True)
                before = {'email': entry.email, 'note': entry.note}
                serializer.save()
                record_staff_action(request.user, 'allowlist.updated', target=entry.email,
                                    details={'id': entry.pk, 'before': before,
                                             'after': {'email': entry.email, 'note': entry.note}})
        except IntegrityError:
            raise serializers.ValidationError({'email': 'This email is already on the allowlist.'})
        return Response(serializer.data)

    def delete(self, request, pk):
        with transaction.atomic():
            entry = get_object_or_404(AccessAllowlistEntry.objects.select_for_update(), pk=pk)
            record_staff_action(request.user, 'allowlist.removed', target=entry.email,
                                details={'id': entry.pk})
            entry.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
