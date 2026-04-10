from django.db import models
from django.utils.text import slugify
from django.core.exceptions import ValidationError
import uuid
from vendors.models import Vendor

class LiveSession(models.Model):
    PLATFORM_CHOICES = [
        ('tiktok', 'TikTok'),
        ('facebook', 'Facebook'),
        ('instagram', 'Instagram'),
    ]

    STATUS_CHOICES = [
        ('scheduled', 'Programada'),
        ('live', 'En vivo'),
        ('ended', 'Finalizada'),
    ]

    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name='live_sessions')
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True, null=True)
    platform = models.CharField(max_length=20, choices=PLATFORM_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='scheduled')
    scheduled_at = models.DateTimeField()
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    stream_url = models.URLField(blank=True, null=True)
    payment_qr_image = models.ImageField(upload_to='livestreams/qr/', blank=True, null=True)
    payment_instructions = models.TextField(blank=True, null=True)
    slug = models.SlugField(max_length=255, unique=True, blank=True)
    slot = models.PositiveSmallIntegerField(
        default=1,
        help_text="Canal de transmisión simultánea (1, 2, 3...)"
    )
    allow_multiple_cart = models.BooleanField(
        default=False,
        verbose_name="Permitir carrito multi-producto",
        help_text="Si está activo, los clientes pueden agregar varios productos al carrito. Si está inactivo, compran de a un producto a la vez."
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Sesión en Vivo'
        verbose_name_plural = 'Sesiones en Vivo'
        ordering = ['-scheduled_at']

    def __str__(self):
        return f'{self.title} - {self.vendor.nombre_tienda}'

    def clean(self):
        # Prevent two simultaneous live sessions on the same slot for the same vendor
        if self.status == 'live':
            qs = LiveSession.objects.filter(
                vendor=self.vendor,
                slot=self.slot,
                status='live'
            )
            if self.pk:
                qs = qs.exclude(pk=self.pk)
            if qs.exists():
                raise ValidationError(
                    f"Ya existe una sesión en vivo activa en el slot {self.slot} para este vendedor. "
                    "Finaliza la sesión anterior antes de iniciar una nueva."
                )

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.title)
            self.slug = f"{base_slug}-{str(uuid.uuid4())[:8]}"
        super().save(*args, **kwargs)

    @property
    def is_live(self):
        return self.status == 'live'
