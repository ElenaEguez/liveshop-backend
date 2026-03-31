from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from rest_framework_simplejwt.tokens import RefreshToken
from django.contrib.auth import get_user_model

from .serializers import RegisterSerializer, LoginSerializer, UserProfileSerializer

User = get_user_model()


def _get_tokens_for_user(user):
    """Generate JWT tokens with extra claims: vendor_id, role, is_vendor_owner, perms."""
    refresh = RefreshToken.for_user(user)

    # Vendor owner — has all permissions
    if hasattr(user, 'vendor_profile'):
        vp = user.vendor_profile
        refresh['vendor_id']      = vp.id
        refresh['store_name']     = vp.nombre_tienda
        refresh['role']           = 'vendor_owner'
        refresh['is_vendor_owner'] = True
        refresh['role_name']      = 'Propietario'
        refresh['perms'] = {
            'products': True, 'categories': True, 'inventory': True,
            'live_sessions': True, 'my_store': True,
            'orders': True, 'payments': True, 'team': True, 'dashboard': True,
            'pos': True, 'warehouse': True, 'expenses': True,
        }
    else:
        # Team member — permissions come from their custom role
        try:
            tm = user.team_member_profile
            refresh['vendor_id']      = tm.vendor_id
            refresh['store_name']     = tm.vendor.nombre_tienda
            refresh['is_vendor_owner'] = False
            cr = tm.custom_role
            if cr:
                refresh['role']      = str(cr.id)
                refresh['role_name'] = cr.name
                refresh['perms'] = {
                    'products':      cr.perm_products,
                    'categories':    cr.perm_categories,
                    'inventory':     cr.perm_inventory,
                    'live_sessions': cr.perm_live_sessions,
                    'my_store':      cr.perm_my_store,
                    'orders':        cr.perm_orders,
                    'payments':      cr.perm_payments,
                    'team':          cr.perm_team,
                    'dashboard':     cr.perm_dashboard,
                    'pos':           cr.perm_pos,
                    'warehouse':     cr.perm_warehouse,
                    'expenses':      cr.perm_expenses,
                }
            else:
                # No role assigned — minimal access
                refresh['role']      = None
                refresh['role_name'] = None
                refresh['perms'] = {
                    'products': False, 'categories': False, 'inventory': False,
                    'live_sessions': False, 'my_store': False,
                    'orders': True, 'payments': False, 'team': False, 'dashboard': False,
                    'pos': False, 'warehouse': False, 'expenses': False,
                }
        except Exception:
            refresh['vendor_id']      = None
            refresh['role']           = None
            refresh['role_name']      = None
            refresh['is_vendor_owner'] = False
            refresh['perms'] = {
                'products': False, 'categories': False, 'inventory': False,
                'live_sessions': False, 'my_store': False,
                'orders': False, 'payments': False, 'team': False, 'dashboard': False,
                'pos': False, 'warehouse': False, 'expenses': False,
            }

    return refresh


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
