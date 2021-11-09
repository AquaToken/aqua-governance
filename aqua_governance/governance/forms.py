from django import forms

from aqua_governance.utils.widgets import CustomQuillWidget


class ProposalAdminForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['text'].widget = CustomQuillWidget()

    class Meta:
        fields = forms.ALL_FIELDS
