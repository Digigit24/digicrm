from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    LeadViewSet, LeadStatusViewSet, LeadActivityViewSet, LeadOrderViewSet,
    LeadFieldConfigurationViewSet, debug_lead_count
)

router = DefaultRouter()
router.register(r'leads', LeadViewSet, basename='lead')
router.register(r'statuses', LeadStatusViewSet, basename='leadstatus')
router.register(r'activities', LeadActivityViewSet, basename='leadactivity')
router.register(r'orders', LeadOrderViewSet, basename='leadorder')
router.register(r'field-configurations', LeadFieldConfigurationViewSet, basename='leadfieldconfiguration')

urlpatterns = [
    # Temporary debug endpoint (no auth required) - REMOVE after diagnosis
    path('debug/', debug_lead_count, name='debug-lead-count'),
    path('', include(router.urls)),
]