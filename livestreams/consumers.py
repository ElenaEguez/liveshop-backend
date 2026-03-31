import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from livestreams.models import LiveSession
from orders.models import Reservation
from products.models import Inventory
from django.utils import timezone


class LiveSessionConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.session_id = self.scope['url_route']['kwargs']['session_id']
        self.session_group_name = f'session_{self.session_id}'

        # Agregar conexión al grupo
        await self.channel_layer.group_add(
            self.session_group_name,
            self.channel_name
        )

        await self.accept()

    async def disconnect(self, close_code):
        # Remover conexión del grupo
        await self.channel_layer.group_discard(
            self.session_group_name,
            self.channel_name
        )

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
            
            if data.get('type') == 'new_reservation':
                await self.handle_new_reservation(data)
            else:
                await self.send(text_data=json.dumps({
                    'type': 'error',
                    'message': 'Tipo de mensaje no válido.'
                }))
        except json.JSONDecodeError:
            await self.send(text_data=json.dumps({
                'type': 'error',
                'message': 'Formato JSON inválido.'
            }))
        except Exception as e:
            await self.send(text_data=json.dumps({
                'type': 'error',
                'message': f'Error: {str(e)}'
            }))

    async def handle_new_reservation(self, data):
        """Maneja la creación de una nueva reserva"""
        try:
            product_id = data.get('product_id')
            customer_name = data.get('customer_name')
            customer_phone = data.get('customer_phone')
            quantity = data.get('quantity', 1)

            # Validar datos
            if not all([product_id, customer_name, customer_phone, quantity]):
                await self.send(text_data=json.dumps({
                    'type': 'error',
                    'message': 'Faltan campos requeridos: product_id, customer_name, customer_phone, quantity.'
                }))
                return

            if quantity < 1:
                await self.send(text_data=json.dumps({
                    'type': 'error',
                    'message': 'La cantidad debe ser mayor a 0.'
                }))
                return

            # Crear reserva en la BD
            reservation = await self.create_reservation_in_db(
                self.session_id,
                product_id,
                customer_name,
                customer_phone,
                quantity
            )

            if reservation is None:
                await self.send(text_data=json.dumps({
                    'type': 'error',
                    'message': 'No hay suficiente stock disponible.'
                }))
                return

            # Enviar actualización a todo el grupo
            await self.channel_layer.group_send(
                self.session_group_name,
                {
                    'type': 'reservation_update',
                    'reservation_id': reservation['id'],
                    'customer_name': reservation['customer_name'],
                    'product_id': reservation['product_id'],
                    'product_name': reservation['product_name'],
                    'quantity': reservation['quantity'],
                    'total_price': float(reservation['total_price']),
                    'status': reservation['status'],
                    'created_at': reservation['created_at'],
                }
            )

        except Exception as e:
            await self.send(text_data=json.dumps({
                'type': 'error',
                'message': f'Error al crear la reserva: {str(e)}'
            }))

    async def reservation_update(self, event):
        """Envía la actualización de reserva al WebSocket"""
        await self.send(text_data=json.dumps({
            'type': 'new_reservation',
            'data': {
                'reservation_id': event['reservation_id'],
                'customer_name': event['customer_name'],
                'product_id': event['product_id'],
                'product_name': event['product_name'],
                'quantity': event['quantity'],
                'total_price': event['total_price'],
                'status': event['status'],
                'created_at': event['created_at'],
            }
        }))

    async def payment_confirmed_update(self, event):
        """Envía la actualización de pago confirmado al WebSocket"""
        await self.send(text_data=json.dumps({
            'type': 'payment_confirmed',
            'data': {
                'payment_id': event['payment_id'],
                'reservation_id': event['reservation_id'],
                'customer_name': event['customer_name'],
                'amount': event['amount'],
                'payment_method': event['payment_method'],
                'confirmed_at': event['confirmed_at'],
            }
        }))

    @database_sync_to_async
    def create_reservation_in_db(self, session_id, product_id, customer_name, customer_phone, quantity):
        """Crea la reserva en la BD y decrementa el inventario"""
        try:
            from django.db import transaction

            with transaction.atomic():
                # Verificar que la sesión existe
                session = LiveSession.objects.get(id=session_id)

                # Verificar que el producto existe
                from products.models import Product
                product = Product.objects.get(id=product_id)

                # Verificar inventario disponible
                inventory = Inventory.objects.filter(product=product, is_active=True).first()
                if not inventory or inventory.available_quantity < quantity:
                    return None

                # Crear la reserva
                reservation = Reservation.objects.create(
                    session=session,
                    product=product,
                    customer_name=customer_name,
                    customer_phone=customer_phone,
                    quantity=quantity,
                    status='pending'
                )

                # Decrementar reserved_quantity
                inventory.reserved_quantity += quantity
                inventory.save()

                # Retornar datos de la reserva
                return {
                    'id': reservation.id,
                    'customer_name': reservation.customer_name,
                    'product_id': product.id,
                    'product_name': product.name,
                    'quantity': reservation.quantity,
                    'total_price': reservation.total_price,
                    'status': reservation.status,
                    'created_at': reservation.created_at.isoformat(),
                }

        except LiveSession.DoesNotExist:
            return None
        except Exception:
            return None


class VendorConsumer(AsyncWebsocketConsumer):
    """WebSocket scoped to a vendor — receives payment events for dashboard refresh."""

    async def connect(self):
        self.vendor_id = self.scope['url_route']['kwargs']['vendor_id']
        self.group_name = f'vendor_{self.vendor_id}'
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive(self, text_data):
        pass  # read-only channel — no client messages processed

    async def vendor_update(self, event):
        """Forward any vendor-level event to connected clients."""
        await self.send(text_data=json.dumps({
            'type': event['event_type'],
            'data': event.get('data', {}),
        }))
