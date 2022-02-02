from django.urls import include, path

from rest_framework import routers

from aqua_governance.governance.views import LogVoteView, ProposalsView, ProposalCreateViewSet

api_router = routers.SimpleRouter()
api_router.register(r'proposals', ProposalsView, basename='proposals')
api_router.register(r'proposal', ProposalCreateViewSet, basename='proposal')
api_router.register(r'votes-for-proposal', LogVoteView, basename='log_votes')


urlpatterns = [
    path('', include(api_router.urls)),
]
