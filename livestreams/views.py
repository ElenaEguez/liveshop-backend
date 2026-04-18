from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.views import APIView
from django.shortcuts import get_object_or_404
from django.http import HttpResponseRedirect, HttpResponse
from django.utils import timezone
from .models import LiveSession
from .serializers import LiveSessionSerializer
from vendors.permissions import IsVendorOrTeamMember, get_vendor_for_user, get_role_for_user


class PublicLiveSessionDetailView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, slug):
        from products.models import Product, Inventory, ProductImage, ProductVariant
        from orders.models import Reservation
        from django.db.models import Sum

        session = get_object_or_404(LiveSession, slug=slug)

        # Todos los productos activos del vendor, ordenados por created_at ASC
        # (más antiguo primero = PEPS)
        all_products = list(Product.objects.filter(
            vendor=session.vendor,
            is_active=True
        ).order_by('created_at'))

        product_ids = [p.id for p in all_products]

        # Pre-fetch reservas activas (una sola query)
        active_statuses = ['pending', 'confirmed', 'paid']
        reserved_map = dict(
            Reservation.objects.filter(
                product_id__in=product_ids,
                status__in=active_statuses
            ).values('product_id').annotate(total=Sum('quantity')).values_list('product_id', 'total')
        )

        # Pre-fetch primera entrada de inventario activa por producto (una sola query)
        inventory_map: dict = {}
        for inv in Inventory.objects.filter(
            product_id__in=product_ids, is_active=True
        ).order_by('created_at'):
            if inv.product_id not in inventory_map:
                inventory_map[inv.product_id] = inv

        # Deduplicación PEPS: por cada nombre, conservar solo el más antiguo
        # que tenga stock disponible > 0
        vistos: set = set()
        productos_finales = []

        for producto in all_products:
            inv = inventory_map.get(producto.id)
            base_stock = inv.quantity if inv else producto.stock
            reserved = reserved_map.get(producto.id, 0)
            available = max(0, base_stock - reserved)

            if available > 0:
                clave = producto.name.strip().lower()
                if clave not in vistos:
                    vistos.add(clave)
                    productos_finales.append((producto, available))

        # Pre-fetch imágenes solo para los productos seleccionados (una sola query)
        ids_finales = [p.id for p, _ in productos_finales]
        images_map: dict = {}
        for img in ProductImage.objects.filter(product_id__in=ids_finales):
            images_map.setdefault(img.product_id, []).append(img)

        # Pre-fetch variantes estructuradas (con stock por variante)
        variants_map: dict = {}
        for v in ProductVariant.objects.filter(
            product_id__in=ids_finales, is_active=True
        ).order_by('id'):
            variants_map.setdefault(v.product_id, []).append(v)

        products_data = []
        for producto, available in productos_finales:
            imgs = images_map.get(producto.id, [])
            product_variants = variants_map.get(producto.id, [])
            variants_data = [
                {
                    'id': v.id,
                    'size': v.talla,
                    'color': v.color,
                    # stock_extra > 0 → stock propio de la variante
                    # stock_extra == 0 → comparte el stock del producto
                    'stock': v.stock_extra if v.stock_extra > 0 else available,
                    'disponible': v.stock_extra > 0 or available > 0,
                }
                for v in product_variants
            ]
            products_data.append({
                'id': producto.id,
                'name': producto.name,
                'description': producto.description,
                'price': producto.price,
                'stock': producto.stock,
                'variants': variants_data,
                'available_quantity': available,
                'images': [request.build_absolute_uri(img.image.url) for img in imgs],
            })

        return Response({
            'id': session.id,
            'vendor_id': session.vendor.id,
            'title': session.title,
            'status': session.status,
            'platform': session.platform,
            'vendor_name': session.vendor.nombre_tienda,
            'payment_qr_image': (
                request.build_absolute_uri(session.payment_qr_image.url)
                if session.payment_qr_image
                else (
                    request.build_absolute_uri(session.vendor.payment_qr_image.url)
                    if session.vendor.payment_qr_image
                    else None
                )
            ),
            'payment_instructions': session.payment_instructions or session.vendor.payment_instructions or '',
            'allow_multiple_cart': session.allow_multiple_cart,
            'vendor_slug': session.vendor.slug,
            'products': products_data,
        })

class LiveSessionViewSet(viewsets.ModelViewSet):
    serializer_class = LiveSessionSerializer
    permission_classes = [IsAuthenticated, IsVendorOrTeamMember]
    queryset = LiveSession.objects.all()

    def _get_vendor(self):
        vendor = get_vendor_for_user(self.request.user)
        if not vendor:
            raise PermissionDenied("Sin perfil de vendedor asociado.")
        return vendor

    def _check_write_permission(self):
        role = get_role_for_user(self.request.user)
        if role in ('vendor_owner', 'admin'):
            return
        raise PermissionDenied("Tu rol no permite modificar sesiones en vivo.")

    def get_queryset(self):
        qs = LiveSession.objects.filter(vendor=self._get_vendor())
        params = self.request.query_params

        # Filter by exact date (scheduled_at__date)
        fecha = params.get('fecha')
        if fecha:
            qs = qs.filter(scheduled_at__date=fecha)

        # Filter by date range
        fecha_inicio = params.get('fecha_inicio')
        if fecha_inicio:
            qs = qs.filter(scheduled_at__date__gte=fecha_inicio)

        fecha_fin = params.get('fecha_fin')
        if fecha_fin:
            qs = qs.filter(scheduled_at__date__lte=fecha_fin)

        # Filter by slot
        slot = params.get('slot')
        if slot:
            qs = qs.filter(slot=slot)

        # Filter by status
        estado = params.get('estado')
        if estado:
            qs = qs.filter(status=estado)

        return qs

    def perform_create(self, serializer):
        self._check_write_permission()
        vendor = self._get_vendor()
        slot = self.request.data.get('slot', 1)

        # Auto-close any active 'live' session on the same channel before creating a new one
        LiveSession.objects.filter(
            vendor=vendor, slot=slot, status='live'
        ).update(status='ended', ended_at=timezone.now())

        serializer.save(vendor=vendor)

    def update(self, request, *args, **kwargs):
        self._check_write_permission()
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        self._check_write_permission()
        return super().partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        self._check_write_permission()
        return super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=['post'])
    def start_session(self, request, pk=None):
        session = self.get_object()
        if session.status != 'scheduled':
            return Response({'error': 'La sesión ya ha sido iniciada o finalizada.'}, status=status.HTTP_400_BAD_REQUEST)
        session.status = 'live'
        session.started_at = timezone.now()
        session.save()
        serializer = self.get_serializer(session)
        return Response(serializer.data)

    @action(detail=True, methods=['post'])
    def end_session(self, request, pk=None):
        session = self.get_object()
        if session.status != 'live':
            return Response({'error': 'La sesión no está en vivo.'}, status=status.HTTP_400_BAD_REQUEST)
        session.status = 'ended'
        session.ended_at = timezone.now()
        # Eliminar QR al finalizar para liberar espacio
        if session.payment_qr_image:
            import os
            if os.path.isfile(session.payment_qr_image.path):
                os.remove(session.payment_qr_image.path)
            session.payment_qr_image = None
        session.save()
        serializer = self.get_serializer(session)
        return Response(serializer.data)


def live_activo_redirect(request, vendor_slug, slot=1):
    """
    Redirect to the active live session for a vendor+slot.
    URL: /tienda/<vendor_slug>/live-ahora/  or  /tienda/<vendor_slug>/live-ahora/<slot>/
    """
    from vendors.models import Vendor
    try:
        vendor = Vendor.objects.get(slug=vendor_slug)
    except Vendor.DoesNotExist:
        return HttpResponse(
            "<h2>Tienda no encontrada</h2><p>El enlace no corresponde a ninguna tienda registrada.</p>",
            status=404, content_type='text/html'
        )

    session = LiveSession.objects.filter(vendor=vendor, slot=slot, status='live').first()
    if session:
        return HttpResponseRedirect(f'/public/live/{session.slug}')

    return HttpResponse(
        f"<h2>Sin live activo</h2>"
        f"<p>La tienda <strong>{vendor.nombre_tienda}</strong> no tiene un live en vivo en este momento (canal {slot}).</p>"
        f"<p>Por favor intenta más tarde.</p>",
        status=200, content_type='text/html'
    )
