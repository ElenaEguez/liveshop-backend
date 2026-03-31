"""
ASGI config for config project.
"""

import os

# Must be set before any Django/app imports
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

from django.core.asgi import get_asgi_application

# Initialize Django fully before importing any app modules that use ORM models
django_asgi_app = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter
from livestreams import routing as livestream_routing

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    "websocket": URLRouter(livestream_routing.websocket_urlpatterns),
})
