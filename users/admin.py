from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """Custom User Admin"""
    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('Información Personal', {'fields': ('nombre', 'apellido', 'telefono', 'ciudad', 'foto_perfil')}),
        ('Permisos', {'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
        ('Información Importante', {'fields': ('rol', 'date_joined', 'last_login')}),
    )
    
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('email', 'password1', 'password2'),
        }),
    )
    
    readonly_fields = ('date_joined', 'last_login')
    
    list_display = ('email', 'nombre', 'apellido', 'rol', 'is_active', 'is_staff', 'date_joined')
    list_filter = ('is_active', 'is_staff', 'rol', 'ciudad', 'date_joined')
    search_fields = ('email', 'nombre', 'apellido')
    ordering = ('-date_joined',)
