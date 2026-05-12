from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from decimal import Decimal, InvalidOperation

from django.db import transaction

from compras.models import Proveedor, OrdenCompra, OrdenCompraItem
from compras.serializers import ProveedorSerializer, OrdenCompraSerializer
from vendors.permissions import get_vendor_for_user


def _get_vendor(request):
    """Helper: obtiene el vendor del usuario logueado."""
    return get_vendor_for_user(request.user)


def _decimal_from_request(val, default=None):
    if default is None:
        default = Decimal('0')
    if val is None or val == '':
        return default
    if isinstance(val, Decimal):
        return val
    try:
        return Decimal(str(val))
    except (InvalidOperation, TypeError, ValueError):
        return default


def _int_from_request(val, default=1):
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def _prepare_orden_item_row(raw):
    """
    Convierte FKs a *_id, elimina campos de solo lectura del API
    y fuerza tipos numéricos (JSON/Form pueden enviar strings).
    """
    item = dict(raw)
    for key in ('id', 'producto_nombre', 'variante_detalle'):
        item.pop(key, None)
    if 'producto' in item:
        item['producto_id'] = item.pop('producto')
    if 'variante' in item:
        item['variante_id'] = item.pop('variante')
    if 'almacen' in item:
        item['almacen_id'] = item.pop('almacen')
    for fld in (
        'costo_mercaderia', 'flete_unitario', 'costo_unitario_total',
        'porcentaje_ganancia', 'precio_venta_sugerido', 'precio_unitario',
        'subtotal',
    ):
        if fld in item:
            item[fld] = _decimal_from_request(item[fld])
    if 'cantidad' in item:
        item['cantidad'] = _int_from_request(item['cantidad'], 1)
    return item


def _items_missing_almacen(items_data):
    return any(not item.get('almacen') for item in items_data)


class ProveedorViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = ProveedorSerializer

    def get_queryset(self):
        vendor = _get_vendor(self.request)
        if not vendor:
            return Proveedor.objects.none()
        return Proveedor.objects.filter(vendor=vendor)

    def perform_create(self, serializer):
        vendor = _get_vendor(self.request)
        serializer.save(vendor=vendor)


class OrdenCompraViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = OrdenCompraSerializer

    def get_queryset(self):
        vendor = _get_vendor(self.request)
        if not vendor:
            return OrdenCompra.objects.none()
        return OrdenCompra.objects.filter(
            vendor=vendor
        ).prefetch_related('items__producto', 'items__variante')

    def create(self, request, *args, **kwargs):
        vendor = _get_vendor(request)
        if not vendor:
            return Response({'error': 'Sin vendor asignado'}, status=400)

        items_data = request.data.get('items', [])
        estado = request.data.get('estado')
        if estado in ('pendiente', 'recibida') and _items_missing_almacen(items_data):
            return Response(
                {'error': 'Cada producto de la compra debe tener almacén destino.'},
                status=400
            )

        with transaction.atomic():
            serializer = self.get_serializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            orden = serializer.save(vendor=vendor, created_by=request.user)
            for item_data in items_data:
                item = _prepare_orden_item_row(item_data)
                OrdenCompraItem.objects.create(orden=orden, **item)
            orden.recalcular_totales()

        return Response(
            OrdenCompraSerializer(orden).data,
            status=status.HTTP_201_CREATED
        )

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()

        if instance.estado == 'recibida':
            return Response(
                {'error': 'No se puede editar una orden recibida'},
                status=400
            )

        items_data = request.data.get('items', [])
        estado = request.data.get('estado', instance.estado)
        if estado in ('pendiente', 'recibida') and _items_missing_almacen(items_data):
            return Response(
                {'error': 'Cada producto de la compra debe tener almacén destino.'},
                status=400
            )

        with transaction.atomic():
            serializer = self.get_serializer(
                instance, data=request.data, partial=partial
            )
            serializer.is_valid(raise_exception=True)
            orden = serializer.save()

            if items_data:
                orden.items.all().delete()
                for item_data in items_data:
                    item = _prepare_orden_item_row(item_data)
                    OrdenCompraItem.objects.create(orden=orden, **item)
                orden.recalcular_totales()

        return Response(OrdenCompraSerializer(orden).data)

    @action(methods=['post'], detail=True, url_path='confirmar')
    def confirmar(self, request, pk=None):
        orden = self.get_object()
        if orden.estado != 'pendiente':
            return Response(
                {'error': 'Solo se pueden confirmar órdenes pendientes'},
                status=400
            )
        if orden.items.filter(almacen__isnull=True).exists():
            return Response(
                {'error': 'Cada producto de la compra debe tener almacén destino.'},
                status=400
            )
        orden.estado = 'recibida'
        orden.save()  # dispara la señal pre_save
        return Response(OrdenCompraSerializer(orden).data)

    @action(methods=['post'], detail=True, url_path='cancelar')
    def cancelar(self, request, pk=None):
        orden = self.get_object()
        if orden.estado == 'recibida':
            return Response(
                {'error': 'No se puede cancelar una orden ya recibida'},
                status=400
            )
        orden.estado = 'cancelada'
        orden.save()
        return Response(OrdenCompraSerializer(orden).data)

