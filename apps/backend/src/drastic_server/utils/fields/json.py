import json

from wtforms import fields

# Credits: https://gist.github.com/dukebody/dcc371bf286534d546e9

class JSONField(fields.StringField):
    def _value(self):
        return json.dumps(self.data) if self.data else ''

    def process_formdata(self, valuelist):
        if valuelist:
            try:
                self.data = json.loads(valuelist[0])
            except ValueError as err:
                raise ValueError('This field contains invalid JSON') from err
        else:
            self.data = None

    def pre_validate(self, form):
        super().pre_validate(form)
        if self.data:
            try:
                json.dumps(self.data)
            except TypeError as err:
                raise ValueError('This field contains invalid JSON') from err
