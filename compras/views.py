from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from decimal import Decimal, InvalidOperation

from django.db import transaction

from products.models import Product, ProductVariant

from compras.models import (
    Proveedor,
    OrdenCompra,
    OrdenCompraItem,
    OrdenCompraItemDistribucion,
    DevolucionCompra,
)
from compras.serializers import (
    ProveedorSerializer,
    OrdenCompraSerializer,
    DevolucionCompraSerializer,
    DevolucionCompraCreateSerializer,
)
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


def _optional_int(val):
    if val is None or val == '':
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def _prepare_orden_item_row(raw):
    """
    Convierte FKs a *_id, elimina campos de solo lectura del API
    y fuerza tipos numéricos (JSON/Form pueden enviar strings).
    Devuelve (dict para OrdenCompraItem.objects.create, lista distribución o None).
    """
    item = dict(raw)
    dist_raw = item.pop('distribuciones', None)
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
    if 'precio_venta_es_manual' in item:
        v = item['precio_venta_es_manual']
        item['precio_venta_es_manual'] = v in (True, 'true', 'True', '1', 1, 'on', 'yes')
    item.pop('subtotal', None)
    item.pop('costo_unitario_total', None)

    dist_list = None
    if dist_raw is not None:
        if not isinstance(dist_raw, list):
            dist_raw = []
        dist_list = []
        for d in dist_raw:
            if not isinstance(d, dict):
                continue
            vid = d.get('variante')
            if vid is None:
                vid = d.get('variante_id')
            if vid is None:
                continue
            try:
                vid_int = int(vid)
            except (TypeError, ValueError):
                continue
            cq = _int_from_request(d.get('cantidad'), 0)
            if cq <= 0:
                continue
            dist_list.append({'variante_id': vid_int, 'cantidad': cq})
        if not dist_list:
            dist_list = None
    return item, dist_list


def _items_missing_almacen(items_data, orden_almacen_id=None):
    for raw in items_data:
        row, _ = _prepare_orden_item_row(raw)
        if not (row.get('almacen_id') or orden_almacen_id):
            return True
    return False


def _validate_compra_items(vendor, prepared_rows):
    """prepared_rows: list of (item_row dict, dist_list | None)."""
    for item_row, dist_list in prepared_rows:
        pid = item_row.get('producto_id')
        if not pid:
            raise ValueError('Cada ítem debe incluir un producto.')
        try:
            producto = Product.objects.get(pk=pid, vendor=vendor)
        except Product.DoesNotExist as exc:
            raise ValueError('Producto no encontrado o no pertenece a su tienda.') from exc

        var_qs = ProductVariant.objects.filter(product=producto, is_active=True)
        has_variants = var_qs.exists()
        cantidad = item_row.get('cantidad', 1)

        if has_variants:
            if not dist_list:
                raise ValueError(
                    f'"{producto.name}": indique la distribución por variantes '
                    f'(cantidad por talla/color). La suma debe ser {cantidad} uds.'
                )
            total_dist = sum(d['cantidad'] for d in dist_list)
            if total_dist != cantidad:
                raise ValueError(
                    f'"{producto.name}": la suma por variante ({total_dist}) debe igualar '
                    f'la cantidad total del ítem ({cantidad}).'
                )
            seen_v = set()
            for d in dist_list:
                vid = d['variante_id']
                if vid in seen_v:
                    raise ValueError(f'"{producto.name}": variante repetida en la distribución.')
                seen_v.add(vid)
                try:
                    ProductVariant.objects.get(pk=vid, product=producto, is_active=True)
                except ProductVariant.DoesNotExist as exc:
                    raise ValueError(
                        f'"{producto.name}": variante no válida para este producto.'
                    ) from exc
            item_row['variante_id'] = None
        else:
            if dist_list:
                raise ValueError(
                    f'"{producto.name}" no tiene variantes; no envíe "distribuciones".'
                )


def _persist_items(orden, items_data, orden_almacen_id):
    """Crea ítems y distribuciones. Almacén de cabecera (orden o payload) en líneas sin almacén."""
    cabecera_alm = orden_almacen_id or orden.almacen_id
    for item_data in items_data:
        item_row, dist_list = _prepare_orden_item_row(item_data)
        if not item_row.get('almacen_id') and cabecera_alm:
            item_row['almacen_id'] = cabecera_alm
        oi = OrdenCompraItem.objects.create(orden=orden, **item_row)
        if dist_list:
            for d in dist_list:
                OrdenCompraItemDistribucion.objects.create(
                    item=oi,
                    variante_id=d['variante_id'],
                    cantidad=d['cantidad'],
                )


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
        qs = OrdenCompra.objects.filter(
            vendor=vendor
        ).prefetch_related(
            'items__producto',
            'items__variante',
            'items__distribuciones__variante',
        )
        estado = self.request.query_params.get('estado')
        if estado:
            qs = qs.filter(estado=estado)
        return qs

    def create(self, request, *args, **kwargs):
        vendor = _get_vendor(request)
        if not vendor:
            return Response({'error': 'Sin vendor asignado'}, status=400)

        items_data = request.data.get('items', [])
        estado = request.data.get('estado')
        orden_almacen_id = _optional_int(request.data.get('almacen'))

        prepared = [_prepare_orden_item_row(x) for x in items_data]
        if estado in ('pendiente', 'recibida') and _items_missing_almacen(
            items_data, orden_almacen_id
        ):
            return Response(
                {'error': 'Indique almacén destino en la cabecera o en cada línea.'},
                status=400
            )
        try:
            _validate_compra_items(vendor, prepared)
        except ValueError as exc:
            return Response({'error': str(exc)}, status=400)

        with transaction.atomic():
            serializer = self.get_serializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            orden = serializer.save(vendor=vendor, created_by=request.user)
            _persist_items(orden, items_data, orden_almacen_id)
            orden.recalcular_totales()

        return Response(
            OrdenCompraSerializer(orden).data,
            status=status.HTTP_201_CREATED
        )

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()

        vendor = _get_vendor(request)
        if not vendor:
            return Response({'error': 'Sin vendor asignado'}, status=400)

        if instance.estado == 'recibida':
            return Response(
                {'error': 'No se puede editar una orden recibida'},
                status=400
            )

        items_data = request.data.get('items', [])
        estado = request.data.get('estado', instance.estado)
        orden_almacen_id = _optional_int(request.data.get('almacen'))

        prepared = [_prepare_orden_item_row(x) for x in items_data] if items_data else []
        if items_data and estado in ('pendiente', 'recibida') and _items_missing_almacen(
            items_data, orden_almacen_id
        ):
            return Response(
                {'error': 'Indique almacén destino en la cabecera o en cada línea.'},
                status=400
            )
        if items_data:
            try:
                _validate_compra_items(vendor, prepared)
            except ValueError as exc:
                return Response({'error': str(exc)}, status=400)

        with transaction.atomic():
            serializer = self.get_serializer(
                instance, data=request.data, partial=partial
            )
            serializer.is_valid(raise_exception=True)
            orden = serializer.save()

            if items_data:
                orden.items.all().delete()
                _persist_items(orden, items_data, orden_almacen_id)
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
        for it in orden.items.all():
            if not it.almacen_id and not orden.almacen_id:
                return Response(
                    {
                        'error': (
                            'Indique almacén destino en la cabecera de la orden '
                            'o en cada línea antes de confirmar.'
                        )
                    },
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


class DevolucionCompraViewSet(viewsets.ModelViewSet):
    """
    POST /compras/devoluciones-proveedor/ — registra salida de inventario y variante.
    GET list / detail — historial de devoluciones a proveedor.
    """
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'post', 'head', 'options']

    def get_queryset(self):
        vendor = _get_vendor(self.request)
        if not vendor:
            return DevolucionCompra.objects.none()
        return DevolucionCompra.objects.filter(vendor=vendor).prefetch_related(
            'items__producto', 'items__variante', 'items__almacen',
        )

    def get_serializer_class(self):
        if self.action == 'create':
            return DevolucionCompraCreateSerializer
        return DevolucionCompraSerializer

    def create(self, request, *args, **kwargs):
        vendor = _get_vendor(request)
        if not vendor:
            return Response({'error': 'Sin vendor asignado'}, status=400)
        ser = self.get_serializer(
            data=request.data,
            context={'vendor': vendor, 'request': request},
        )
        ser.is_valid(raise_exception=True)
        dev = ser.save()
        out = DevolucionCompraSerializer(
            DevolucionCompra.objects.prefetch_related(
                'items__producto', 'items__variante', 'items__almacen',
            ).get(pk=dev.pk)
        )
        return Response(out.data, status=status.HTTP_201_CREATED)

