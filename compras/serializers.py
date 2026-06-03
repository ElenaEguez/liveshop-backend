from django.db import transaction
from django.db.models import Q, Sum

from rest_framework import serializers

from compras.models import (
    Proveedor,
    OrdenCompra,
    OrdenCompraItem,
    OrdenCompraItemDistribucion,
    DevolucionCompra,
    DevolucionCompraItem,
)
from products.models import Inventory, Product, ProductVariant
from vendors.models import Almacen, KardexMovimiento

# Motivos de kardex que representan ventas (salida de stock).
MOTIVOS_VENTA_KARDEX = ('venta', 'venta_live')


def _variante_descripcion(variante):
    if not variante:
        return ''
    partes = [getattr(variante, 'talla', ''), getattr(variante, 'color', '')]
    texto = ' / '.join(p for p in partes if p)
    return texto or f'ID {variante.id}'


def _tiene_ventas_registradas(producto, almacen, variante=None, desde_fecha=None):
    """True si hubo al menos una salida por venta en el almacén (opc. desde fecha de compra)."""
    qs = KardexMovimiento.objects.filter(
        tipo='salida',
        motivo__in=MOTIVOS_VENTA_KARDEX,
        inventory__product=producto,
        almacen=almacen,
    )
    if variante:
        qs = qs.filter(variant=variante)
    else:
        qs = qs.filter(variant__isnull=True)
    if desde_fecha is not None:
        qs = qs.filter(created_at__date__gte=desde_fecha)
    return qs.exists()


def _assert_sin_ventas_para_devolucion(producto, almacen, variante=None, desde_fecha=None):
    if not _tiene_ventas_registradas(producto, almacen, variante, desde_fecha):
        return
    var_txt = _variante_descripcion(variante) or 'sin variante'
    raise serializers.ValidationError({
        'items': (
            f'No se puede devolver: el producto "{producto.name}" ({var_txt}) '
            f'ya tiene ventas registradas en este almacén.'
        ),
    })


def _cantidad_ya_devuelta_en_orden(orden, producto_id, almacen_id, variante_id=None):
    qs = DevolucionCompraItem.objects.filter(
        devolucion__orden_compra=orden,
        producto_id=producto_id,
        almacen_id=almacen_id,
    )
    if variante_id:
        qs = qs.filter(variante_id=variante_id)
    else:
        qs = qs.filter(variante__isnull=True)
    return qs.aggregate(total=Sum('cantidad'))['total'] or 0


def _procesar_fila_devolucion(dev, vendor, user, row, documento_ref, notas):
    """
    row: dict con producto, almacen, cantidad (ints); variante opcional.
    Descuenta inventario, kardex y stock_extra de variante; crea DevolucionCompraItem.
    """
    producto = Product.objects.get(pk=row['producto'], vendor=vendor)
    almacen = Almacen.objects.select_related('sucursal').get(
        pk=row['almacen'],
        sucursal__vendor=vendor,
    )
    variante = None
    if row.get('variante'):
        variante = ProductVariant.objects.get(
            pk=row['variante'],
            product=producto,
        )
    cantidad = row['cantidad']

    inv = Inventory.objects.select_for_update().filter(
        product=producto,
        almacen=almacen,
        is_active=True,
    ).first()
    stock_disponible = inv.quantity if inv else 0
    if stock_disponible < cantidad:
        raise serializers.ValidationError({
            'items': (
                f'Stock insuficiente para devolver "{producto.name}": '
                f'disponible {stock_disponible} uds, '
                f'solicitado {cantidad} uds.'
            )
        })

    from products.stock_service import StockError, apply_stock_delta, product_has_variants

    if product_has_variants(producto.id) and not variante:
        raise serializers.ValidationError(
            {
                'items': (
                    f'"{producto.name}" tiene variantes: '
                    f'indique la variante en la devolución.'
                ),
            },
        )

    doc = f'DCP-{dev.pk}' + (f' ({documento_ref})' if documento_ref else '')
    try:
        apply_stock_delta(
            product=producto,
            almacen=almacen,
            delta=-int(cantidad),
            variant=variante,
            usuario=user,
            motivo='devolucion_compra',
            documento_ref=doc,
            notas=notas or 'Devolución a proveedor',
        )
    except StockError as exc:
        raise serializers.ValidationError({'items': str(exc)}) from exc

    DevolucionCompraItem.objects.create(
        devolucion=dev,
        producto=producto,
        variante=variante,
        almacen=almacen,
        cantidad=cantidad,
    )


class ProveedorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Proveedor
        fields = ['id', 'nombre', 'contacto', 'telefono', 'email', 'notas', 'activo']


class OrdenCompraItemDistribucionSerializer(serializers.ModelSerializer):
    variante_detalle = serializers.SerializerMethodField()

    class Meta:
        model = OrdenCompraItemDistribucion
        fields = ['id', 'variante', 'variante_detalle', 'cantidad']

    def get_variante_detalle(self, obj):
        v = obj.variante
        return {
            'id': v.id,
            'talla': getattr(v, 'talla', ''),
            'color': getattr(v, 'color', ''),
            'color_hex': getattr(v, 'color_hex', ''),
        }


class OrdenCompraItemSerializer(serializers.ModelSerializer):
    producto_nombre = serializers.SerializerMethodField()
    variante_detalle = serializers.SerializerMethodField()
    distribuciones = OrdenCompraItemDistribucionSerializer(many=True, read_only=True)

    class Meta:
        model = OrdenCompraItem
        fields = [
            'id', 'producto', 'producto_nombre',
            'variante', 'variante_detalle',
            'distribuciones',
            'almacen',
            'descripcion', 'cantidad',
            'costo_mercaderia', 'flete_unitario',
            'costo_unitario_total',
            'porcentaje_ganancia', 'precio_venta_sugerido', 'precio_venta_es_manual',
            'precio_unitario', 'subtotal'
        ]
        read_only_fields = ['subtotal', 'costo_unitario_total']

    def get_producto_nombre(self, obj):
        return obj.producto.name if obj.producto else ''

    def get_variante_detalle(self, obj):
        if not obj.variante:
            return None
        v = obj.variante
        return {
            'id': v.id,
            'talla': getattr(v, 'talla', ''),
            'color': getattr(v, 'color', ''),
            'color_hex': getattr(v, 'color_hex', ''),
        }


class OrdenCompraSerializer(serializers.ModelSerializer):
    items = OrdenCompraItemSerializer(many=True, read_only=True)
    proveedor_data = ProveedorSerializer(source='proveedor', read_only=True)
    cantidad_total = serializers.SerializerMethodField()

    class Meta:
        model = OrdenCompra
        fields = [
            'id', 'numero', 'proveedor', 'proveedor_data',
            'factura_compra', 'sucursal', 'almacen', 'fecha', 'fecha_entrega',
            'estado', 'notas', 'subtotal', 'descuento',
            'total', 'cantidad_total', 'items', 'created_by', 'created_at',
            'updated_at'
        ]
        read_only_fields = [
            'numero', 'subtotal', 'total',
            'created_by', 'created_at', 'updated_at'
        ]

    def get_cantidad_total(self, obj):
        return sum(item.cantidad for item in obj.items.all())


class DevolucionCompraItemReadSerializer(serializers.ModelSerializer):
    producto_nombre = serializers.CharField(source='producto.name', read_only=True)

    class Meta:
        model = DevolucionCompraItem
        fields = (
            'id', 'producto', 'producto_nombre', 'variante', 'almacen', 'cantidad',
        )


class DevolucionCompraSerializer(serializers.ModelSerializer):
    items = DevolucionCompraItemReadSerializer(many=True, read_only=True)

    class Meta:
        model = DevolucionCompra
        fields = (
            'id', 'vendor', 'created_by', 'documento_ref', 'notas',
            'orden_compra', 'created_at', 'items',
        )
        read_only_fields = (
            'id', 'vendor', 'created_by', 'created_at', 'items', 'orden_compra',
        )


class DevolucionCompraItemWriteSerializer(serializers.Serializer):
    producto = serializers.IntegerField()
    variante = serializers.IntegerField(required=False, allow_null=True)
    almacen = serializers.IntegerField()
    cantidad = serializers.IntegerField(min_value=1)


class DevolucionCompraCreateSerializer(serializers.Serializer):
    documento_ref = serializers.CharField(max_length=120, required=False, allow_blank=True)
    notas = serializers.CharField(required=False, allow_blank=True)
    orden_compra = serializers.IntegerField(required=False, allow_null=True)
    items = serializers.ListField(child=serializers.DictField(), allow_empty=False)

    def validate(self, attrs):
        vendor = self.context['vendor']
        raw_items = attrs.get('items') or []
        orden_pk = attrs.get('orden_compra')

        if orden_pk:
            try:
                orden = OrdenCompra.objects.prefetch_related(
                    'items__distribuciones',
                ).get(
                    pk=orden_pk, vendor=vendor,
                )
            except OrdenCompra.DoesNotExist:
                raise serializers.ValidationError(
                    {'orden_compra': 'Orden no encontrada.'}
                )
            if orden.estado != 'recibida':
                raise serializers.ValidationError(
                    {
                        'orden_compra': (
                            'Solo órdenes en estado «recibida» permiten devolución por compra.'
                        )
                    }
                )
            items_by_id = {i.id: i for i in orden.items.all()}
            resolved = []
            seen_items = set()
            seen_dist = set()
            for raw in raw_items:
                if not isinstance(raw, dict):
                    raise serializers.ValidationError({'items': 'Cada ítem debe ser un objeto.'})

                ddid = raw.get('orden_distribucion_id')
                if ddid is not None:
                    try:
                        ddid = int(ddid)
                    except (TypeError, ValueError):
                        raise serializers.ValidationError(
                            {'items': 'orden_distribucion_id inválido.'}
                        )
                    try:
                        cant = int(raw.get('cantidad'))
                    except (TypeError, ValueError):
                        raise serializers.ValidationError({'items': 'cantidad inválida.'})
                    if cant < 1:
                        continue
                    if ddid in seen_dist:
                        raise serializers.ValidationError(
                            {'items': f'Distribución de orden duplicada (id {ddid}).'}
                        )
                    seen_dist.add(ddid)
                    try:
                        dist = OrdenCompraItemDistribucion.objects.select_related(
                            'item', 'variante',
                        ).get(pk=ddid, item__orden=orden)
                    except OrdenCompraItemDistribucion.DoesNotExist:
                        raise serializers.ValidationError(
                            {'items': f'La distribución {ddid} no pertenece a esta orden.'}
                        )
                    if cant > dist.cantidad:
                        raise serializers.ValidationError(
                            {
                                'items': (
                                    f'No puede devolver más de {dist.cantidad} uds. '
                                    f'en esta variante (compradas en la orden).'
                                )
                            }
                        )
                    oi = dist.item
                    almacen_id = oi.almacen_id or orden.almacen_id
                    if not almacen_id:
                        raise serializers.ValidationError(
                            {
                                'items': (
                                    'La línea no tiene almacén asignado; '
                                    'no se puede devolver desde orden.'
                                )
                            }
                        )
                    resolved.append({
                        'producto': oi.producto_id,
                        'almacen': almacen_id,
                        'cantidad': cant,
                        'variante': dist.variante_id,
                        'desde_fecha': orden.fecha,
                    })
                    continue

                oid = raw.get('orden_item_id')
                if oid is None:
                    raise serializers.ValidationError(
                        {
                            'items': (
                                'Cada ítem debe incluir orden_item_id + cantidad, '
                                'o orden_distribucion_id + cantidad.'
                            )
                        }
                    )
                try:
                    oid = int(oid)
                except (TypeError, ValueError):
                    raise serializers.ValidationError({'items': 'orden_item_id inválido.'})
                try:
                    cant = int(raw.get('cantidad'))
                except (TypeError, ValueError):
                    raise serializers.ValidationError({'items': 'cantidad inválida.'})
                if cant < 1:
                    continue
                if oid in seen_items:
                    raise serializers.ValidationError(
                        {'items': f'Ítem de orden duplicado (id {oid}).'}
                    )
                seen_items.add(oid)
                oi = items_by_id.get(oid)
                if not oi:
                    raise serializers.ValidationError(
                        {'items': f'La línea {oid} no pertenece a esta orden.'}
                    )
                if list(oi.distribuciones.all()):
                    raise serializers.ValidationError(
                        {
                            'items': (
                                f'La línea {oid} tiene distribución por variantes; '
                                'use orden_distribucion_id con la cantidad por cada variante.'
                            )
                        }
                    )
                if cant > oi.cantidad:
                    raise serializers.ValidationError(
                        {
                            'items': (
                                f'"{oi.producto.name if oi.producto else "Producto"}": '
                                f'no puede devolver más de {oi.cantidad} uds. (compradas en la orden).'
                            )
                        }
                    )
                almacen_id = oi.almacen_id or orden.almacen_id
                if not almacen_id:
                    raise serializers.ValidationError(
                        {
                            'items': (
                                f'La línea de orden {oid} no tiene almacén; '
                                'no se puede devolver desde orden.'
                            )
                        }
                    )
                row = {
                    'producto': oi.producto_id,
                    'almacen': almacen_id,
                    'cantidad': cant,
                }
                if oi.variante_id:
                    row['variante'] = oi.variante_id
                row['desde_fecha'] = orden.fecha
                resolved.append(row)
            if not resolved:
                raise serializers.ValidationError(
                    {'items': 'Indique al menos una cantidad a devolver (> 0).'}
                )
            attrs['_orden'] = orden
            attrs['_resolved_items'] = resolved
        else:
            writes = DevolucionCompraItemWriteSerializer(data=raw_items, many=True)
            if not writes.is_valid():
                raise serializers.ValidationError({'items': writes.errors})
            attrs['_orden'] = None
            attrs['_resolved_items'] = writes.validated_data

        return attrs

    def create(self, validated_data):
        vendor = self.context['vendor']
        user = self.context['request'].user
        items_data = validated_data.pop('_resolved_items')
        orden = validated_data.pop('_orden', None)
        validated_data.pop('items', None)
        validated_data.pop('orden_compra', None)
        documento_ref = validated_data.get('documento_ref') or ''
        notas = validated_data.get('notas') or ''

        with transaction.atomic():
            dev = DevolucionCompra.objects.create(
                vendor=vendor,
                created_by=user,
                documento_ref=documento_ref,
                notas=notas,
                orden_compra=orden,
            )
            for row in items_data:
                _procesar_fila_devolucion(dev, vendor, user, row, documento_ref, notas)

        return dev
