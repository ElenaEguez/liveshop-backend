from django.shortcuts import get_object_or_404
from django.db import transaction
from django.utils import timezone

from rest_framework import generics, viewsets, status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import Sucursal, Almacen, Caja, TurnoCaja, TicketConfig, Comprobante
from .serializers import (
    SucursalSerializer, AlmacenSerializer,
    CajaSerializer, TurnoCajaSerializer,
    TicketConfigSerializer, ComprobanteSerializer,
)
from .permissions import IsVendorOrTeamMember, get_vendor_for_user


def _get_vendor_or_403(user):
    vendor = get_vendor_for_user(user)
    if not vendor:
        raise PermissionDenied("Sin perfil de vendedor asociado.")
    return vendor


# ─── Sucursales ───────────────────────────────────────────────────────────────

class SucursalViewSet(viewsets.ModelViewSet):
    """
    CRUD de sucursales del vendor autenticado.
    GET/POST    /api/v1/branches/sucursales/
    PATCH/DELETE /api/v1/branches/sucursales/{pk}/
    GET         /api/v1/branches/sucursales/{pk}/almacenes/
    GET/POST    /api/v1/branches/sucursales/{pk}/cajas/
    """
    serializer_class = SucursalSerializer
    permission_classes = [IsAuthenticated, IsVendorOrTeamMember]
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def _get_vendor(self):
        return _get_vendor_or_403(self.request.user)

    def get_queryset(self):
        return Sucursal.objects.filter(
            vendor=self._get_vendor()
        ).prefetch_related('almacenes', 'cajas')

    def perform_create(self, serializer):
        serializer.save(vendor=self._get_vendor())

    def destroy(self, request, *args, **kwargs):
        sucursal = self.get_object()
        # Circular import safe: payments app imports from vendors, not the other way
        from payments.models import VentaPOS
        if VentaPOS.objects.filter(sucursal=sucursal).exists():
            return Response(
                {'error': 'No se puede eliminar: la sucursal tiene ventas registradas.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=['get'])
    def almacenes(self, request, pk=None):
        sucursal = self.get_object()
        return Response(AlmacenSerializer(sucursal.almacenes.all(), many=True).data)

    @action(detail=True, methods=['get', 'post'])
    def cajas(self, request, pk=None):
        sucursal = self.get_object()
        if request.method == 'GET':
            return Response(CajaSerializer(sucursal.cajas.all(), many=True).data)
        ser = CajaSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        ser.save(sucursal=sucursal)
        return Response(ser.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['delete'], url_path='cajas/(?P<caja_pk>[^/.]+)')
    def delete_caja(self, request, pk=None, caja_pk=None):
        sucursal = self.get_object()
        caja = get_object_or_404(Caja, pk=caja_pk, sucursal=sucursal)
        from payments.models import VentaPOS
        if VentaPOS.objects.filter(caja=caja).exists():
            return Response(
                {'error': 'No se puede eliminar: la caja tiene ventas registradas.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if TurnoCaja.objects.filter(caja=caja).exists():
            return Response(
                {'error': 'No se puede eliminar: la caja tiene turnos registrados.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        caja.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ─── Almacenes ────────────────────────────────────────────────────────────────

class AlmacenViewSet(viewsets.ModelViewSet):
    """
    CRUD de almacenes del vendor autenticado.
    GET/POST    /api/v1/branches/almacenes/
    PATCH/DELETE /api/v1/branches/almacenes/{pk}/
    """
    serializer_class = AlmacenSerializer
    permission_classes = [IsAuthenticated, IsVendorOrTeamMember]
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def _get_vendor(self):
        return _get_vendor_or_403(self.request.user)

    def get_queryset(self):
        return Almacen.objects.filter(sucursal__vendor=self._get_vendor())

    def perform_create(self, serializer):
        vendor = self._get_vendor()
        sucursal_id = self.request.data.get('sucursal')
        sucursal = get_object_or_404(Sucursal, id=sucursal_id, vendor=vendor)
        serializer.save(sucursal=sucursal)

    def destroy(self, request, *args, **kwargs):
        almacen = self.get_object()
        from products.models import Inventory
        if Inventory.objects.filter(almacen=almacen, is_active=True).exists():
            return Response(
                {'error': 'No se puede eliminar: el almacén tiene inventarios activos asignados.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return super().destroy(request, *args, **kwargs)


# ─── TicketConfig ─────────────────────────────────────────────────────────────

class TicketConfigView(generics.RetrieveUpdateAPIView):
    """
    GET  /api/v1/branches/ticket-config/  → obtiene o crea la config del vendor
    PUT/PATCH /api/v1/branches/ticket-config/  → actualiza
    """
    serializer_class = TicketConfigSerializer
    permission_classes = [IsAuthenticated, IsVendorOrTeamMember]

    def get_object(self):
        vendor = _get_vendor_or_403(self.request.user)
        config, _ = TicketConfig.objects.get_or_create(vendor=vendor)
        return config


# ─── Comprobantes ─────────────────────────────────────────────────────────────

class ComprobanteViewSet(viewsets.ModelViewSet):
    """
    GET/POST/PATCH /api/v1/branches/comprobantes/
    Crea los 6 tipos si no existen al primer GET.
    """
    serializer_class = ComprobanteSerializer
    permission_classes = [IsAuthenticated, IsVendorOrTeamMember]
    http_method_names = ['get', 'post', 'patch', 'head', 'options']

    TIPOS_DEFAULT = [
        'factura', 'boleta', 'nota_credito',
        'nota_debito', 'ticket_venta', 'ticket_compra',
    ]

    def _get_vendor(self):
        return _get_vendor_or_403(self.request.user)

    def get_queryset(self):
        vendor = self._get_vendor()
        qs = Comprobante.objects.filter(vendor=vendor)
        # Auto-create missing tipos
        existing = set(qs.values_list('tipo', flat=True))
        missing = [t for t in self.TIPOS_DEFAULT if t not in existing]
        if missing:
            Comprobante.objects.bulk_create([
                Comprobante(vendor=vendor, tipo=t) for t in missing
            ])
            qs = Comprobante.objects.filter(vendor=vendor)
        return qs.order_by('tipo')

    def perform_create(self, serializer):
        serializer.save(vendor=self._get_vendor())
