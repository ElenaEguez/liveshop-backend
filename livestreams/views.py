from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.views import APIView
from django.shortcuts import get_object_or_404
from django.utils import timezone
from .models import LiveSession
from .serializers import LiveSessionSerializer
from vendors.permissions import IsVendorOrTeamMember, get_vendor_for_user, get_role_for_user


class PublicLiveSessionDetailView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, slug):
        from products.models import Product, Inventory, ProductImage
        from orders.models import Reservation
        from django.db.models import Sum

        session = get_object_or_404(LiveSession, slug=slug)

        products = Product.objects.filter(
            vendor=session.vendor,
            is_active=True
        ).values('id', 'name', 'description', 'price', 'stock', 'variants')

        # Pre-fetch active reserved quantities across ALL sessions for each product
        active_statuses = ['pending', 'confirmed', 'paid']
        product_ids = [p['id'] for p in products]
        reserved_map = dict(
            Reservation.objects.filter(
                product_id__in=product_ids,
                status__in=active_statuses
            ).values('product_id').annotate(total=Sum('quantity')).values_list('product_id', 'total')
        )

        products_data = []
        for p in products:
            inv = Inventory.objects.filter(
                product_id=p['id'], is_active=True
            ).first()
            base_stock = inv.quantity if inv else p['stock']
            reserved = reserved_map.get(p['id'], 0)
            p['available_quantity'] = max(0, base_stock - reserved)
            images = ProductImage.objects.filter(product_id=p['id'])
            p['images'] = [request.build_absolute_uri(img.image.url) for img in images]
            products_data.append(p)

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
        return LiveSession.objects.filter(vendor=self._get_vendor())

    def perform_create(self, serializer):
        self._check_write_permission()
        serializer.save(vendor=self._get_vendor())

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
