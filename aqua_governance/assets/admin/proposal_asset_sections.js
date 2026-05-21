(function () {
  function isAssetProposal(value) {
    return value === 'ADD_ASSET' || value === 'REMOVE_ASSET';
  }

  function syncAssetSections() {
    var proposalType = document.getElementById('id_proposal_type');
    if (!proposalType) {
      return;
    }

    var display = isAssetProposal(proposalType.value) ? '' : 'none';
    var sections = document.querySelectorAll('fieldset.asset-proposal-section');
    for (var i = 0; i < sections.length; i += 1) {
      sections[i].style.display = display;
    }
  }

  document.addEventListener('DOMContentLoaded', function () {
    var proposalType = document.getElementById('id_proposal_type');
    if (!proposalType) {
      return;
    }
    proposalType.addEventListener('change', syncAssetSections);
    syncAssetSections();
  });
}());
