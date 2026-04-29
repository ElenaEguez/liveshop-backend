from rest_framework.permissions import BasePermission


def get_vendor_for_user(user):
    """
    Returns the Vendor instance for a vendor owner or an active TeamMember.
    Returns None if the user is neither.
    """
    if hasattr(user, 'vendor_profile'):
        return user.vendor_profile
    try:
        tm = user.team_member_profile
        if tm.is_active:
            return tm.vendor
    except Exception:
        pass
    return None


def get_role_for_user(user):
    """
    Returns 'vendor_owner', 'admin', 'assistant', 'payments', or None.
    """
    if hasattr(user, 'vendor_profile'):
        return 'vendor_owner'
    try:
        return user.team_member_profile.role
    except Exception:
        return None


class IsVendorOwner(BasePermission):
    """
    Allows access only to users who own a VendorProfile.
    TeamMembers (even with 'admin' role) are denied — only the account owner passes.
    """

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        return hasattr(request.user, 'vendor_profile')


class IsVendorOrTeamMember(BasePermission):
    """
    Allows access to the vendor owner OR an active TeamMember of that vendor.
    Views using this permission must set self.get_vendor_from_request() or
    the vendor is resolved from the authenticated user's profile / team membership.
    """

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        # Vendor owner
        if hasattr(request.user, 'vendor_profile'):
            return True
        # Active team member
        try:
            return request.user.team_member_profile.is_active
        except Exception:
            return False

    def has_object_permission(self, request, view, obj):
        user = request.user
        # Determine which vendor owns this object
        vendor = getattr(obj, 'vendor', None)
        if vendor is None:
            # Reservation → session → vendor
            session = getattr(obj, 'session', None)
            if session:
                vendor = getattr(session, 'vendor', None)
        if vendor is None:
            # Payment → reservation → session → vendor
            reservation = getattr(obj, 'reservation', None)
            if reservation:
                session = getattr(reservation, 'session', None)
                if session:
                    vendor = getattr(session, 'vendor', None)
        if vendor is None:
            # TurnoCaja / Caja / Sucursal style objects
            caja = getattr(obj, 'caja', None)
            if caja and getattr(caja, 'sucursal', None):
                vendor = getattr(caja.sucursal, 'vendor', None)
        if vendor is None:
            sucursal = getattr(obj, 'sucursal', None)
            if sucursal:
                vendor = getattr(sucursal, 'vendor', None)
        if vendor is None:
            # MovimientoCaja → turno → caja → sucursal → vendor
            turno = getattr(obj, 'turno', None)
            if turno and getattr(turno, 'caja', None) and getattr(turno.caja, 'sucursal', None):
                vendor = getattr(turno.caja.sucursal, 'vendor', None)
        if vendor is None:
            return False
        # Owner
        if hasattr(user, 'vendor_profile') and user.vendor_profile == vendor:
            return True
        # Active team member of that vendor
        try:
            tm = user.team_member_profile
            return tm.is_active and tm.vendor == vendor
        except Exception:
            return False
