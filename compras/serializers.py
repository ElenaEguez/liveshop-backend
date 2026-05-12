from rest_framework import serializers
from compras.models import Proveedor, OrdenCompra, OrdenCompraItem


class ProveedorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Proveedor
        fields = ['id', 'nombre', 'contacto', 'telefono', 'email', 'notas', 'activo']


class OrdenCompraItemSerializer(serializers.ModelSerializer):
    producto_nombre = serializers.SerializerMethodField()
    variante_detalle = serializers.SerializerMethodField()

    class Meta:
        model = OrdenCompraItem
        fields = [
            'id', 'producto', 'producto_nombre',
            'variante', 'variante_detalle',
            'almacen',
            'descripcion', 'cantidad',
            'costo_mercaderia', 'flete_unitario',
            'costo_unitario_total',
            'porcentaje_ganancia', 'precio_venta_sugerido',
            'precio_unitario', 'subtotal'
        ]
        read_only_fields = ['subtotal', 'costo_unitario_total', 'precio_venta_sugerido']

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
