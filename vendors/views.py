from django.db import models as django_models
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated, AllowAny
from django.shortcuts import get_object_or_404
from django.db import transaction
from django.contrib.auth import get_user_model
from django.db.models import Count, DecimalField, F, Q, Sum
from django.db.models.functions import Coalesce

import datetime
from decimal import Decimal
from .models import (
    Vendor,
    TeamMember,
    CustomRole,
    Promocion,
    TransferenciaAlmacen,
    TransferenciaAlmacenItem,
    ConteoFisico,
    ConteoFisicoItem,
)
from .serializers import (
    VendorSerializer,
    VendorProfileSerializer,
    TeamMemberSerializer,
    CustomRoleSerializer,
    ConteoFisicoSerializer,
    ConteoFisicoItemSerializer,
    TransferenciaAlmacenSerializer,
)
from .permissions import (
    IsVendorOwner,
    get_vendor_for_user,
    IsVendorOrWarehouseTeamMember,
    get_role_for_user,
)

User = get_user_model()


def _user_has_warehouse_perm(user):
    """Propietario o miembro con perm_warehouse."""
    if hasattr(user, 'vendor_profile'):
        return True
    role = get_role_for_user(user)
    return bool(role and getattr(role, 'perm_warehouse', False))


def _user_has_inventory_perm(user):
    """Propietario o miembro con perm_inventory."""
    if hasattr(user, 'vendor_profile'):
        return True
    role = get_role_for_user(user)
    return bool(role and getattr(role, 'perm_inventory', False))


class VendorProfileView(APIView):
    """View for vendor profile management (current user's vendor profile)"""
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        """Get current user's vendor profile"""
        try:
            vendor = request.user.vendor_profile
            serializer = VendorProfileSerializer(vendor, context={'request': request})
            return Response(serializer.data, status=status.HTTP_200_OK)
        except Vendor.DoesNotExist:
            return Response(
                {'error': 'El usuario no tiene un perfil de vendedor'},
                status=status.HTTP_404_NOT_FOUND
            )

    def put(self, request, *args, **kwargs):
        """Update current user's vendor profile"""
        try:
            vendor = request.user.vendor_profile
        except Vendor.DoesNotExist:
            return Response(
                {'error': 'El usuario no tiene un perfil de vendedor'},
                status=status.HTTP_404_NOT_FOUND
            )

        serializer = VendorProfileSerializer(
            vendor,
            data=request.data,
            partial=True,
            context={'request': request}
        )
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def patch(self, request, *args, **kwargs):
        return self.put(request, *args, **kwargs)


class VendorListView(APIView):
    """View for listing all vendors"""
    permission_classes = [AllowAny]

    def get(self, request, *args, **kwargs):
        """Get list of all vendors"""
        vendors = Vendor.objects.all()
        serializer = VendorSerializer(vendors, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class VendorDetailView(APIView):
    """View for vendor detail by slug"""
    permission_classes = [AllowAny]

    def get(self, request, slug, *args, **kwargs):
        """Get vendor details by slug"""
        vendor = get_object_or_404(Vendor, slug=slug)
        serializer = VendorSerializer(vendor)
        return Response(serializer.data, status=status.HTTP_200_OK)



class CustomRoleViewSet(viewsets.ModelViewSet):
    """CRUD for vendor-defined custom roles. Only vendor owner can manage."""
    serializer_class = CustomRoleSerializer
    permission_classes = [IsAuthenticated, IsVendorOwner]

    def _get_vendor(self):
        try:
            return self.request.user.vendor_profile
        except Vendor.DoesNotExist:
            raise ValidationError("El usuario no tiene un perfil de vendedor.")

    def get_queryset(self):
        return CustomRole.objects.filter(vendor=self._get_vendor())

    def perform_create(self, serializer):
        serializer.save(vendor=self._get_vendor())


class TeamMemberViewSet(viewsets.ModelViewSet):
    """
    CRUD for TeamMembers.
    Only the VendorProfile owner can create/update/delete.
    """
    serializer_class = TeamMemberSerializer
    permission_classes = [IsAuthenticated, IsVendorOwner]

    def _get_vendor(self):
        try:
            return self.request.user.vendor_profile
        except Vendor.DoesNotExist:
            raise ValidationError("El usuario no tiene un perfil de vendedor.")

    def get_queryset(self):
        vendor = self._get_vendor()
        return TeamMember.objects.filter(vendor=vendor).select_related('custom_role', 'user')

    def create(self, request, *args, **kwargs):
        vendor = self._get_vendor()
        limite = getattr(vendor, 'max_usuarios', 3)
        if vendor.team_members.filter(is_active=True).count() >= limite:
            raise ValidationError(f"Límite de {limite} miembros de equipo alcanzado.")

        email        = request.data.get('email')
        nombre       = request.data.get('nombre') or request.data.get('first_name', '')
        apellido     = request.data.get('apellido') or request.data.get('last_name', '')
        custom_role_id = request.data.get('custom_role')
        password     = request.data.get('password')

        if not email:
            raise ValidationError({"email": "El email es requerido."})

        # Validate custom_role belongs to this vendor
        custom_role = None
        if custom_role_id:
            custom_role = get_object_or_404(CustomRole, id=custom_role_id, vendor=vendor)

        user, created = User.objects.get_or_create(
            email=email,
            defaults={'nombre': nombre, 'apellido': apellido}
        )
        if created:
            if password:
                user.set_password(password)
            else:
                user.set_unusable_password()
            user.save()

        if hasattr(user, 'vendor_profile'):
            raise ValidationError("Este usuario ya tiene un perfil de vendedor propio.")

        try:
            existing = user.team_member_profile
            if existing.vendor != vendor:
                raise ValidationError("Este usuario ya es miembro del equipo de otro vendedor.")
            existing.custom_role = custom_role
            existing.is_active = True
            existing.save()
            serializer = self.get_serializer(existing)
            return Response(serializer.data, status=status.HTTP_200_OK)
        except TeamMember.DoesNotExist:
            pass

        team_member = TeamMember.objects.create(
            vendor=vendor, user=user, custom_role=custom_role
        )
        serializer = self.get_serializer(team_member)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def perform_create(self, serializer):
        pass


class VendorDashboardView(APIView):
    """Dashboard summary for the current vendor"""
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        from django.utils import timezone
        from payments.models import Payment, VentaPOS, VentaPOSItem, GastoOperativo
        from orders.models import Reservation

        vendor = get_vendor_for_user(request.user)
        if not vendor:
            return Response({'error': 'Sin perfil de vendedor'}, status=404)

        now = timezone.now()
        today = timezone.localdate()

        # ── Período filtrable ─────────────────────────────────────────────────
        periodo = request.query_params.get('periodo', 'month')
        if periodo == 'today':
            fecha_inicio = today
            fecha_fin = today
        elif periodo == 'week':
            fecha_inicio = today - datetime.timedelta(days=7)
            fecha_fin = today
        elif periodo == 'year':
            fecha_inicio = datetime.date(today.year, 1, 1)
            fecha_fin = today
        else:  # month (default)
            fecha_inicio = datetime.date(today.year, today.month, 1)
            fecha_fin = today

        # ── Productos activos ─────────────────────────────────────────────────
        total_active_products = vendor.products.filter(is_active=True).count()

        # ── Ventas POS del período ────────────────────────────────────────────
        ventas_qs = VentaPOS.objects.filter(
            vendor=vendor,
            status='completada',
            created_at__date__gte=fecha_inicio,
            created_at__date__lte=fecha_fin,
        )

        ingreso_agg = ventas_qs.aggregate(
            total=Coalesce(Sum('total'), Decimal('0'))
        )
        ingreso_total = ingreso_agg['total']
        monthly_sales = float(ingreso_total)

        # ── Ventas por método de pago ─────────────────────────────────────────
        ventas_por_metodo_qs = (
            ventas_qs
            .values('metodo_pago__nombre')
            .annotate(
                total=Coalesce(Sum('total'), Decimal('0')),
                cantidad=Count('id'),
            )
            .order_by('metodo_pago__nombre')
        )
        ventas_por_metodo_pago = {}
        for item in ventas_por_metodo_qs:
            nombre = item['metodo_pago__nombre'] or 'Sin método'
            ventas_por_metodo_pago[nombre] = {
                'total': float(item['total']),
                'cantidad': item['cantidad'],
            }

        # ── Costo total de lo vendido (suma cantidad × costo_unitario) ────────
        costo_agg = VentaPOSItem.objects.filter(
            venta__in=ventas_qs,
        ).aggregate(
            total=Coalesce(
                Sum(F('cantidad') * F('costo_unitario'), output_field=DecimalField()),
                Decimal('0'),
            )
        )
        costo_total = costo_agg['total']

        # ── Utilidades ────────────────────────────────────────────────────────
        utilidad_bruta = float(ingreso_total) - float(costo_total)

        gastos_agg = GastoOperativo.objects.filter(
            vendor=vendor,
            status='activo',
            fecha__gte=fecha_inicio,
            fecha__lte=fecha_fin,
        ).aggregate(
            total=Coalesce(Sum('monto'), Decimal('0'))
        )
        gastos_total = gastos_agg['total']
        utilidad_neta = utilidad_bruta - float(gastos_total)

        # ── Pedidos pendientes (canal live) ───────────────────────────────────
        pending_orders = Reservation.objects.filter(
            product__vendor=vendor,
            status='pending',
        ).count()

        # ── Próximo live ──────────────────────────────────────────────────────
        next_live = None
        upcoming = vendor.live_sessions.filter(
            status='scheduled',
            scheduled_at__gte=now,
        ).order_by('scheduled_at').first()
        if upcoming:
            next_live = upcoming.scheduled_at.isoformat()

        # ── Ventas por producto ───────────────────────────────────────────────
        ventas_por_producto_qs = (
            VentaPOSItem.objects
            .filter(
                venta__vendor=vendor,
                venta__status='completada',
                venta__created_at__date__gte=fecha_inicio,
                venta__created_at__date__lte=fecha_fin,
            )
            .values('product__id', 'product__name', 'product__category__name')
            .annotate(
                unidades=Coalesce(Sum('cantidad'), 0),
                ingresos=Coalesce(
                    Sum(F('cantidad') * F('precio_unitario'), output_field=DecimalField()),
                    Decimal('0'),
                ),
            )
            .filter(unidades__gt=0)
            .order_by('-ingresos')
        )
        ventas_por_producto = [
            {
                'id': p['product__id'],
                'nombre': p['product__name'],
                'categoria': p['product__category__name'] or '',
                'unidades_vendidas': p['unidades'],
                'ingresos': float(p['ingresos']),
            }
            for p in ventas_por_producto_qs
        ]

        # ── Ventas POS del mes (legacy — mantiene compatibilidad) ─────────────
        monthly_agg = Payment.objects.filter(
            reservation__product__vendor=vendor,
            status='confirmed',
            confirmed_at__year=now.year,
            confirmed_at__month=now.month,
        ).aggregate(total=Sum('amount'))
        monthly_sales_live = float(monthly_agg['total'] or 0)

        return Response({
            'total_active_products': total_active_products,
            'monthly_sales': monthly_sales,
            'monthly_sales_live': monthly_sales_live,
            'pending_orders': pending_orders,
            'next_live': next_live,
            'periodo': periodo,
            'fecha_inicio': fecha_inicio.isoformat(),
            'fecha_fin': fecha_fin.isoformat(),
            'ventas_por_metodo_pago': ventas_por_metodo_pago,
            'utilidad_bruta': round(utilidad_bruta, 2),
            'utilidad_neta': round(utilidad_neta, 2),
            'costo_total_vendido': round(float(costo_total), 2),
            'gastos_operativos': round(float(gastos_total), 2),
            'ventas_por_producto': ventas_por_producto,
        })


class PublicPromocionesView(APIView):
    """GET /api/v1/vendors/public/{vendor_slug}/promociones/ — no auth required."""
    permission_classes = [AllowAny]

    def get(self, request, vendor_slug):
        vendor = get_object_or_404(Vendor, slug=vendor_slug)
        today = datetime.date.today()
        promos = Promocion.objects.filter(
            vendor=vendor,
            activa=True,
            fecha_inicio__lte=today,
        ).filter(
            django_models.Q(fecha_fin__isnull=True) | django_models.Q(fecha_fin__gte=today)
        ).order_by('orden', '-fecha_inicio')

        data = []
        for p in promos:
            imagen_url = None
            if p.imagen:
                imagen_url = request.build_absolute_uri(p.imagen.url)
            data.append({
                'id': p.id,
                'titulo': p.titulo,
                'descripcion': p.descripcion,
                'imagen': imagen_url,
            })
        return Response(data)


class MisPermisosView(APIView):
    """
    Devuelve el perfil completo de permisos del usuario autenticado.
    Fuente de verdad para el frontend al decidir qué módulos mostrar.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user

        # Superadmin → acceso total
        if user.is_staff or user.is_superuser:
            todos = {
                m: {'ver': True, 'operar': True}
                for m in ['pos', 'inventario', 'almacen', 'compras', 'reportes',
                          'livestream', 'productos', 'configuracion', 'pedidos', 'pagos']
            }
            return Response({
                'rol': 'superadmin',
                'vendor_id': None,
                'vendor_nombre': None,
                'estado_suscripcion': 'activo',
                'permisos': todos,
                'es_propietario': True,
                'max_usuarios': None,
                'usuarios_activos': None,
            })

        # Propietario del vendor
        if hasattr(user, 'vendor_profile'):
            vendor = user.vendor_profile
            todos = {
                m: {'ver': True, 'operar': True}
                for m in ['pos', 'inventario', 'almacen', 'compras', 'reportes',
                          'livestream', 'productos', 'configuracion', 'pedidos', 'pagos']
            }
            return Response({
                'rol': 'propietario',
                'vendor_id': vendor.id,
                'vendor_nombre': vendor.nombre_tienda,
                'estado_suscripcion': getattr(vendor, 'estado_suscripcion', 'activo'),
                'permisos': todos,
                'es_propietario': True,
                'max_usuarios': getattr(vendor, 'max_usuarios', 3),
                'usuarios_activos': vendor.team_members.filter(is_active=True).count(),
            })

        # TeamMember
        if hasattr(user, 'team_member_profile'):
            tm = user.team_member_profile
            vendor = tm.vendor
            role = tm.custom_role

            # Mapeo de perm_* a estructura {ver, operar}
            # 'operar' = mismo booleano que 'ver' (el sistema actual no diferencia)
            MODULO_MAP = {
                'pos':           getattr(role, 'perm_pos',           False) if role else False,
                'inventario':    getattr(role, 'perm_inventory',      False) if role else False,
                'almacen':       getattr(role, 'perm_warehouse',      False) if role else False,
                'compras':       getattr(role, 'perm_compras',        False) if role else False,
                'reportes':      getattr(role, 'perm_dashboard',      False) if role else False,
                'livestream':    getattr(role, 'perm_live_sessions',  False) if role else False,
                'productos':     getattr(role, 'perm_products',       False) if role else False,
                'configuracion': getattr(role, 'perm_team',           False) if role else False,
                'pedidos':       getattr(role, 'perm_orders',         False) if role else False,
                'pagos':         getattr(role, 'perm_payments',       False) if role else False,
            }

            permisos = {
                modulo: {'ver': tiene, 'operar': tiene}
                for modulo, tiene in MODULO_MAP.items()
            }

            return Response({
                'rol': 'miembro',
                'vendor_id': vendor.id,
                'vendor_nombre': vendor.nombre_tienda,
                'estado_suscripcion': getattr(vendor, 'estado_suscripcion', 'activo'),
                'permisos': permisos,
                'es_propietario': False,
                'max_usuarios': getattr(vendor, 'max_usuarios', 3),
                'usuarios_activos': vendor.team_members.filter(is_active=True).count(),
            })

        return Response({'error': 'Usuario sin vendor asignado'}, status=400)


class ConteoFisicoViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, IsVendorOrWarehouseTeamMember]
    serializer_class = ConteoFisicoSerializer
    http_method_names = ['get', 'post', 'head', 'options']

    def get_queryset(self):
        vendor = get_vendor_for_user(self.request.user)
        if not vendor:
            return ConteoFisico.objects.none()
        qs = ConteoFisico.objects.filter(
            vendor=vendor
        ).select_related('almacen', 'creado_por', 'aprobado_por').prefetch_related(
            'items__producto', 'items__variante'
        )
        estado = (self.request.query_params.get('estado') or '').strip()
        if estado:
            qs = qs.filter(estado=estado)
        return qs

    def create(self, request, *args, **kwargs):
        vendor = get_vendor_for_user(request.user)
        if not vendor:
            return Response(
                {'error': 'Sin vendor asignado'}, status=400)
        if not _user_has_warehouse_perm(request.user):
            return Response(
                {'error': 'Solo el propietario o un usuario con permiso de almacén '
                          'puede crear un conteo físico'},
                status=403,
            )

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        almacen = serializer.validated_data.get('almacen')
        if almacen.sucursal.vendor_id != vendor.id:
            return Response(
                {'error': 'El almacén no pertenece a su tienda'},
                status=400,
            )

        conteo = serializer.save(
            vendor=vendor,
            creado_por=request.user
        )
        return Response(
            ConteoFisicoSerializer(conteo).data,
            status=status.HTTP_201_CREATED)

    @action(methods=['post'], detail=True,
            url_path='agregar-item')
    def agregar_item(self, request, pk=None):
        """
        Agrega o actualiza un ítem del conteo.
        Body: { producto, variante(opt), stock_fisico, notas }
        Calcula stock_sistema desde Inventory automáticamente.
        """
        if not _user_has_inventory_perm(request.user):
            return Response(
                {'error': 'Solo el propietario o un usuario con permiso de inventario '
                          'puede registrar ítems de conteo'},
                status=403,
            )

        conteo = self.get_object()
        if conteo.estado not in ('abierto',):
            return Response(
                {'error': 'Solo se pueden agregar ítems '
                          'a conteos abiertos'},
                status=400)

        from products.models import Inventory, Product, ProductVariant

        producto_id = request.data.get('producto')
        variante_id = request.data.get('variante')
        try:
            stock_fisico = int(request.data.get('stock_fisico', 0))
        except (TypeError, ValueError):
            return Response(
                {'error': 'stock_fisico inválido'},
                status=400,
            )

        notas = request.data.get('notas', '')
        if producto_id is None:
            return Response({'error': 'Debe enviar producto'}, status=400)

        get_object_or_404(Product, pk=producto_id, vendor=conteo.vendor)

        vid = variante_id or None
        if vid:
            get_object_or_404(
                ProductVariant, pk=vid, product_id=producto_id,
            )

        try:
            inv = Inventory.objects.get(
                product_id=producto_id,
                almacen=conteo.almacen
            )
            stock_sistema = inv.quantity
        except Inventory.DoesNotExist:
            stock_sistema = 0

        item, created = ConteoFisicoItem.objects.update_or_create(
            conteo=conteo,
            producto_id=producto_id,
            variante_id=vid,
            defaults={
                'stock_sistema': stock_sistema,
                'stock_fisico': stock_fisico,
                'contado_por': request.user,
                'notas': notas,
            }
        )

        data = ConteoFisicoItemSerializer(item).data
        stat = status.HTTP_201_CREATED if created else status.HTTP_200_OK
        return Response(data, status=stat)

    @action(methods=['post'], detail=True,
            url_path='cerrar')
    def cerrar(self, request, pk=None):
        """Cierra el conteo — listo para revisión y aprobación."""
        if not (_user_has_inventory_perm(request.user)
                or _user_has_warehouse_perm(request.user)):
            return Response(
                {'error': 'Sin permiso para cerrar el conteo'},
                status=403,
            )

        conteo = self.get_object()
        if conteo.estado != 'abierto':
            return Response(
                {'error': 'Solo se pueden cerrar conteos abiertos'},
                status=400)
        if not conteo.items.exists():
            return Response(
                {'error': 'El conteo no tiene ítems registrados'},
                status=400)
        conteo.estado = 'cerrado'
        conteo.save(update_fields=['estado', 'updated_at'])
        conteo = self.get_queryset().get(pk=conteo.pk)
        return Response(ConteoFisicoSerializer(conteo).data)

    @action(methods=['post'], detail=True,
            url_path='aprobar')
    def aprobar(self, request, pk=None):
        """
        Aprueba el conteo y aplica los ajustes de stock.
        Solo propietario o usuario con perm_warehouse.
        Por cada ítem con diferencia != 0:
          - Actualiza Inventory.quantity = stock_fisico
          - Crea KardexMovimiento de ajuste
        """
        user = request.user
        if not _user_has_warehouse_perm(user):
            return Response(
                {'error': 'Solo el propietario o '
                          'usuario con permiso de almacén '
                          'puede aprobar conteos'},
                status=403)

        conteo = self.get_object()
        if conteo.estado != 'cerrado':
            return Response(
                {'error': 'Solo se pueden aprobar conteos cerrados'},
                status=400)

        from products.models import Inventory
        from vendors.models import KardexMovimiento

        items_a_ajustar = list(
            conteo.items.exclude(diferencia=0).select_related(
                'producto', 'variante'
            )
        )
        for item in items_a_ajustar:
            if item.stock_fisico < 0:
                return Response(
                    {'error':
                     f'Stock físico inválido para el producto {item.producto_id} '
                     f'(Inventory requiere cantidad ≥ 0).'},
                    status=400,
                )

        items_ajustados = 0
        with transaction.atomic():
            for item in items_a_ajustar:
                try:
                    inv = Inventory.objects.select_for_update().get(
                        product=item.producto,
                        almacen=conteo.almacen
                    )
                except Inventory.DoesNotExist:
                    inv = Inventory.objects.create(
                        product=item.producto,
                        almacen=conteo.almacen,
                        quantity=0,
                        reserved_quantity=0,
                        is_active=True,
                    )

                stock_anterior = inv.quantity
                inv.quantity = item.stock_fisico
                inv.save(update_fields=['quantity'])

                tipo = 'entrada' if item.diferencia > 0 else 'salida'
                KardexMovimiento.objects.create(
                    inventory=inv,
                    almacen=conteo.almacen,
                    variant=item.variante,
                    tipo=tipo,
                    motivo='ajuste_manual',
                    cantidad=abs(item.diferencia),
                    stock_anterior=stock_anterior,
                    stock_actual=item.stock_fisico,
                    documento_ref=f'CONTEO-{conteo.id}',
                    usuario=user,
                    notas=(
                        f'Ajuste por conteo físico aprobado. '
                        f'Contado: {item.stock_fisico}, '
                        f'Sistema: {item.stock_sistema}'
                    ),
                )
                items_ajustados += 1

            conteo.estado = 'aprobado'
            conteo.aprobado_por = user
            conteo.save(update_fields=[
                'estado', 'aprobado_por', 'updated_at'])

        conteo_ref = self.get_queryset().get(pk=conteo.pk)
        return Response({
            **ConteoFisicoSerializer(conteo_ref).data,
            'items_ajustados': items_ajustados,
        })

    @action(methods=['post'], detail=True,
            url_path='cancelar')
    def cancelar(self, request, pk=None):
        if not _user_has_warehouse_perm(request.user):
            return Response(
                {'error': 'Solo el propietario o un usuario con permiso de almacén '
                          'puede cancelar conteos'},
                status=403,
            )

        conteo = self.get_object()
        if conteo.estado == 'aprobado':
            return Response(
                {'error': 'No se puede cancelar un '
                          'conteo ya aprobado'},
                status=400)
        conteo.estado = 'cancelado'
        conteo.save(update_fields=['estado', 'updated_at'])
        conteo = self.get_queryset().get(pk=conteo.pk)
        return Response(ConteoFisicoSerializer(conteo).data)


class TransferenciaAlmacenViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated,
                          IsVendorOrWarehouseTeamMember]
    serializer_class = TransferenciaAlmacenSerializer
    http_method_names = ['get', 'post', 'head', 'options']

    def get_queryset(self):
        vendor = get_vendor_for_user(self.request.user)
        if not vendor:
            return TransferenciaAlmacen.objects.none()
        return TransferenciaAlmacen.objects.filter(
            vendor=vendor
        ).prefetch_related(
            'items__producto', 'items__variante'
        )

    def create(self, request, *args, **kwargs):
        vendor = get_vendor_for_user(request.user)
        if not vendor:
            return Response(
                {'error': 'Sin vendor asignado'},
                status=400)

        items_data = request.data.get('items', [])
        if not items_data:
            return Response(
                {'error': 'Debe incluir al menos un ítem'},
                status=400)

        origen_id = request.data.get('almacen_origen')
        destino_id = request.data.get('almacen_destino')
        if str(origen_id) == str(destino_id):
            return Response(
                {'error': 'Origen y destino no pueden '
                         'ser el mismo almacén'},
                status=400)

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        vals = serializer.validated_data
        origen_obj = vals['almacen_origen']
        destino_obj = vals['almacen_destino']
        if (origen_obj.sucursal.vendor_id != vendor.pk
                or destino_obj.sucursal.vendor_id != vendor.pk):
            return Response(
                {'error': 'Los almacenes deben pertenecer a su tienda'},
                status=400)

        with transaction.atomic():
            transferencia = serializer.save(
                vendor=vendor,
                creado_por=request.user
            )
            for item_data in items_data:
                item = dict(item_data)
                if 'producto' in item:
                    item['producto_id'] = item.pop('producto')
                if 'variante' in item:
                    item['variante_id'] = item.pop('variante')
                for campo in ['producto_nombre',
                              'variante_detalle', 'id']:
                    item.pop(campo, None)
                TransferenciaAlmacenItem.objects.create(
                    transferencia=transferencia, **item)

        refresh = TransferenciaAlmacen.objects.prefetch_related(
            'items__producto', 'items__variante'
        ).get(pk=transferencia.pk)
        return Response(
            TransferenciaAlmacenSerializer(refresh).data,
            status=status.HTTP_201_CREATED)

    @action(methods=['post'], detail=True,
            url_path='confirmar')
    def confirmar(self, request, pk=None):
        transferencia = self.get_object()

        if transferencia.estado != 'pendiente':
            return Response(
                {'error': 'Solo se pueden confirmar '
                         'transferencias pendientes'},
                status=400)

        from products.models import Inventory
        from vendors.models import KardexMovimiento

        items = list(
            transferencia.items.select_related(
                'producto', 'variante').all())

        for item in items:
            try:
                inv_chk = Inventory.objects.get(
                    product=item.producto,
                    almacen=transferencia.almacen_origen)
            except Inventory.DoesNotExist:
                return Response(
                    {'error': (
                        f'Sin inventario de '
                        f'"{item.producto.name}" '
                        f'en almacén origen'
                    )}, status=400)
            if inv_chk.quantity < item.cantidad:
                return Response(
                    {'error': (
                        f'Stock insuficiente de '
                        f'"{item.producto.name}". '
                        f'Disponible: {inv_chk.quantity}, '
                        f'requerido: {item.cantidad}'
                    )}, status=400)

        with transaction.atomic():
            for item in items:

                inv_origen = Inventory.objects.select_for_update().get(
                    product=item.producto,
                    almacen=transferencia.almacen_origen)

                if inv_origen.quantity < item.cantidad:
                    raise ValidationError({
                        'error': (
                            f'Stock insuficiente en origen para '
                            f'"{item.producto.name}" tras bloqueo '
                            f'(concurrencia)'
                        )})

                ant_origen = inv_origen.quantity
                inv_origen.quantity -= item.cantidad
                inv_origen.save(update_fields=['quantity'])

                KardexMovimiento.objects.create(
                    inventory=inv_origen,
                    almacen=transferencia.almacen_origen,
                    variant=item.variante,
                    tipo='salida',
                    motivo='transferencia',
                    cantidad=item.cantidad,
                    stock_anterior=ant_origen,
                    stock_actual=inv_origen.quantity,
                    documento_ref=f'TRANSF-{transferencia.id}',
                    usuario=request.user,
                    notas=(
                        f'Transferencia hacia '
                        f'{transferencia.almacen_destino.nombre}'
                    ),
                )

                inv_destino, _ = Inventory.objects.get_or_create(
                    product=item.producto,
                    almacen=transferencia.almacen_destino,
                    defaults={
                        'quantity': 0,
                        'reserved_quantity': 0,
                        'purchase_cost': inv_origen.purchase_cost,
                        'is_active': True,
                    }
                )
                ant_destino = inv_destino.quantity
                inv_destino.quantity += item.cantidad
                inv_destino.save(update_fields=['quantity'])

                KardexMovimiento.objects.create(
                    inventory=inv_destino,
                    almacen=transferencia.almacen_destino,
                    variant=item.variante,
                    tipo='entrada',
                    motivo='transferencia',
                    cantidad=item.cantidad,
                    stock_anterior=ant_destino,
                    stock_actual=inv_destino.quantity,
                    documento_ref=f'TRANSF-{transferencia.id}',
                    usuario=request.user,
                    notas=(
                        f'Transferencia desde '
                        f'{transferencia.almacen_origen.nombre}'
                    ),
                )

                if item.variante:
                    v = item.variante
                    v.stock_extra = max(
                        0, v.stock_extra - item.cantidad)
                    v.save(update_fields=['stock_extra'])

            transferencia.estado = 'completada'
            transferencia.completado_por = request.user
            transferencia.save(update_fields=[
                'estado', 'completado_por', 'updated_at'])

        refresh = TransferenciaAlmacen.objects.prefetch_related(
            'items__producto', 'items__variante'
        ).get(pk=transferencia.pk)
        return Response(
            TransferenciaAlmacenSerializer(refresh).data)

    @action(methods=['post'], detail=True,
            url_path='cancelar')
    def cancelar(self, request, pk=None):
        transferencia = self.get_object()
        if transferencia.estado == 'completada':
            return Response(
                {'error': 'No se puede cancelar una '
                         'transferencia completada'},
                status=400)
        transferencia.estado = 'cancelada'
        transferencia.save(
            update_fields=['estado', 'updated_at'])
        refresh = TransferenciaAlmacen.objects.prefetch_related(
            'items__producto', 'items__variante'
        ).get(pk=transferencia.pk)
        return Response(
            TransferenciaAlmacenSerializer(refresh).data)