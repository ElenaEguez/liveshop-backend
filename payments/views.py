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
from .models import Payment, Devolucion, DevolucionItem, VentaPOS, VentaPOSItem
from .serializers import (
    PaymentSerializer,
    PaymentConfirmSerializer,
    DevolucionSerializer,
    VentaPOSSimpleSerializer,
)
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

        if hasattr(request.user, 'vendor_profile'):
            allowed = True
        else:
            role = get_role_for_user(request.user)
            allowed = role is not None and getattr(role, 'perm_payments', False)
        if not allowed:
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

        if hasattr(request.user, 'vendor_profile'):
            allowed = True
        else:
            role = get_role_for_user(request.user)
            allowed = role is not None and getattr(role, 'perm_payments', False)
        if not allowed:
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


class BuscarVentaParaDevolucionView(APIView):
    """
    Busca una VentaPOS por número de ticket o ID.
    GET /api/v1/payments/devoluciones/buscar-venta/?ticket=001
    GET /api/v1/payments/devoluciones/buscar-venta/?id=5
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        vendor = get_vendor_for_user(request.user)
        if not vendor:
            return Response(
                {'error': 'Sin vendor asignado'},
                status=400)

        ticket = request.query_params.get('ticket')
        venta_id = request.query_params.get('id')

        if not ticket and not venta_id:
            return Response(
                {'error': 'Proporciona ticket o id'},
                status=400)

        try:
            if ticket:
                venta = VentaPOS.objects.prefetch_related(
                    'items__product',
                    'items__variant',
                    'items__devoluciones',
                ).get(
                    vendor=vendor,
                    numero_ticket=ticket
                )
            else:
                venta = VentaPOS.objects.prefetch_related(
                    'items__product',
                    'items__variant',
                    'items__devoluciones',
                ).get(
                    vendor=vendor,
                    id=venta_id
                )
        except VentaPOS.DoesNotExist:
            return Response(
                {'error': 'Venta no encontrada'},
                status=404)

        if venta.status == 'devuelto':
            return Response(
                {'error': 'Esta venta ya fue devuelta '
                          'completamente'},
                status=400)

        return Response(VentaPOSSimpleSerializer(venta).data)


class DevolucionViewSet(viewsets.ModelViewSet):
    """
    CRUD de devoluciones.
    Crear: POST /api/v1/payments/devoluciones/
    Listar: GET /api/v1/payments/devoluciones/
    """
    permission_classes = [IsAuthenticated]
    serializer_class = DevolucionSerializer
    http_method_names = ['get', 'post', 'head', 'options']

    def get_queryset(self):
        vendor = get_vendor_for_user(self.request.user)
        if not vendor:
            return Devolucion.objects.none()
        qs = Devolucion.objects.filter(
            vendor=vendor
        ).select_related(
            'venta', 'procesado_por'
        ).prefetch_related(
            'items__venta_item__product',
            'items__venta_item__variant',
        )
        venta_id = self.request.query_params.get('venta')
        if venta_id:
            qs = qs.filter(venta_id=venta_id)
        return qs

    def create(self, request, *args, **kwargs):
        vendor = get_vendor_for_user(request.user)
        if not vendor:
            return Response(
                {'error': 'Sin vendor asignado'},
                status=400)

        venta_id = request.data.get('venta')
        tipo_resolucion = request.data.get('tipo_resolucion')
        motivo = request.data.get('motivo', '')
        items_data = request.data.get('items', [])

        if not venta_id:
            return Response(
                {'error': 'venta es requerida'},
                status=400)
        if not tipo_resolucion:
            return Response(
                {'error': 'tipo_resolucion es requerido'},
                status=400)
        if tipo_resolucion not in ('cambio', 'devolucion_dinero'):
            return Response(
                {'error': 'tipo_resolucion inválido'},
                status=400)
        if not items_data:
            return Response(
                {'error': 'items es requerido'},
                status=400)

        try:
            venta = VentaPOS.objects.select_related(
                'caja__sucursal'
            ).prefetch_related(
                'items__devoluciones'
            ).get(id=venta_id, vendor=vendor)
        except VentaPOS.DoesNotExist:
            return Response(
                {'error': 'Venta no encontrada'},
                status=404)

        if venta.status == 'devuelto':
            return Response(
                {'error': 'Esta venta ya fue devuelta '
                          'completamente'},
                status=400)

        almacen = None
        if venta.caja and venta.caja.sucursal:
            almacen = venta.caja.sucursal.almacenes.filter(
                activo=True
            ).first()

        devolucion_items = []
        monto_total = 0

        for item_data in items_data:
            venta_item_id = item_data.get('venta_item')
            try:
                cantidad = int(item_data.get('cantidad', 1))
            except (TypeError, ValueError):
                return Response(
                    {'error': 'cantidad inválida'},
                    status=400)
            if cantidad <= 0:
                return Response(
                    {'error': 'cantidad debe ser mayor a 0'},
                    status=400)

            try:
                venta_item = VentaPOSItem.objects.prefetch_related(
                    'devoluciones'
                ).select_related(
                    'product', 'variant'
                ).get(
                    id=venta_item_id,
                    venta=venta
                )
            except VentaPOSItem.DoesNotExist:
                return Response(
                    {'error': (
                        f'Ítem {venta_item_id} '
                        f'no pertenece a esta venta'
                    )}, status=400)

            ya_devuelto = sum(
                di.cantidad
                for di in venta_item.devoluciones.all()
            )
            disponible = venta_item.cantidad - ya_devuelto

            if cantidad > disponible:
                return Response(
                    {'error': (
                        f'"{venta_item.product.name}": '
                        f'solo {disponible} unidades '
                        f'disponibles para devolver'
                    )}, status=400)

            subtotal = cantidad * venta_item.precio_unitario
            monto_total += subtotal
            devolucion_items.append({
                'venta_item': venta_item,
                'cantidad': cantidad,
                'precio_unitario': venta_item.precio_unitario,
                'subtotal': subtotal,
                'almacen': almacen,
            })

        total_items_venta = sum(
            i.cantidad for i in venta.items.all())
        total_devolviendo = sum(
            d['cantidad'] for d in devolucion_items)

        items_ya_devueltos = sum(
            sum(di.cantidad
                for di in i.devoluciones.all())
            for i in venta.items.prefetch_related(
                'devoluciones').all()
        )
        tipo = ('total'
                if (items_ya_devueltos + total_devolviendo)
                >= total_items_venta
                else 'parcial')

        from products.stock_service import StockError, apply_stock_delta

        with transaction.atomic():
            devolucion = Devolucion.objects.create(
                venta=venta,
                vendor=vendor,
                tipo=tipo,
                tipo_resolucion=tipo_resolucion,
                motivo=motivo,
                monto_devuelto=monto_total,
                procesado_por=request.user,
            )

            for d in devolucion_items:
                DevolucionItem.objects.create(
                    devolucion=devolucion,
                    venta_item=d['venta_item'],
                    cantidad=d['cantidad'],
                    precio_unitario=d['precio_unitario'],
                )

                if d['almacen']:
                    try:
                        apply_stock_delta(
                            product=d['venta_item'].product,
                            almacen=d['almacen'],
                            delta=int(d['cantidad']),
                            variant=d['venta_item'].variant,
                            usuario=request.user,
                            motivo='devolucion',
                            documento_ref=(
                                f'DEV-{devolucion.id}-T{venta.numero_ticket}'
                            ),
                            notas=(
                                f'Devolución de venta #{venta.numero_ticket}. '
                                f'Motivo: {motivo or "Sin motivo"}'
                            ),
                        )
                    except StockError as exc:
                        return Response(
                            {'error': str(exc)},
                            status=status.HTTP_400_BAD_REQUEST,
                        )

            if tipo == 'total':
                venta.status = 'devuelto'
            else:
                venta.status = 'parcialmente_devuelto'
            venta.save(update_fields=['status'])

        devolucion = Devolucion.objects.prefetch_related(
            'items__venta_item__product',
            'items__venta_item__variant'
        ).select_related(
            'venta', 'procesado_por'
        ).get(pk=devolucion.pk)

        return Response(
            DevolucionSerializer(devolucion).data,
            status=status.HTTP_201_CREATED)

