from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    LeadViewSet, LeadStatusViewSet, LeadActivityViewSet, LeadOrderViewSet,
    LeadCustomFieldViewSet, LeadFieldVisibilityViewSet
)

router = DefaultRouter()
router.register(r'leads', LeadViewSet, basename='lead')
router.register(r'statuses', LeadStatusViewSet, basename='leadstatus')
router.register(r'activities', LeadActivityViewSet, basename='leadactivity')
router.register(r'orders', LeadOrderViewSet, basename='leadorder')
router.register(r'custom-fields', LeadCustomFieldViewSet, basename='leadcustomfield')
router.register(r'field-visibility', LeadFieldVisibilityViewSet, basename='leadfieldvisibility')

urlpatterns = [
    path('', include(router.urls)),
]