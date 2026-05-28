from django.db import models as django_models
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated, AllowAny
from django.shortcuts import get_object_or_404
from django.db import transaction
from django.contrib.auth import get_user_model
from django.db.models import Count, DecimalField, F, Q, Sum
from django.db.models.functions import Coalesce

import datetime
from collections import defaultdict
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
    get_vendor_for_user,
    IsVendorOrWarehouseTeamMember,
    get_role_for_user,
)
from .role_permissions import (
    GRANULAR_MODULE_KEYS,
    LEGACY_MODULE_ALIASES,
    MODULE_TO_ROLE_FIELD,
    build_permisos_modulos,
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


def _is_vendor_owner(user, vendor):
    """True si el usuario es el propietario (cuenta vendor) de la tienda."""
    if not vendor:
        return False
    return (
        hasattr(user, 'vendor_profile')
        and user.vendor_profile.pk == vendor.pk
    )


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



ROLE_PERMISSION_FIELDS = tuple(sorted(set(MODULE_TO_ROLE_FIELD.values()) | {'perm_manage_roles'}))


def _role_permissions_dict(role):
    if role is None:
        return {field: False for field in ROLE_PERMISSION_FIELDS}
    return {field: bool(getattr(role, field, False)) for field in ROLE_PERMISSION_FIELDS}


def _is_subset_role(candidate_role, max_allowed_role) -> bool:
    candidate = _role_permissions_dict(candidate_role)
    ceiling = _role_permissions_dict(max_allowed_role)
    return all((not candidate[field]) or ceiling[field] for field in ROLE_PERMISSION_FIELDS)


class _RoleManagementMixin:
    def _get_vendor(self):
        vendor = get_vendor_for_user(self.request.user)
        if not vendor:
            raise ValidationError("El usuario no tiene un vendor asignado.")
        return vendor

    def _is_owner(self, vendor):
        vp = getattr(self.request.user, 'vendor_profile', None)
        return bool(vp and vp.id == vendor.id)

    def _actor_role(self):
        role = get_role_for_user(self.request.user)
        if role and getattr(role, 'vendor_id', None) != self._get_vendor().id:
            return None
        return role

    def _can_manage_roles(self, vendor):
        if self.request.user.is_staff or self.request.user.is_superuser:
            return True
        if self._is_owner(vendor):
            return True
        role = self._actor_role()
        return bool(role and role.perm_manage_roles)

    def _require_role_management_access(self):
        vendor = self._get_vendor()
        if not self._can_manage_roles(vendor):
            raise PermissionDenied("No tienes permiso para administrar roles/equipo.")
        return vendor

    def _actor_ceiling_role(self, vendor):
        if self._is_owner(vendor) or self.request.user.is_staff or self.request.user.is_superuser:
            class _AllPerms:
                pass
            role = _AllPerms()
            for field in ROLE_PERMISSION_FIELDS:
                setattr(role, field, True)
            return role
        return self._actor_role()

    def _assert_role_within_ceiling(self, role_obj):
        vendor = self._require_role_management_access()
        ceiling = self._actor_ceiling_role(vendor)
        if not _is_subset_role(role_obj, ceiling):
            raise PermissionDenied("No puedes otorgar permisos por encima de tu nivel.")


class CustomRoleViewSet(_RoleManagementMixin, viewsets.ModelViewSet):
    """CRUD de roles personalizados: owner o admin delegado con techo estricto."""
    serializer_class = CustomRoleSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        vendor = self._require_role_management_access()
        return CustomRole.objects.filter(vendor=vendor)

    def perform_create(self, serializer):
        vendor = self._require_role_management_access()
        role = CustomRole(vendor=vendor, **serializer.validated_data)
        self._assert_role_within_ceiling(role)
        serializer.save(vendor=vendor)

    def perform_update(self, serializer):
        role = serializer.instance
        self._assert_role_within_ceiling(role)
        role_after = CustomRole(vendor=role.vendor, **{**_role_permissions_dict(role), **serializer.validated_data, 'name': serializer.validated_data.get('name', role.name)})
        self._assert_role_within_ceiling(role_after)
        serializer.save()

    def perform_destroy(self, instance):
        self._assert_role_within_ceiling(instance)
        instance.delete()


class TeamMemberViewSet(_RoleManagementMixin, viewsets.ModelViewSet):
    """
    CRUD de miembros de equipo para owner o admin delegado.
    Aplicando techo de permisos para asignación/edición de roles.
    """
    serializer_class = TeamMemberSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        vendor = self._require_role_management_access()
        return TeamMember.objects.filter(vendor=vendor).select_related('custom_role', 'user')

    def _assert_assignable_role(self, role):
        if role is None:
            return
        self._assert_role_within_ceiling(role)

    def _assert_manageable_member(self, member):
        # No puede administrar miembros con un rol superior al actor
        self._assert_assignable_role(member.custom_role)

    def create(self, request, *args, **kwargs):
        vendor = self._require_role_management_access()
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
            self._assert_assignable_role(custom_role)

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
            self._assert_manageable_member(existing)
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

    def partial_update(self, request, *args, **kwargs):
        member = self.get_object()
        self._assert_manageable_member(member)
        role_id = request.data.get('custom_role', None)
        if role_id not in (None, ''):
            role = get_object_or_404(CustomRole, id=role_id, vendor=member.vendor)
            self._assert_assignable_role(role)
        return super().partial_update(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        member = self.get_object()
        self._assert_manageable_member(member)
        role_id = request.data.get('custom_role', None)
        if role_id not in (None, ''):
            role = get_object_or_404(CustomRole, id=role_id, vendor=member.vendor)
            self._assert_assignable_role(role)
        return super().update(request, *args, **kwargs)

    def perform_destroy(self, instance):
        self._assert_manageable_member(instance)
        instance.delete()


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
            mod_keys = list(GRANULAR_MODULE_KEYS) + list(LEGACY_MODULE_ALIASES.keys()) + ['manage_roles']
            todos = {m: {'ver': True, 'operar': True} for m in mod_keys}
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
            mod_keys = list(GRANULAR_MODULE_KEYS) + list(LEGACY_MODULE_ALIASES.keys()) + ['manage_roles']
            todos = {m: {'ver': True, 'operar': True} for m in mod_keys}
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

            mod_bools = build_permisos_modulos(role)
            mod_bools['manage_roles'] = bool(role and role.perm_manage_roles)
            permisos = {
                modulo: {'ver': tiene, 'operar': tiene}
                for modulo, tiene in mod_bools.items()
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


class ConteoFisicoPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100


class ConteoFisicoViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, IsVendorOrWarehouseTeamMember]
    serializer_class = ConteoFisicoSerializer
    pagination_class = ConteoFisicoPagination
    http_method_names = ['get', 'post', 'patch', 'head', 'options']

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
            if ',' in estado:
                estados = [e.strip() for e in estado.split(',') if e.strip()]
                qs = qs.filter(estado__in=estados)
            else:
                qs = qs.filter(estado=estado)

        almacen_id = self.request.query_params.get('almacen_id')
        if almacen_id:
            try:
                qs = qs.filter(almacen_id=int(almacen_id))
            except (TypeError, ValueError):
                pass

        fecha_desde = (self.request.query_params.get('fecha_desde') or '').strip()
        if fecha_desde:
            try:
                qs = qs.filter(created_at__date__gte=datetime.date.fromisoformat(fecha_desde))
            except ValueError:
                pass

        fecha_hasta = (self.request.query_params.get('fecha_hasta') or '').strip()
        if fecha_hasta:
            try:
                qs = qs.filter(created_at__date__lte=datetime.date.fromisoformat(fecha_hasta))
            except ValueError:
                pass

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
        conteo = self.get_object()
        if conteo.estado != 'abierto':
            return Response(
                {'error': 'Solo se pueden agregar ítems a conteos abiertos.'},
                status=400,
            )
        if not _user_has_inventory_perm(request.user):
            return Response(
                {'error': 'Solo el propietario o un usuario con permiso de inventario '
                          'puede registrar ítems de conteo'},
                status=403,
            )

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

        from products.stock_service import (
            get_system_stock,
            product_has_variants,
            sum_variant_stock,
        )

        if vid:
            stock_sistema = get_system_stock(producto_id, conteo.almacen_id, vid)
        elif product_has_variants(producto_id):
            stock_sistema = sum_variant_stock(producto_id)
        else:
            try:
                inv = Inventory.objects.get(
                    product_id=producto_id,
                    almacen=conteo.almacen,
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
        item.diferencia = item.stock_fisico - item.stock_sistema
        item.save(update_fields=['diferencia'])

        data = ConteoFisicoItemSerializer(item).data
        stat = status.HTTP_201_CREATED if created else status.HTTP_200_OK
        return Response(data, status=stat)

    @action(
        methods=['patch'],
        detail=True,
        url_path=r'editar-item/(?P<item_id>[0-9]+)',
    )
    def editar_item(self, request, pk=None, item_id=None):
        """
        Corrige stock_fisico y notas de un ítem en conteo cerrado (antes de aprobar).
        """
        user = request.user
        vendor = get_vendor_for_user(user)
        if not (_is_vendor_owner(user, vendor) or _user_has_warehouse_perm(user)):
            return Response(
                {'error': 'Sin permiso para editar ítems del conteo.'},
                status=403,
            )

        conteo = self.get_object()
        if conteo.estado != 'cerrado':
            return Response(
                {'error': 'Solo se pueden editar ítems de conteos cerrados.'},
                status=400,
            )

        item = get_object_or_404(
            ConteoFisicoItem,
            pk=item_id,
            conteo=conteo,
        )

        update_fields = []

        if 'stock_fisico' in request.data:
            try:
                stock_fisico = int(request.data.get('stock_fisico'))
            except (TypeError, ValueError):
                return Response(
                    {'error': 'stock_fisico inválido'},
                    status=400,
                )
            item.stock_fisico = stock_fisico
            update_fields.append('stock_fisico')

        if 'notas' in request.data:
            item.notas = str(request.data.get('notas', '') or '')[:300]
            update_fields.append('notas')

        if not update_fields:
            return Response(
                {'error': 'Debe enviar stock_fisico y/o notas'},
                status=400,
            )

        item.diferencia = item.stock_fisico - item.stock_sistema
        update_fields.append('diferencia')
        item.save(update_fields=update_fields)

        return Response(ConteoFisicoItemSerializer(item).data)

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
        vendor = get_vendor_for_user(user)
        if not _is_vendor_owner(user, vendor):
            return Response(
                {'error': 'Solo el propietario puede aprobar conteos.'},
                status=403,
            )

        conteo = self.get_object()
        if conteo.estado != 'cerrado':
            return Response(
                {'error': 'Solo se pueden aprobar conteos cerrados'},
                status=400)

        from products.stock_service import (
            StockError,
            product_has_variants,
            set_stock_absolute,
        )

        items_a_ajustar = list(
            conteo.items.exclude(diferencia=0).select_related(
                'producto', 'variante',
            ),
        )
        for item in items_a_ajustar:
            if item.stock_fisico < 0:
                return Response(
                    {
                        'error': (
                            f'Stock físico inválido para el producto '
                            f'{item.producto_id}.'
                        ),
                    },
                    status=400,
                )
            if product_has_variants(item.producto_id) and not item.variante_id:
                return Response(
                    {
                        'error': (
                            f'"{item.producto.name}" tiene variantes: '
                            f'conteo por talla/color obligatorio.'
                        ),
                    },
                    status=400,
                )

        items_ajustados = 0
        try:
            with transaction.atomic():
                for item in items_a_ajustar:
                    set_stock_absolute(
                        product=item.producto,
                        almacen=conteo.almacen,
                        new_quantity=item.stock_fisico,
                        variant=item.variante,
                        usuario=user,
                        motivo='ajuste_manual',
                        documento_ref=f'CONTEO-{conteo.id}',
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
                    'estado', 'aprobado_por', 'updated_at',
                ])
        except StockError as exc:
            return Response({'error': str(exc)}, status=400)

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


class TransferenciaPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 50


class TransferenciaAlmacenViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated,
                          IsVendorOrWarehouseTeamMember]
    serializer_class = TransferenciaAlmacenSerializer
    pagination_class = TransferenciaPagination
    http_method_names = ['get', 'post', 'put', 'patch', 'head', 'options']

    def get_queryset(self):
        vendor = get_vendor_for_user(self.request.user)
        if not vendor:
            return TransferenciaAlmacen.objects.none()
        qs = TransferenciaAlmacen.objects.filter(
            vendor=vendor
        ).prefetch_related(
            'items__producto', 'items__variante'
        )
        estado = self.request.query_params.get('estado')
        if estado:
            qs = qs.filter(estado=estado)
        return qs.order_by('-created_at')

    def update(self, request, *args, **kwargs):
        transferencia = self.get_object()
        if transferencia.estado != 'pendiente':
            raise ValidationError(
                'Solo se pueden editar transferencias en estado pendiente.'
            )

        items_data = request.data.get('items')
        if items_data is None:
            return Response(
                {'error': 'Debe enviar la lista de ítems (items).'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not items_data:
            return Response(
                {'error': 'Debe incluir al menos un ítem'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from products.models import Product
        from products.stock_service import product_has_variants

        vendor = get_vendor_for_user(request.user)
        normalized_items = []
        for item_data in items_data:
            item = dict(item_data)
            if 'producto' in item:
                item['producto_id'] = item.pop('producto')
            if 'variante' in item:
                item['variante_id'] = item.pop('variante')
            for campo in ['producto_nombre', 'variante_detalle', 'id', 'stock_actual']:
                item.pop(campo, None)
            pid = item.get('producto_id')
            vid = item.get('variante_id')
            if pid and product_has_variants(pid) and not vid:
                prod = Product.objects.filter(pk=pid).first()
                nombre = prod.name if prod else pid
                return Response(
                    {
                        'error': (
                            f'"{nombre}" tiene variantes: '
                            f'indique talla/color en la transferencia.'
                        ),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            normalized_items.append(item)

        with transaction.atomic():
            if 'notas' in request.data:
                transferencia.notas = request.data.get('notas') or ''
                transferencia.save(update_fields=['notas', 'updated_at'])
            transferencia.items.all().delete()
            for item in normalized_items:
                TransferenciaAlmacenItem.objects.create(
                    transferencia=transferencia,
                    **item,
                )

        refresh = TransferenciaAlmacen.objects.prefetch_related(
            'items__producto', 'items__variante',
        ).get(pk=transferencia.pk)
        return Response(TransferenciaAlmacenSerializer(refresh).data)

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

        from products.models import Product
        from products.stock_service import product_has_variants

        normalized_items = []
        for item_data in items_data:
            item = dict(item_data)
            if 'producto' in item:
                item['producto_id'] = item.pop('producto')
            if 'variante' in item:
                item['variante_id'] = item.pop('variante')
            for campo in ['producto_nombre', 'variante_detalle', 'id']:
                item.pop(campo, None)
            pid = item.get('producto_id')
            vid = item.get('variante_id')
            if pid and product_has_variants(pid) and not vid:
                prod = Product.objects.filter(pk=pid).first()
                nombre = prod.name if prod else pid
                return Response(
                    {
                        'error': (
                            f'"{nombre}" tiene variantes: '
                            f'indique talla/color en la transferencia.'
                        ),
                    },
                    status=400,
                )
            normalized_items.append(item)

        with transaction.atomic():
            transferencia = serializer.save(
                vendor=vendor,
                creado_por=request.user
            )
            for item in normalized_items:
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
        from products.stock_service import product_has_variants

        items = list(
            transferencia.items.select_related(
                'producto', 'variante').all())

        for item in items:
            if product_has_variants(item.producto_id) and not item.variante_id:
                return Response(
                    {
                        'error': (
                            f'"{item.producto.name}" tiene variantes: '
                            f'indique talla/color en la transferencia.'
                        ),
                    },
                    status=400,
                )

        demanda_por_producto = defaultdict(int)
        for item in items:
            demanda_por_producto[item.producto_id] += int(item.cantidad)

        for producto_id, cantidad_total in demanda_por_producto.items():
            inv_qs = Inventory.objects.filter(
                product_id=producto_id,
                almacen=transferencia.almacen_origen,
                is_active=True,
            ).select_related('product').order_by('-quantity')
            inv_chk = inv_qs.first()
            if not inv_chk:
                nombre = next(
                    (i.producto.name for i in items if i.producto_id == producto_id),
                    producto_id,
                )
                return Response(
                    {'error': (
                        f'Sin inventario de "{nombre}" en almacén origen'
                    )},
                    status=400,
                )
            disponible = max(
                0,
                int(inv_chk.quantity or 0) - int(inv_chk.reserved_quantity or 0),
            )
            if disponible < cantidad_total:
                return Response(
                    {'error': (
                        f'Stock insuficiente para '
                        f'"{inv_chk.product.name}". '
                        f'Disponible: {disponible}, '
                        f'requerido en esta transferencia: {cantidad_total}.'
                    )},
                    status=400,
                )

        with transaction.atomic():
            for item in items:

                inv_origen = (
                    Inventory.objects.select_for_update()
                    .filter(
                        product=item.producto,
                        almacen=transferencia.almacen_origen,
                        is_active=True,
                    )
                    .order_by('-quantity')
                    .first()
                )
                if not inv_origen:
                    raise ValidationError({
                        'error': (
                            f'Sin inventario de "{item.producto.name}" '
                            f'en almacén origen'
                        ),
                    })

                disponible_origen = max(
                    0,
                    int(inv_origen.quantity or 0)
                    - int(inv_origen.reserved_quantity or 0),
                )
                if disponible_origen < item.cantidad:
                    raise ValidationError({
                        'error': (
                            f'Stock insuficiente en origen para '
                            f'"{item.producto.name}" tras bloqueo '
                            f'(disponible: {disponible_origen}, '
                            f'requerido: {item.cantidad})'
                        ),
                    })

                from products.stock_service import apply_stock_delta

                qty = int(item.cantidad)
                apply_stock_delta(
                    product=item.producto,
                    almacen=transferencia.almacen_origen,
                    delta=-qty,
                    variant=item.variante,
                    usuario=request.user,
                    motivo='transferencia',
                    documento_ref=f'TRANSF-{transferencia.id}',
                    notas=(
                        f'Transferencia hacia '
                        f'{transferencia.almacen_destino.nombre}'
                    ),
                    sync_variant_with_inventory=False,
                )
                apply_stock_delta(
                    product=item.producto,
                    almacen=transferencia.almacen_destino,
                    delta=qty,
                    variant=item.variante,
                    usuario=request.user,
                    motivo='transferencia',
                    documento_ref=f'TRANSF-{transferencia.id}',
                    notas=(
                        f'Transferencia desde '
                        f'{transferencia.almacen_origen.nombre}'
                    ),
                    sync_variant_with_inventory=False,
                    update_purchase_cost=inv_origen.purchase_cost,
                )

                # stock_extra no cambia: la transferencia mueve stock entre almacenes,
                # el total por variante en la tienda se mantiene.

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