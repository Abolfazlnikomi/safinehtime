import re
from datetime import date, datetime

import jdatetime
from django import forms


class ShamsiDateInput(forms.TextInput):
    template_name = 'student_records/widgets/shamsi_date.html'


class ShamsiDateField(forms.DateField):
    widget = ShamsiDateInput
    default_error_messages = {
        'invalid': 'یک تاریخ شمسی معتبر مانند ۱۴۰۵/۰۷/۲۸ وارد کنید.',
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.widget.attrs.update({
            'dir': 'ltr',
            'placeholder': '۱۴۰۵/۰۷/۲۸',
            'maxlength': 10,
            'autocomplete': 'off',
        })

    def to_python(self, value):
        if value in self.empty_values:
            return None
        if isinstance(value, (date, datetime)):
            return super().to_python(value)
        normalized = str(value).strip().translate(str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789'))
        if re.fullmatch(r'[0-9]{4}([/-])[0-9]{1,2}\1[0-9]{1,2}', normalized):
            try:
                year, month, day = map(int, re.split(r'[/-]', normalized))
                return jdatetime.date(year, month, day).togregorian()
            except (ValueError, OverflowError):
                pass
        raise forms.ValidationError(self.error_messages['invalid'], code='invalid')

    def prepare_value(self, value):
        if isinstance(value, datetime):
            value = value.date()
        if isinstance(value, date):
            return jdatetime.date.fromgregorian(date=value).strftime('%Y/%m/%d').translate(str.maketrans('0123456789', '۰۱۲۳۴۵۶۷۸۹'))
        return value
