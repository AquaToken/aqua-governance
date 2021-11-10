from django.forms.renderers import get_default_renderer
from django.forms.utils import flatatt
from django.utils.safestring import mark_safe

from django_quill.widgets import QuillWidget, json_encode


class CustomQuillWidget(QuillWidget):

    def render(self, name, value, attrs=None, renderer=None):
        if renderer is None:
            renderer = get_default_renderer()
        if value is None:
            value = ''

        attrs = attrs or {}
        attrs['name'] = name
        if hasattr(value, 'quill'):
            attrs['quill'] = value.quill
            html = value.quill.html
        else:
            attrs['value'] = value
            html = value.html if hasattr(value, 'html') else ''
        final_attrs = self.build_attrs(self.attrs, attrs)
        return mark_safe(renderer.render('admin/widgets/quill_text_widget.html', { # NOQA S703
            'final_attrs': flatatt(final_attrs),
            'id': final_attrs['id'],
            'name': final_attrs['name'],
            'config': json_encode(self.config),
            'quill': final_attrs.get('quill', None),
            'value': final_attrs.get('value', None),
            'html': html,
        }))
