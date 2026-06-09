from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    LeadViewSet, LeadStatusViewSet, LeadActivityViewSet, LeadOrderViewSet,
    LeadFieldConfigurationViewSet, LeadGroupViewSet
)

router = DefaultRouter()
router.register(r'leads', LeadViewSet, basename='lead')
router.register(r'statuses', LeadStatusViewSet, basename='leadstatus')
router.register(r'activities', LeadActivityViewSet, basename='leadactivity')
router.register(r'orders', LeadOrderViewSet, basename='leadorder')
router.register(r'field-configurations', LeadFieldConfigurationViewSet, basename='leadfieldconfiguration')
router.register(r'lead-groups', LeadGroupViewSet, basename='leadgroup')

urlpatterns = [
    path('', include(router.urls)),
]