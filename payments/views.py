from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.views import APIView
from rest_framework.parsers import MultiPartParser, FormParser
from django.utils import timezone
from django.shortcuts import get_object_or_404
from django.db import transaction
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from .models import Payment
from .serializers import PaymentSerializer, PaymentConfirmSerializer
from orders.models import Reservation
from vendors.models import Vendor
from vendors.permissions import IsVendorOrTeamMember, get_vendor_for_user, get_role_for_user


def _emit_vendor_update(vendor_id, event_type, data):
    """Send a real-time event to the vendor's WebSocket group."""
    try:
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f'vendor_{vendor_id}',
            {'type': 'vendor_update', 'event_type': event_type, 'data': data},
        )
    except Exception:
        pass  # never block the HTTP response due to WS errors


class PublicPaymentCreateView(APIView):
    permission_classes = [AllowAny]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        reservation_id = request.data.get('reservation_id')
        payment_method = request.data.get('payment_method')
        receipt_image = request.FILES.get('receipt_image')
        customer_reference = request.data.get('customer_reference', '')

        if not reservation_id or not payment_method or not receipt_image:
            return Response(
                {'error': 'reservation_id, payment_method y receipt_image son requeridos.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            reservation = Reservation.objects.get(id=reservation_id)
        except Reservation.DoesNotExist:
            return Response(
                {'error': 'Reserva no encontrada.'},
                status=status.HTTP_404_NOT_FOUND
            )

        if hasattr(reservation, 'payment'):
            return Response(
                {'error': 'Esta reserva ya tiene un comprobante enviado.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        payment = Payment.objects.create(
            reservation=reservation,
            amount=reservation.total_price,
            payment_method=payment_method,
            receipt_image=receipt_image,
            customer_reference=customer_reference,
            status='submitted',
            submitted_at=timezone.now()
        )

        reservation.status = 'confirmed'
        reservation.save()

        _emit_vendor_update(
            reservation.session.vendor_id,
            'payment_submitted',
            {'payment_id': payment.id, 'reservation_id': reservation.id},
        )

        return Response({
            'id': payment.id,
            'status': payment.status,
            'amount': str(payment.amount),
            'message': 'Comprobante enviado correctamente. El vendedor lo verificará pronto.'
        }, status=status.HTTP_201_CREATED)

class PaymentPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100


class PaymentViewSet(viewsets.ModelViewSet):
    serializer_class = PaymentSerializer
    permission_classes = [IsAuthenticated, IsVendorOrTeamMember]
    queryset = Payment.objects.all()
    pagination_class = PaymentPagination

    def _get_vendor(self):
        vendor = get_vendor_for_user(self.request.user)
        if not vendor:
            raise PermissionDenied("Sin perfil de vendedor asociado.")
        return vendor

    def _assert_payment_permission(self, payment):
        """Verify the user belongs to the vendor that owns this payment."""
        vendor = self._get_vendor()
        payment_vendor = payment.reservation.session.vendor
        if vendor != payment_vendor:
            raise PermissionDenied("No tienes permiso para gestionar este pago.")

    def get_queryset(self):
        vendor = self._get_vendor()
        qs = Payment.objects.filter(
            reservation__session__vendor=vendor
        ).select_related(
            "reservation", "reservation__product", "reservation__session"
        )
        status_filter = self.request.query_params.get("status")
        if status_filter:
            qs = qs.filter(status=status_filter)
        return qs

    def create(self, request, *args, **kwargs):
        """Cliente crea un pago y sube el comprobante"""
        with transaction.atomic():
            serializer = self.get_serializer(data=request.data)
            serializer.is_valid(raise_exception=True)

            payment = serializer.save()
            payment.status = 'submitted'
            payment.submitted_at = timezone.now()
            payment.save()

            return Response(
                PaymentSerializer(payment).data,
                status=status.HTTP_201_CREATED
            )

    @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated, IsVendorOrTeamMember])
    def confirm(self, request, pk=None):
        """Vendedor confirma un pago"""
        payment = self.get_object()
        self._assert_payment_permission(payment)

        role = get_role_for_user(request.user)
        if role not in ('vendor_owner', 'admin', 'payments'):
            return Response(
                {'error': 'Tu rol no permite confirmar pagos.'},
                status=status.HTTP_403_FORBIDDEN
            )

        if payment.status != 'submitted':
            return Response(
                {'error': 'Solo puedes confirmar pagos en estado "presentado".'},
                status=status.HTTP_400_BAD_REQUEST
            )

        serializer = PaymentConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            payment.status = 'confirmed'
            payment.confirmed_at = timezone.now()
            payment.vendor_notes = serializer.validated_data.get('vendor_notes', '')
            payment.save()

            # Actualizar estado de la reserva a 'paid'
            payment.reservation.status = 'paid'
            payment.reservation.save()

        _emit_vendor_update(
            payment.reservation.session.vendor_id,
            'payment_confirmed',
            {'payment_id': payment.id, 'reservation_id': payment.reservation_id},
        )

        return Response(
            PaymentSerializer(payment).data,
            status=status.HTTP_200_OK
        )

    @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated, IsVendorOrTeamMember])
    def reject(self, request, pk=None):
        """Vendedor rechaza un pago"""
        payment = self.get_object()
        self._assert_payment_permission(payment)

        role = get_role_for_user(request.user)
        if role not in ('vendor_owner', 'admin', 'payments'):
            return Response(
                {'error': 'Tu rol no permite rechazar pagos.'},
                status=status.HTTP_403_FORBIDDEN
            )

        if payment.status != 'submitted':
            return Response(
                {'error': 'Solo puedes rechazar pagos en estado "presentado".'},
                status=status.HTTP_400_BAD_REQUEST
            )

        serializer = PaymentConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            payment.status = 'rejected'
            payment.vendor_notes = serializer.validated_data.get('vendor_notes', '')
            payment.save()

            # Actualizar estado de la reserva a 'pending'
            payment.reservation.status = 'pending'
            payment.reservation.save()

        _emit_vendor_update(
            payment.reservation.session.vendor_id,
            'payment_rejected',
            {'payment_id': payment.id, 'reservation_id': payment.reservation_id},
        )

        return Response(
            PaymentSerializer(payment).data,
            status=status.HTTP_200_OK
        )

    @action(detail=False, methods=['get'], permission_classes=[AllowAny])
    def get_vendor_qr(self, request):
        """Obtener QR e instrucciones de pago del vendedor (público)"""
        vendor_id = request.query_params.get('vendor_id')

        if not vendor_id:
            return Response(
                {'error': 'Parámetro vendor_id es requerido.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        vendor = get_object_or_404(Vendor, id=vendor_id)

        return Response({
            'vendor_id': vendor.id,
            'vendor_name': vendor.nombre_tienda,
            'payment_qr_image': vendor.payment_qr_image.url if vendor.payment_qr_image else None,
            'payment_instructions': vendor.payment_instructions,
            'accepted_payment_methods': vendor.accepted_payment_methods,
        })

