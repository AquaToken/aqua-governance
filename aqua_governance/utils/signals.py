from django.db.models.signals import (
    post_delete,
    post_init,
    post_migrate,
    post_save,
    pre_delete,
    pre_init,
    pre_migrate,
    pre_save,
)
from django.utils.module_loading import import_string


class DisableSignals:
    def __init__(self, *receivers, sender=None, signals=None):
        self.sender = sender
        self.signals = signals or [
            pre_init, post_init,
            pre_save, post_save,
            pre_delete, post_delete,
            pre_migrate, post_migrate,
        ]
        self.receivers_func = [import_string(r) for r in receivers]

    def __enter__(self):
        self.disabled_signals = {
            func: [
                s for s in self.signals
                if s.disconnect(func, sender=self.sender)
            ]
            for func in self.receivers_func
        }

    def __exit__(self, exc_type, exc_val, exc_tb):
        for func, signals in self.disabled_signals.items():
            for signal in signals:
                signal.connect(func, sender=self.sender)
