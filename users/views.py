from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.exceptions import TokenError, InvalidToken
from django.contrib.auth import get_user_model

from .serializers import RegisterSerializer, LoginSerializer, UserProfileSerializer

User = get_user_model()

CUSTOM_CLAIMS = [
    'vendor_id', 'store_name', 'role', 'is_vendor_owner',
    'role_name', 'perms',
]


class CustomTokenRefreshView(TokenRefreshView):
    """
    Extiende TokenRefreshView para preservar los claims custom
    (vendor_id, perms, etc.) en el nuevo access token.
    Sin esto, el access token renovado llega vacio de permisos.
    """
    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
        except TokenError as e:
            raise InvalidToken(e.args[0])

        try:
            refresh = RefreshToken(request.data.get('refresh', ''))
        except TokenError:
            return Response(serializer.validated_data)

        new_access_token = serializer.validated_data.get('access')
        if not new_access_token:
            return Response(serializer.validated_data)

        try:
            from rest_framework_simplejwt.tokens import AccessToken
            access_obj = AccessToken(new_access_token)
            for claim in CUSTOM_CLAIMS:
                if claim in refresh.payload:
                    access_obj[claim] = refresh.payload[claim]
            serializer.validated_data['access'] = str(access_obj)
        except Exception:
            pass

        return Response(serializer.validated_data)


def _get_tokens_for_user(user):
    """Generate JWT tokens with extra claims: vendor_id, role, is_vendor_owner, perms."""
    token = RefreshToken.for_user(user)

    # Vendor owner — has all permissions
    if hasattr(user, 'vendor_profile'):
        vp = user.vendor_profile
        claims = {
            'vendor_id': vp.id,
            'store_name': vp.nombre_tienda,
            'role': 'vendor_owner',
            'is_vendor_owner': True,
            'role_name': 'Propietario',
            'perms': {
                'products': True, 'categories': True, 'inventory': True,
                'live_sessions': True, 'my_store': True,
                'orders': True, 'payments': True, 'team': True, 'dashboard': True,
                'manage_roles': True,
                'pos': True, 'warehouse': True, 'expenses': True,
                'compras': True, 'proveedores': True,
                'pedidos': True, 'pagos': True,
                'arqueos': True, 'ventas_pos': True, 'devoluciones': True,
                'conteos': True, 'conteos_control': True,
                'transferencias': True, 'almacen': True,
                'configuracion': True, 'ecommerce_orders': True,
                'livestream': True,
            },
        }
    else:
        # Team member — permissions come from their custom role
        try:
            tm = user.team_member_profile
            base = {
                'vendor_id': tm.vendor_id,
                'store_name': tm.vendor.nombre_tienda,
                'is_vendor_owner': False,
            }
            cr = tm.custom_role
            if cr:
                from vendors.role_permissions import build_jwt_perms_dict
                claims = {
                    **base,
                    'role': str(cr.id),
                    'role_name': cr.name,
                    'perms': build_jwt_perms_dict(cr),
                }
            else:
                # No role assigned — minimal access
                claims = {
                    **base,
                    'role': None,
                    'role_name': None,
                    'perms': {
                        'products': False, 'categories': False, 'inventory': False,
                        'live_sessions': False, 'my_store': False,
                        'orders': True, 'payments': False, 'team': False, 'dashboard': False,
                        'manage_roles': False,
                        'pos': False, 'warehouse': False, 'expenses': False,
                        'compras': False,
                        'pedidos': True,
                        'pagos': False,
                    },
                }
        except Exception:
            claims = {
                'vendor_id': None,
                'store_name': None,
                'role': None,
                'role_name': None,
                'is_vendor_owner': False,
                'perms': {
                    'products': False, 'categories': False, 'inventory': False,
                    'live_sessions': False, 'my_store': False,
                    'orders': False, 'payments': False, 'team': False, 'dashboard': False,
                    'manage_roles': False,
                    'pos': False, 'warehouse': False, 'expenses': False,
                    'compras': False,
                    'pedidos': False,
                    'pagos': False,
                },
            }

    for key, value in claims.items():
        token[key] = value
        token.access_token[key] = value

    return token


class RegisterView(APIView):
    """View for user registration"""
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = RegisterSerializer(data=request.data)
        if serializer.is_valid():
            user = serializer.save()

            # Generate tokens
            refresh = _get_tokens_for_user(user)
            
            return Response({
                'user': UserProfileSerializer(user).data,
                'tokens': {
                    'refresh': str(refresh),
                    'access': str(refresh.access_token),
                }
            }, status=status.HTTP_201_CREATED)
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class LoginView(APIView):
    """View for user login"""
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = LoginSerializer(data=request.data)
        if serializer.is_valid():
            user = serializer.validated_data['user']

            # Generate tokens
            refresh = _get_tokens_for_user(user)
            
            return Response({
                'user': UserProfileSerializer(user).data,
                'tokens': {
                    'refresh': str(refresh),
                    'access': str(refresh.access_token),
                }
            }, status=status.HTTP_200_OK)
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class RefreshView(TokenRefreshView):
    """View for refreshing JWT tokens"""
    permission_classes = [AllowAny]


class MeView(APIView):
    """View for getting current authenticated user information"""
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        serializer = UserProfileSerializer(request.user)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def put(self, request, *args, **kwargs):
        serializer = UserProfileSerializer(
            request.user, 
            data=request.data, 
            partial=True
        )
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
