from django.db.models import Q

from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination
from rest_framework.views import APIView
from decimal import Decimal, InvalidOperation

from django.db import transaction

from products.models import Product, ProductVariant, Inventory

from compras.models import (
    Proveedor,
    OrdenCompra,
    OrdenCompraItem,
    OrdenCompraItemDistribucion,
    DevolucionCompra,
)
from vendors.models import Almacen
from vendors.permissions import SuscripcionActivaPermission
from compras.serializers import (
    ProveedorSerializer,
    OrdenCompraSerializer,
    DevolucionCompraSerializer,
    DevolucionCompraCreateSerializer,
    _cantidad_ya_devuelta_en_orden,
    _variante_descripcion,
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


def _procesar_recepcion_inventario(orden):
    """
    Incrementa stock al recibir una orden.
    Idempotente por instancia en memoria (confirmar + señal pre_save).
    """
    if getattr(orden, '_inventario_recepcion_aplicado', False):
        return

    from products.stock_service import (
        StockError,
        apply_stock_delta,
        product_has_variants,
    )

    qs_items = orden.items.select_related(
        'producto', 'variante',
    ).prefetch_related('distribuciones__variante')
    for item in qs_items:
        almacen_destino = item.almacen or orden.almacen
        if not almacen_destino:
            raise ValueError(
                'Cada ítem de compra debe tener almacén destino antes de recibir la orden.'
            )

        distribuciones = list(item.distribuciones.all())
        if distribuciones:
            filas = [
                (d.cantidad, d.variante)
                for d in distribuciones
                if d.cantidad and d.variante_id
            ]
        elif item.variante_id:
            filas = [(item.cantidad, item.variante)]
        elif product_has_variants(item.producto_id):
            raise ValueError(
                f'"{item.producto.name}": indique distribución por variante '
                f'antes de recibir la compra.'
            )
        else:
            filas = [(item.cantidad, None)]

        for cant_u, variante in filas:
            if not cant_u:
                continue
            apply_stock_delta(
                product=item.producto,
                almacen=almacen_destino,
                delta=int(cant_u),
                variant=variante,
                usuario=orden.created_by,
                motivo='compra',
                documento_ref=f'OC-{orden.numero}',
                notas=f'Compra OC-{orden.numero}',
                costo_promedio=item.costo_unitario_total,
                update_purchase_cost=item.costo_unitario_total,
            )

        if item.precio_venta_sugerido and item.precio_venta_sugerido > 0:
            producto = item.producto
            producto.price = item.precio_venta_sugerido
            producto.save(update_fields=['price'])

    orden._inventario_recepcion_aplicado = True


def _inventory_lote_item_compra(item, orden):
    """
    Sin FK directa OrdenCompraItem → Inventory.
    Busca el lote PEPS: mismo producto + almacén (ítem o cabecera), stock > 0.
    """
    almacen = item.almacen or orden.almacen
    if not almacen:
        return None
    return (
        Inventory.objects.filter(
            product=item.producto,
            almacen=almacen,
            is_active=True,
            quantity__gt=0,
        )
        .order_by('created_at', 'id')
        .first()
    )


class ProveedorPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 50


class OrdenCompraPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 50


class ProveedorViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = ProveedorSerializer
    pagination_class = ProveedorPagination

    def get_queryset(self):
        vendor = _get_vendor(self.request)
        if not vendor:
            return Proveedor.objects.none()
        qs = Proveedor.objects.filter(vendor=vendor).order_by('-created_at')
        search = (self.request.query_params.get('search') or '').strip()
        if search:
            qs = qs.filter(nombre__icontains=search)
        return qs

    def perform_create(self, serializer):
        vendor = _get_vendor(self.request)
        serializer.save(vendor=vendor)

    def perform_destroy(self, instance):
        if instance.ordencompra_set.exists():
            raise ValidationError({
                'detail': (
                    'No se puede eliminar: el proveedor tiene órdenes de compra asociadas. '
                    'Puede desactivarlo para ocultarlo en nuevas compras sin afectar el historial.'
                ),
            })
        super().perform_destroy(instance)


class OrdenCompraViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = OrdenCompraSerializer
    pagination_class = OrdenCompraPagination

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
        proveedor_id = self.request.query_params.get('proveedor_id')
        if proveedor_id:
            try:
                qs = qs.filter(proveedor_id=int(proveedor_id))
            except (TypeError, ValueError):
                pass
        numero = self.request.query_params.get('numero', '').strip()
        if numero:
            qs = qs.filter(numero__icontains=numero)
        return qs.order_by('-fecha', '-created_at')

    def perform_destroy(self, instance):
        if instance.estado not in ('borrador', 'pendiente'):
            raise ValidationError(
                'Solo se pueden eliminar órdenes en borrador o pendiente.'
            )
        super().perform_destroy(instance)

    def create(self, request, *args, **kwargs):
        vendor = _get_vendor(request)
        if not vendor:
            return Response({'error': 'Sin vendor asignado'}, status=400)

        items_data = request.data.get('items', [])
        estado = request.data.get('estado')
        if estado == 'recibida':
            return Response(
                {
                    'error': (
                        'No se puede crear una orden ya recibida. '
                        'Use borrador o pendiente y confirme con el botón Recibir.'
                    ),
                },
                status=400,
            )
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
        vendor = _get_vendor(request)
        if not vendor:
            return Response({'error': 'Sin vendor asignado'}, status=400)

        with transaction.atomic():
            orden = OrdenCompra.objects.select_for_update().get(
                pk=pk,
                vendor=vendor,
            )
            if orden.estado != 'pendiente':
                return Response(
                    {'error': 'Solo se pueden confirmar órdenes pendientes'},
                    status=400,
                )
            for it in orden.items.all():
                if not it.almacen_id and not orden.almacen_id:
                    return Response(
                        {
                            'error': (
                                'Indique almacén destino en la cabecera de la orden '
                                'o en cada línea antes de confirmar.'
                            ),
                        },
                        status=400,
                    )
            try:
                from products.stock_service import StockError
                _procesar_recepcion_inventario(orden)
            except (ValueError, StockError) as exc:
                return Response({'error': str(exc)}, status=400)
            orden.estado = 'recibida'
            orden.save()  # dispara la señal pre_save

        orden.refresh_from_db()
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

    @action(methods=['patch'], detail=True, url_path='actualizar-precios')
    def actualizar_precios(self, request, pk=None):
        """
        Edita precio_venta_sugerido de ítems en orden recibida y actualiza
        Inventory.precio_venta del lote PEPS correspondiente.
        """
        orden = self.get_object()

        if orden.estado != 'recibida':
            return Response(
                {'error': 'Solo se puede editar precios de órdenes recibidas'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        items_data = request.data.get('items', [])
        if not isinstance(items_data, list) or not items_data:
            return Response(
                {'error': 'Debe enviar items: [{ id, precio_venta_sugerido }, ...]'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        actualizados = []
        errores = []

        with transaction.atomic():
            for item_data in items_data:
                if not isinstance(item_data, dict):
                    errores.append({
                        'item_id': None,
                        'error': 'Cada ítem debe ser un objeto',
                    })
                    continue

                item_id = item_data.get('id')
                try:
                    item = OrdenCompraItem.objects.select_related(
                        'producto',
                    ).get(id=item_id, orden=orden)
                except OrdenCompraItem.DoesNotExist:
                    errores.append({
                        'item_id': item_id,
                        'error': 'Ítem no pertenece a esta orden',
                    })
                    continue

                try:
                    nuevo_precio = Decimal(str(item_data['precio_venta_sugerido']))
                except (KeyError, ValueError, TypeError, InvalidOperation):
                    errores.append({
                        'item_id': item_id,
                        'error': 'Precio inválido',
                    })
                    continue

                if nuevo_precio <= 0:
                    errores.append({
                        'item_id': item_id,
                        'error': 'El precio debe ser mayor a 0',
                    })
                    continue

                precio_anterior = item.precio_venta_sugerido

                item.precio_venta_sugerido = nuevo_precio
                item.precio_venta_es_manual = True
                item.save(update_fields=[
                    'precio_venta_sugerido',
                    'precio_venta_es_manual',
                    'costo_unitario_total',
                    'subtotal',
                ])

                inv = _inventory_lote_item_compra(item, orden)
                if inv:
                    inv.precio_venta = nuevo_precio
                    inv.save(update_fields=['precio_venta', 'updated_at'])
                    actualizados.append({
                        'item_id': item.id,
                        'producto': item.producto.name,
                        'precio_anterior': float(precio_anterior),
                        'precio_nuevo': float(nuevo_precio),
                        'inventory_id': inv.id,
                        'stock_restante': inv.quantity,
                    })
                else:
                    errores.append({
                        'item_id': item.id,
                        'error': 'No se encontró inventory activo para este producto',
                    })

        return Response({
            'actualizados': actualizados,
            'errores': errores,
            'mensaje': f'{len(actualizados)} precios actualizados correctamente',
        })


class BuscarDevolucionView(APIView):
    """
    GET /compras/buscar-devolucion/
    Órdenes recibidas con líneas/variantes y flag puede_devolver.
    """
    permission_classes = [IsAuthenticated, SuscripcionActivaPermission]

    def get(self, request):
        vendor = _get_vendor(request)
        if not vendor:
            return Response([])

        proveedor_id = request.query_params.get('proveedor_id')
        producto_id = request.query_params.get('producto_id')
        orden_id = request.query_params.get('orden_id')
        q = (request.query_params.get('q') or '').strip()

        qs = OrdenCompra.objects.filter(
            vendor=vendor,
            estado='recibida',
        ).select_related(
            'proveedor', 'almacen',
        ).prefetch_related(
            'items__producto',
            'items__variante',
            'items__distribuciones__variante',
        )

        if proveedor_id:
            try:
                qs = qs.filter(proveedor_id=int(proveedor_id))
            except (TypeError, ValueError):
                pass

        if producto_id:
            try:
                qs = qs.filter(items__producto_id=int(producto_id)).distinct()
            except (TypeError, ValueError):
                pass

        if orden_id:
            try:
                qs = qs.filter(pk=int(orden_id))
            except (TypeError, ValueError):
                pass

        if q:
            qs = qs.filter(
                Q(proveedor__nombre__icontains=q)
                | Q(items__producto__name__icontains=q)
                | Q(numero__icontains=q),
            ).distinct()

        qs = qs.order_by('-fecha', '-created_at')[:50]

        payload = []
        for orden in qs:
            almacen = orden.almacen
            almacen_nombre = almacen.nombre if almacen else ''
            almacen_id = almacen.id if almacen else None
            items_out = []

            for oi in orden.items.all():
                alm_id = oi.almacen_id or almacen_id
                if not alm_id:
                    continue
                try:
                    alm = Almacen.objects.get(pk=alm_id, sucursal__vendor=vendor)
                except Almacen.DoesNotExist:
                    continue

                dists = list(oi.distribuciones.all())
                if dists:
                    for dist in dists:
                        variante = dist.variante
                        var_id = variante.id if variante else None
                        ya_dev = _cantidad_ya_devuelta_en_orden(
                            orden, oi.producto_id, alm_id, var_id,
                        )
                        inv_check = Inventory.objects.filter(
                            product=oi.producto,
                            almacen=alm,
                            is_active=True,
                        ).first()
                        stock_disp = inv_check.quantity if inv_check else 0
                        puede = stock_disp > 0 and ya_dev < dist.cantidad
                        items_out.append({
                            'item_id': dist.id,
                            'orden_item_id': oi.id,
                            'orden_distribucion_id': dist.id,
                            'producto_id': oi.producto_id,
                            'producto_nombre': oi.producto.name if oi.producto else '',
                            'variante_id': var_id,
                            'variante_descripcion': _variante_descripcion(variante),
                            'cantidad_comprada': dist.cantidad,
                            'cantidad_ya_devuelta': ya_dev,
                            'puede_devolver': puede,
                            'almacen_id': alm_id,
                        })
                else:
                    variante = oi.variante
                    var_id = variante.id if variante else None
                    ya_dev = _cantidad_ya_devuelta_en_orden(
                        orden, oi.producto_id, alm_id, var_id,
                    )
                    inv_check = Inventory.objects.filter(
                        product=oi.producto,
                        almacen=alm,
                        is_active=True,
                    ).first()
                    stock_disp = inv_check.quantity if inv_check else 0
                    puede = stock_disp > 0 and ya_dev < oi.cantidad
                    items_out.append({
                        'item_id': oi.id,
                        'orden_item_id': oi.id,
                        'orden_distribucion_id': None,
                        'producto_id': oi.producto_id,
                        'producto_nombre': oi.producto.name if oi.producto else '',
                        'variante_id': var_id,
                        'variante_descripcion': _variante_descripcion(variante),
                        'cantidad_comprada': oi.cantidad,
                        'cantidad_ya_devuelta': ya_dev,
                        'puede_devolver': puede,
                        'almacen_id': alm_id,
                    })

            if not items_out:
                continue

            payload.append({
                'orden_id': orden.id,
                'numero_orden': orden.numero,
                'proveedor_id': orden.proveedor_id,
                'proveedor_nombre': (
                    orden.proveedor.nombre if orden.proveedor else ''
                ),
                'fecha': orden.fecha.isoformat() if orden.fecha else None,
                'almacen': almacen_nombre,
                'almacen_id': almacen_id,
                'items': items_out,
            })

        return Response(payload)


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

