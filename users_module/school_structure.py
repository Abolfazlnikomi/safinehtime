GRADE_TITLES = ('پنجم', 'ششم', 'هفتم', 'هشتم', 'نهم', 'دهم', 'یازدهم', 'دوازدهم')
GENERAL_GRADES = frozenset(GRADE_TITLES[:5])
ACADEMIC_FIELDS = ('ریاضی', 'تجربی', 'انسانی')
DIGITS = str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789')


def grade_title(value):
    title = str(value or '').strip().translate(DIGITS)
    title = title.removeprefix('پایه').strip()
    if title.isdecimal() and 5 <= int(title) <= 12:
        return GRADE_TITLES[int(title) - 5]
    return title


def is_general_grade(value):
    return grade_title(value if isinstance(value, str) else getattr(value, 'title', value)) in GENERAL_GRADES
