"""
URL configuration for the real_estate app.

  GET/POST     /api/real-estate/projects/                  Project list/create
  GET/PATCH/DELETE /api/real-estate/projects/<id>/          Project detail
  GET          /api/real-estate/projects/<id>/summary/      Unit counts by status/unit_type/floor_number
  GET/POST     /api/real-estate/blocks/                     Block list/create
  GET/PATCH/DELETE /api/real-estate/blocks/<id>/            Block detail
  GET/POST     /api/real-estate/units/                      Unit list/create
  GET/PATCH/DELETE /api/real-estate/units/<id>/             Unit detail
  GET/POST     /api/real-estate/project-interests/          ProjectInterest list/create
  GET/PATCH/DELETE /api/real-estate/project-interests/<id>/ ProjectInterest detail
  GET/POST     /api/real-estate/unit-leads/                 UnitLead list/create
  GET/PATCH/DELETE /api/real-estate/unit-leads/<id>/        UnitLead detail
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from real_estate import views

router = DefaultRouter()
router.register(r'projects', views.ProjectViewSet, basename='real-estate-project')
router.register(r'blocks', views.BlockViewSet, basename='real-estate-block')
router.register(r'units', views.UnitViewSet, basename='real-estate-unit')
router.register(r'project-interests', views.ProjectInterestViewSet, basename='real-estate-project-interest')
router.register(r'unit-leads', views.UnitLeadViewSet, basename='real-estate-unit-lead')

app_name = 'real_estate'

urlpatterns = [
    path('', include(router.urls)),
]
