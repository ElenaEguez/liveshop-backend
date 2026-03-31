from django.db import models as django_models
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated, AllowAny
from django.shortcuts import get_object_or_404
from django.contrib.auth import get_user_model
from django.db.models import Sum

import datetime
from .models import Vendor, TeamMember, CustomRole, Promocion
from .serializers import VendorSerializer, VendorProfileSerializer, TeamMemberSerializer, CustomRoleSerializer
from .permissions import IsVendorOwner

User = get_user_model()


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
        if vendor.team_members.filter(is_active=True).count() >= 3:
            raise ValidationError("Límite de 3 miembros de equipo alcanzado.")

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
        try:
            vendor = request.user.vendor_profile
        except Vendor.DoesNotExist:
            return Response({'error': 'Sin perfil de vendedor'}, status=404)

        # Productos activos
        total_active_products = vendor.products.filter(is_active=True).count()

        # Ventas del mes (pagos confirmados este mes)
        from django.utils import timezone
        from payments.models import Payment
        from orders.models import Reservation
        now = timezone.now()

        monthly_agg = Payment.objects.filter(
            reservation__product__vendor=vendor,
            status='confirmed',
            confirmed_at__year=now.year,
            confirmed_at__month=now.month
        ).aggregate(total=Sum('amount'))
        monthly_sales = float(monthly_agg['total'] or 0)

        # Pedidos pendientes (reservas sin pago confirmado)
        pending_orders = Reservation.objects.filter(
            product__vendor=vendor,
            status='pending'
        ).count()

        # Próximo live
        next_live = None
        upcoming = vendor.live_sessions.filter(
            status='scheduled',
            scheduled_at__gte=now
        ).order_by('scheduled_at').first()
        if upcoming:
            next_live = upcoming.scheduled_at.isoformat()

        return Response({
            'total_active_products': total_active_products,
            'monthly_sales': monthly_sales,
            'pending_orders': pending_orders,
            'next_live': next_live,
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