import io
import re
import zipfile
from xml.etree import ElementTree

from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count
from django.http import HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from auth_module.user_management import _sheet_xml, clean_text
from users_module.curriculum_access import grades_for_user, subjects_for_user
from users_module.models import FieldOfStudy, Grade, School, Subject
from users_module.school_structure import ACADEMIC_FIELDS, GRADE_TITLES, grade_title, is_general_grade
from .models import Chapter, Lesson, Topic


HEADERS = {
    'پایه': 'grade', 'رشته': 'field', 'کتاب': 'book', 'فصل': 'chapter',
    'درس': 'lesson', 'مبحث': 'topic',
}
REQUIRED = {'books': ('grade', 'book', 'chapter'), 'topics': ('chapter', 'lesson', 'topic')}
LABELS = {value: key for key, value in HEADERS.items()}


def schools_for_user(user):
    schools = School.objects.select_related('province').order_by('province__name', 'name')
    if user.is_superuser:
        return schools
    if user.role == 'PROVINCE_TRUSTEE':
        return schools.filter(province__trustees__user=user)
    return schools.filter(grades__in=grades_for_user(user)).distinct()


class StructureUploadForm(forms.Form):
    school = forms.ChoiceField(label='مدرسه / شهر مقصد')
    books = forms.FileField(label='اکسل اول · پایه، رشته، کتاب، فصل', required=False)
    topics = forms.FileField(label='اکسل دوم · فصل، درس، مبحث', required=False)

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.schools = schools_for_user(user)
        self.fields['school'].choices = [('', '---------'), ('all', 'همه موارد')] + [
            (str(school.pk), f'{school.name} · {school.province}') for school in self.schools
        ]
        for field in self.fields.values():
            field.widget.attrs['class'] = 'structure-input'
        for name in ('books', 'topics'):
            self.fields[name].widget.attrs['accept'] = '.xlsx'

    def clean_school(self):
        value = self.cleaned_data['school']
        return None if value == 'all' else self.schools.get(pk=value)

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get('books') and not cleaned.get('topics'):
            raise forms.ValidationError('حداقل یکی از دو فایل اکسل را انتخاب کنید.')
        for name in ('books', 'topics'):
            upload = cleaned.get(name)
            if upload and (not upload.name.lower().endswith('.xlsx') or upload.size > 5 * 1024 * 1024):
                self.add_error(name, 'فقط فایل xlsx با حجم حداکثر ۵ مگابایت مجاز است.')
        return cleaned


def read_rows(upload, kind):
    if not upload.name.lower().endswith('.xlsx') or upload.size > 5 * 1024 * 1024:
        raise ValueError('فقط فایل xlsx با حجم حداکثر ۵ مگابایت مجاز است.')
    namespace = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    try:
        with zipfile.ZipFile(upload) as archive:
            if len(archive.infolist()) > 200 or sum(item.file_size for item in archive.infolist()) > 25 * 1024 * 1024:
                raise ValueError('حجم بازشده فایل بیش از حد مجاز است.')

            def xml(path):
                content = archive.read(path)
                if b'<!DOCTYPE' in content.upper() or b'<!ENTITY' in content.upper():
                    raise ValueError('ساختار XML فایل مجاز نیست.')
                return ElementTree.fromstring(content)

            shared = []
            if 'xl/sharedStrings.xml' in archive.namelist():
                shared = [''.join(item.itertext()) for item in xml('xl/sharedStrings.xml')]
            workbook = xml('xl/workbook.xml')
            sheet = workbook.find('s:sheets/s:sheet', namespace)
            relation = sheet.attrib['{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id']
            target = next(item.attrib['Target'] for item in xml('xl/_rels/workbook.xml.rels') if item.attrib['Id'] == relation)
            path = target.lstrip('/') if target.startswith('/') else 'xl/' + target
            root = xml(path)
            rows = []
            for number, row in enumerate(root.findall('s:sheetData/s:row', namespace), 1):
                if number > 10001:
                    raise ValueError('هر فایل حداکثر ۱۰۰۰۰ ردیف داده می‌تواند داشته باشد.')
                values = {}
                for cell in row.findall('s:c', namespace):
                    reference = cell.attrib.get('r', '')
                    match = re.fullmatch(r'([A-Z]{1,2})[0-9]+', reference)
                    if not match:
                        raise ValueError('نشانی ستون نامعتبر است؛ از فایل نمونه استفاده کنید.')
                    column = 0
                    for letter in match.group(1):
                        column = column * 26 + ord(letter) - 64
                    if column > 20:
                        raise ValueError('تعداد ستون‌های فایل بیش از حد مجاز است.')
                    if cell.find('s:f', namespace) is not None:
                        raise ValueError(f'ردیف {number}: فرمول مجاز نیست؛ مقدار ثابت وارد کنید.')
                    value = cell.findtext('s:v', '', namespace)
                    if cell.attrib.get('t') == 's':
                        value = shared[int(value)]
                    elif cell.attrib.get('t') == 'inlineStr':
                        value = ''.join(cell.find('s:is', namespace).itertext())
                    values[column - 1] = clean_text(value)
                rows.append((int(row.attrib.get('r', number)), values))
    except (zipfile.BadZipFile, ElementTree.ParseError, KeyError, IndexError, TypeError, AttributeError, StopIteration, RuntimeError) as error:
        raise ValueError('فایل Excel معتبر نیست یا رمزگذاری شده است؛ از نمونه استفاده کنید.') from error
    if not rows:
        raise ValueError('فایل خالی است.')
    headers = {column: HEADERS.get(value, value.lower()) for column, value in rows[0][1].items()}
    missing = set(REQUIRED[kind]) - set(headers.values())
    if missing or len(headers.values()) != len(set(headers.values())):
        raise ValueError('عنوان ستون‌ها ناقص یا تکراری است. ستون‌های لازم: ' + '، '.join(LABELS[name] for name in REQUIRED[kind]))
    result = [(number, {headers[column]: value for column, value in values.items() if column in headers})
              for number, values in rows[1:] if any(values.values())]
    if not result:
        raise ValueError('فایل هیچ ردیف داده‌ای ندارد.')
    return result


def get_named(model, **lookup):
    matches = model.objects.filter(**lookup)
    if matches.count() > 1:
        raise ValueError('اطلاعات تکراری در ساختار موجود است؛ ابتدا رکوردهای هم‌نام را اصلاح کنید.')
    instance = matches.first()
    if instance:
        if hasattr(instance, 'is_active') and not instance.is_active:
            raise ValueError('این مورد قبلاً غیرفعال شده است؛ ابتدا آن را فعال کنید.')
        return instance, False
    instance = model(**lookup)
    instance.full_clean()
    instance.save()
    return instance, True


def import_structure(user, school, books=None, topics=None):
    if not user.is_active or not (user.is_superuser or user.role in {'ADMIN', 'PROVINCE_TRUSTEE'}):
        raise PermissionDenied
    schools = schools_for_user(user)
    if school is not None:
        schools = schools.filter(pk=school.pk)
    if not schools.exists():
        raise PermissionDenied
    parsed = {}
    errors = []
    for kind, upload in (('books', books), ('topics', topics)):
        if upload:
            try:
                parsed[kind] = read_rows(upload, kind)
            except ValueError as error:
                errors.append({'file': kind, 'row': 1, 'message': str(error)})
    if errors:
        return {'errors': errors, 'created': 0, 'reused': 0, 'rows': 0}
    created = reused = processed = 0
    with transaction.atomic():
        targets = list(schools.select_for_update().order_by('pk'))
        for kind, rows in parsed.items():
            for number, row in rows:
                try:
                    with transaction.atomic():
                        missing = [LABELS[name] for name in REQUIRED[kind] if not row.get(name)]
                        if missing:
                            raise ValueError('مقادیر الزامی خالی‌اند: ' + '، '.join(missing))
                        if any(len(value) > (100 if name in {'grade', 'field', 'book'} else 200) for name, value in row.items()):
                            raise ValueError('طول یکی از مقادیر بیش از حد مجاز است.')
                        flags = []
                        title = grade_title(row.get('grade'))
                        if title and title not in GRADE_TITLES:
                            raise ValueError('فقط پایه‌های پنجم تا دوازدهم مجاز هستند.')
                        for target in targets:
                            if kind == 'books':
                                if not user.is_superuser and user.role != 'PROVINCE_TRUSTEE':
                                    if not grades_for_user(user).filter(school=target, title=title).exists():
                                        raise ValueError(f'{target.name}: این پایه در محدوده دسترسی شما نیست.')
                                field_title = 'عمومی' if is_general_grade(title) else row.get('field')
                                if not is_general_grade(title) and field_title not in ACADEMIC_FIELDS:
                                    raise ValueError('برای پایه‌های دهم تا دوازدهم رشته ریاضی، تجربی یا انسانی الزامی است.')
                                if is_general_grade(title) and row.get('field') not in {'', None, 'عمومی'}:
                                    raise ValueError('پایه‌های پنجم تا نهم رشته ندارند؛ ستون رشته را خالی بگذارید.')
                                grade, grade_new = get_named(Grade, school=target, title=title)
                                field, field_new = get_named(FieldOfStudy, grade=grade, title=field_title)
                                book, book_new = get_named(Subject, field=field, title=row['book'])
                                chapter, chapter_new = get_named(Chapter, subject=book, name=row['chapter'])
                                flags.extend([grade_new, field_new, book_new, chapter_new])
                            else:
                                candidates = Chapter.objects.filter(
                                    subject__in=subjects_for_user(user), subject__field__grade__school=target,
                                    name=row['chapter'],
                                )
                                if title:
                                    candidates = candidates.filter(subject__field__grade__title=title)
                                if row.get('field') and not is_general_grade(title):
                                    candidates = candidates.filter(subject__field__title=row['field'])
                                if candidates.count() != 1:
                                    raise ValueError(f'{target.name}: فصل یافت نشد، مجاز نیست یا هم‌نام است؛ ابتدا فایل اول را وارد کنید و پایه و رشته را برای تشخیص فصل مشخص کنید. اگر باز هم هم‌نام‌اند، نام فصل را در فایل اول متمایز کنید.')
                                chapter = candidates.get()
                                lesson, lesson_new = get_named(Lesson, chapter=chapter, name=row['lesson'])
                                topic, topic_new = get_named(Topic, chapter=chapter, lesson=lesson, name=row['topic'])
                                flags.extend([lesson_new, topic_new])
                    created += sum(flags)
                    reused += len(flags) - sum(flags)
                    processed += 1
                except (ValueError, ValidationError) as error:
                    errors.append({'file': kind, 'row': number, 'message': str(error)})
        if errors:
            transaction.set_rollback(True)
    return {'errors': errors[:100], 'error_count': len(errors), 'created': 0 if errors else created,
            'reused': 0 if errors else reused, 'rows': 0 if errors else processed}


def template_xlsx(kind):
    headers = ['پایه', 'رشته', 'کتاب', 'فصل'] if kind == 'books' else ['فصل', 'درس', 'مبحث', 'پایه', 'رشته']
    example = ['دهم', 'تجربی', 'زیست‌شناسی ۱', 'دنیای زنده'] if kind == 'books' else ['دنیای زنده', 'درس اول', 'شناخت زیست', 'دهم', 'تجربی']
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>')
        archive.writestr('_rels/.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        archive.writestr('xl/workbook.xml', '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="ساختار مدرسه" sheetId="1" r:id="rId1"/></sheets></workbook>')
        archive.writestr('xl/_rels/workbook.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
        archive.writestr('xl/styles.xml', '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="1"><font><sz val="11"/><name val="Arial"/></font></fonts><fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf/></cellStyleXfs><cellXfs count="2"><xf fontId="0" fillId="0" borderId="0" xfId="0"/><xf fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs></styleSheet>')
        archive.writestr('xl/worksheets/sheet1.xml', _sheet_xml([headers, example], [25] * len(headers)))
    return output.getvalue()


@login_required
@require_http_methods(['GET', 'POST'])
def school_structure(request):
    user = request.user
    if not (user.is_superuser or user.role in {'ADMIN', 'PROVINCE_TRUSTEE'}):
        raise PermissionDenied
    kind = request.GET.get('template')
    if kind in REQUIRED:
        response = HttpResponse(template_xlsx(kind), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = f'attachment; filename="school-{kind}.xlsx"'
        return response
    result = None
    scope_form = None
    if user.is_superuser:
        scope_form = ConsultantGradeScopeForm(request.POST if request.POST.get('action') == 'scope' else None)
    upload_form = StructureUploadForm(request.POST if request.POST.get('action') == 'import' else None, request.FILES or None, user=user)
    if request.method == 'POST':
        if request.POST.get('action') == 'scope':
            if not user.is_superuser:
                raise PermissionDenied
            if scope_form.is_valid():
                scope_form.cleaned_data['consultant'].allowed_grades.set(scope_form.cleaned_data['grades'])
                messages.success(request, 'محدوده پایه‌های مشاور ذخیره شد.')
        elif request.POST.get('action') == 'import' and upload_form.is_valid():
            result = import_structure(user, **upload_form.cleaned_data)
            if result['errors']:
                messages.error(request, 'به دلیل خطا هیچ تغییری ذخیره نشد؛ فایل‌ها را اصلاح و دوباره ارسال کنید.')
            else:
                messages.success(request, f"{result['rows']} ردیف پردازش شد؛ {result['created']} مورد جدید ثبت شد.")
    subjects = subjects_for_user(user).select_related('field__grade__school').prefetch_related(
        'question_chapters__lessons__topics', 'question_chapters__topics',
    ).annotate(chapter_count=Count('question_chapters', distinct=True))
    selected_grade = request.GET.get('grade', '')
    if selected_grade.isdecimal():
        subjects = subjects.filter(field__grade_id=int(selected_grade))
    subject_ids = subjects.values('pk')
    return render(request, 'questions_module/school_structure.html', {
        'upload_form': upload_form, 'scope_form': scope_form, 'result': result,
        'scope_assignments': {str(consultant.pk): list(consultant.allowed_grades.values_list('pk', flat=True))
                              for consultant in scope_form.fields['consultant'].queryset} if scope_form else {},
        'grades': grades_for_user(user).select_related('school').order_by('school__name', 'title'),
        'selected_grade': selected_grade, 'books': subjects.order_by('field__grade__title', 'field__title', 'title')[:200],
        'stats': {'books': subjects.count(), 'chapters': Chapter.objects.filter(subject__in=subject_ids).count(),
                  'lessons': Lesson.objects.filter(chapter__subject__in=subject_ids).count(),
                  'topics': Topic.objects.filter(chapter__subject__in=subject_ids, is_active=True).count()},
    })


class ConsultantGradeScopeForm(forms.Form):
    consultant = forms.ModelChoiceField(label='مشاور', queryset=get_user_model().objects.none())
    grades = forms.ModelMultipleChoiceField(label='پایه‌های مجاز', queryset=Grade.objects.none(), required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['consultant'].queryset = get_user_model().objects.filter(role='CONSULTANT', is_superuser=False, is_active=True)
        self.fields['grades'].queryset = Grade.objects.filter(is_active=True).select_related('school')
        self.fields['grades'].label_from_instance = lambda grade: f'{grade.school.name} · {grade.title}'
        for field in self.fields.values():
            field.widget.attrs['class'] = 'structure-input'
        self.fields['grades'].widget.attrs['size'] = '7'
