"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include

from apps.core.views import healthz, healthz_ai, healthz_worker

urlpatterns = [
    # Database-aware health probe (used by Render/container orchestrators)
    path('healthz', healthz, name='healthz'),
    # Celery worker liveness probe (Phase 5)
    path('healthz/worker/', healthz_worker, name='healthz-worker'),
    # AI foundation health probe (Phase 7a)
    path('healthz/ai/', healthz_ai, name='healthz-ai'),
    path('admin/', admin.site.urls),
    # DRF browsable API session login/logout (moved off /api/auth to avoid
    # clashing with the JWT auth endpoints below)
    path('api/browsable-auth/', include('rest_framework.urls')),
    # Authentication & identity — JWT login/refresh/logout, /api/me
    path('api/', include('apps.accounts.urls')),
    # Ingestion API — upload, review, approve
    path('api/', include('apps.ingestion.urls')),
    # Carbon engine — activity types, factor datasets/factors, calculations
    path('api/', include('apps.carbon.urls')),
    # Governance / audit — hash-chain verification (Phase 6a)
    path('api/', include('apps.audit.urls')),
]
