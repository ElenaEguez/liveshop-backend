from django.db import transaction
from django.shortcuts import get_object_or_404

from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from vendors.models import KardexMovimiento
from vendors.permissions import get_vendor_for_user
from .models import Inventory
from .serializers import KardexMovimientoSerializer


class KardexPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100


class KardexListView(APIView):
    """
    GET /api/v1/inventory/kardex/
    Filtros: product_id, almacen_id, tipo, motivo, fecha_desde, fecha_hasta
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        vendor = get_vendor_for_user(request.user)
        if not vendor:
            return Response({'error': 'Sin perfil de vendedor.'}, status=403)

        qs = KardexMovimiento.objects.filter(
            inventory__product__vendor=vendor
        ).select_related(
            'inventory__product', 'almacen', 'usuario'
        ).order_by('-created_at')

        p = request.query_params
        if p.get('product_id'):
            qs = qs.filter(inventory__product_id=p['product_id'])
        if p.get('almacen_id'):
            qs = qs.filter(almacen_id=p['almacen_id'])
        if p.get('tipo'):
            qs = qs.filter(tipo=p['tipo'])
        if p.get('motivo'):
            qs = qs.filter(motivo=p['motivo'])
        if p.get('fecha_desde'):
            qs = qs.filter(created_at__date__gte=p['fecha_desde'])
        if p.get('fecha_hasta'):
            qs = qs.filter(created_at__date__lte=p['fecha_hasta'])

        paginator = KardexPagination()
        page = paginator.paginate_queryset(qs, request)
        return paginator.get_paginated_response(
            KardexMovimientoSerializer(page, many=True).data
        )


class KardexAjusteView(APIView):
    """
    POST /api/v1/inventory/kardex/ajuste/
    Body: { inventory_id, cantidad (int, + entrada / - salida), motivo, notas }
    Actualiza quantity en Inventory y registra KardexMovimiento.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        vendor = get_vendor_for_user(request.user)
        if not vendor:
            return Response({'error': 'Sin perfil de vendedor.'}, status=403)

        inventory_id = request.data.get('inventory_id')
        raw_cantidad = request.data.get('cantidad')
        motivo = request.data.get('motivo', 'ajuste_manual')
        notas = request.data.get('notas', '')

        if inventory_id is None or raw_cantidad is None:
            return Response(
                {'error': 'inventory_id y cantidad son requeridos.'},
                status=400,
            )

        try:
            cantidad = int(raw_cantidad)
        except (ValueError, TypeError):
            return Response({'error': 'cantidad debe ser un entero.'}, status=400)

        inventory = get_object_or_404(
            Inventory, id=inventory_id,
            product__vendor=vendor, is_active=True,
        )

        with transaction.atomic():
            inv = Inventory.objects.select_for_update().get(pk=inventory.pk)
            stock_anterior = inv.quantity
            nuevo_stock = inv.quantity + cantidad

            if nuevo_stock < 0:
                return Response(
                    {'error': f'Ajuste resultaría en stock negativo ({nuevo_stock}).'},
                    status=400,
                )

            inv.quantity = nuevo_stock
            inv.save(update_fields=['quantity'])

            if cantidad > 0:
                tipo = 'entrada'
            elif cantidad < 0:
                tipo = 'salida'
            else:
                tipo = 'ajuste'

            movimiento = KardexMovimiento.objects.create(
                inventory=inv,
                almacen=inv.almacen,
                tipo=tipo,
                motivo=motivo,
                cantidad=cantidad,
                stock_anterior=stock_anterior,
                stock_actual=inv.quantity,
                usuario=request.user,
                notas=notas,
            )

        return Response(KardexMovimientoSerializer(movimiento).data, status=201)
