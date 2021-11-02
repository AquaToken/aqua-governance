from django.urls import include, path

from rest_framework import routers

from aqua_governance.governance.views import ProposalsView


api_router = routers.SimpleRouter()
api_router.register(r'proposals', ProposalsView, basename='proposals')

urlpatterns = [
    path('', include(api_router.urls)),
]
