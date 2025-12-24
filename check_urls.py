import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'digicrm.settings')
django.setup()

from rest_framework.routers import DefaultRouter
from crm.views import LeadViewSet

router = DefaultRouter()
router.register('leads', LeadViewSet, basename='lead')

print("Registered URL patterns:")
print("=" * 60)
for url in router.urls:
    print(f"{url.pattern}")
print("=" * 60)

# Check if export action is registered
actions = LeadViewSet.get_extra_actions()
print(f"\nExtra actions in LeadViewSet: {len(actions)}")
for action in actions:
    print(f"  - {action.__name__}: {action.mapping}")
