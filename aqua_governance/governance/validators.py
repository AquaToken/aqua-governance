from rest_framework.exceptions import ValidationError


class DiscordUsernameValidator(object):
    message = 'Enter a valid discord username.'
    code = 'invalid'

    def __init__(self, message=None, code=None):
        if message is not None:
            self.message = message
        if code is not None:
            self.code = code

    def __call__(self, value):
        if '#' not in value:
            raise ValidationError(self.message, self.code)
        values = value.split('#')
        if not len(values[1]) == 4:
            raise ValidationError(self.message, self.code)


