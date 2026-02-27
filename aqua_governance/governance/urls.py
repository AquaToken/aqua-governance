from django.urls import include, path

from rest_framework import routers

from aqua_governance.governance.views import AssetRegistryView, LogVoteView, ProposalsView, ProposalViewSet, \
    TestProposalViewSet

api_router = routers.SimpleRouter()
api_router.register(r'proposals', ProposalsView, basename='proposals')  # TODO: remove it
api_router.register(r'proposal', ProposalViewSet, basename='proposal')
api_router.register(r'test/proposal', TestProposalViewSet, basename='proposaltest')  # todo: remove
api_router.register(r'votes-for-proposal', LogVoteView, basename='log_votes')
api_router.register(r'asset-registry', AssetRegistryView, basename='asset_registry')


urlpatterns = [
    path('', include(api_router.urls)),
]
