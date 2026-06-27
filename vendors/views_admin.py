from rest_framework.views import APIView
from rest_framework.generics import ListCreateAPIView, RetrieveUpdateAPIView
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework import serializers

from vendors.models import Vendor
from vendors.serializers import VendorSerializer


class IsSuperAdmin(BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user and
            request.user.is_authenticated and
            request.user.is_staff
        )


class VendorAdminSerializer(VendorSerializer):
    """Extiende VendorSerializer con campos de suscripción para superadmin."""

    usuarios_activos = serializers.SerializerMethodField()

    def get_usuarios_activos(self, obj):
        return obj.team_members.filter(is_active=True).count()

    class Meta(VendorSerializer.Meta):
        fields = tuple(list(VendorSerializer.Meta.fields) + [
            'plan', 'max_usuarios', 'estado_suscripcion',
            'fecha_vencimiento', 'notas_admin', 'usuarios_activos',
            'modo_simple',
        ])


class VendorAdminListView(ListCreateAPIView):
    permission_classes = [IsAuthenticated, IsSuperAdmin]
    serializer_class = VendorAdminSerializer

    def get_queryset(self):
        return Vendor.objects.all().order_by('-created_at')


class VendorAdminDetailView(RetrieveUpdateAPIView):
    permission_classes = [IsAuthenticated, IsSuperAdmin]
    serializer_class = VendorAdminSerializer
    queryset = Vendor.objects.all()


class VendorToggleEstadoView(APIView):
    permission_classes = [IsAuthenticated, IsSuperAdmin]

    ESTADOS_VALIDOS = ['activo', 'vencido', 'suspendido', 'prueba']

    def post(self, request, pk):
        try:
            vendor = Vendor.objects.get(pk=pk)
        except Vendor.DoesNotExist:
            return Response({'error': 'Vendor no encontrado'}, status=404)

        nuevo_estado = request.data.get('estado')
        if nuevo_estado not in self.ESTADOS_VALIDOS:
            return Response(
                {'error': f'Estado inválido. Opciones: {self.ESTADOS_VALIDOS}'},
                status=400
            )

        vendor.estado_suscripcion = nuevo_estado
        vendor.save(update_fields=['estado_suscripcion'])
        return Response({
            'ok': True,
            'vendor_id': vendor.id,
            'estado_nuevo': nuevo_estado,
        })
