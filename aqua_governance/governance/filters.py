from rest_framework.filters import BaseFilterBackend


class HideFilterBackend(BaseFilterBackend):
    def filter_queryset(self, request, queryset, view):
        hide_value = request.query_params.get('hide')

        if not hide_value or hide_value == 'false':
            return queryset.filter(hide=False)

        if hide_value == 'true':
            return queryset.filter(hide=True)

        if hide_value == 'all':
            return queryset

        return queryset.filter(hide=False)
