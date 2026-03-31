from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin, BaseUserManager
from django.db import models


class UserManager(BaseUserManager):
    """Custom user manager for email-based authentication"""

    def create_user(self, email, password=None, **extra_fields):
        """Create and save a regular user"""
        if not email:
            raise ValueError('El email es obligatorio')
        
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        """Create and save a superuser"""
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        
        if extra_fields.get('is_staff') is not True:
            raise ValueError('El superusuario debe tener is_staff=True')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('El superusuario debe tener is_superuser=True')
        
        return self.create_user(email, password, **extra_fields)


BOLIVIAN_CITIES = (
    ('santa_cruz', 'Santa Cruz'),
    ('la_paz', 'La Paz'),
    ('cochabamba', 'Cochabamba'),
    ('sucre', 'Sucre'),
    ('oruro', 'Oruro'),
    ('potosi', 'Potosí'),
    ('tarija', 'Tarija'),
    ('trinidad', 'Trinidad'),
    ('cobija', 'Cobija'),
)

USER_ROLES = (
    ('vendedor', 'Vendedor'),
    ('admin', 'Administrador'),
)


class User(AbstractBaseUser, PermissionsMixin):
    """Custom User model for authentication with email as primary login"""
    
    email = models.EmailField(unique=True)
    nombre = models.CharField(max_length=100)
    apellido = models.CharField(max_length=100)
    telefono = models.CharField(max_length=20, blank=True, null=True)
    ciudad = models.CharField(max_length=20, choices=BOLIVIAN_CITIES, blank=True, null=True)
    foto_perfil = models.ImageField(upload_to='profile_images/', blank=True, null=True)
    rol = models.CharField(max_length=20, choices=USER_ROLES, default='vendedor')
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(auto_now_add=True)
    
    objects = UserManager()
    
    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['nombre', 'apellido']
    
    class Meta:
        verbose_name = 'Usuario'
        verbose_name_plural = 'Usuarios'
        ordering = ['-date_joined']
    
    def __str__(self):
        return f'{self.nombre} {self.apellido} ({self.email})'
    
    def get_full_name(self):
        return f'{self.nombre} {self.apellido}'
