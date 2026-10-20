import csv
import io
import re
import zipfile
from xml.etree import ElementTree
from xml.sax.saxutils import escape

from django.contrib.auth import get_user_model
from django.db import transaction

from users_module.models import FieldOfStudy, Grade, Province, School
from users_module.school_structure import grade_title, is_general_grade
from .models import Consultant, ProvinceTrustee, Student, UserRole


DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
ROLE_MAP = {
    "دانش آموز": UserRole.STUDENT, "دانش‌آموز": UserRole.STUDENT, "student": UserRole.STUDENT,
    "مشاور": UserRole.CONSULTANT, "consultant": UserRole.CONSULTANT,
    "مسئول منطقه": UserRole.PROVINCE_TRUSTEE, "معتمد": UserRole.PROVINCE_TRUSTEE, "معتمد استان": UserRole.PROVINCE_TRUSTEE, "province_trustee": UserRole.PROVINCE_TRUSTEE,
    "ناظر": UserRole.CONTENT_MODERATOR, "ناظر محتوا": UserRole.CONTENT_MODERATOR, "content_moderator": UserRole.CONTENT_MODERATOR,
    "ادمین": UserRole.ADMIN, "مدیر": UserRole.ADMIN, "مدیر سیستم": UserRole.ADMIN, "admin": UserRole.ADMIN,
}
HEADERS = {
    "اسم": "first_name", "نام": "first_name", "فامیل": "last_name", "نام خانوادگی": "last_name",
    "مدرسه": "city", "شهر": "city", "استان": "province", "رشته": "field", "پایه": "grade", "پایه (اختیاری)": "grade",
    "کد ملی": "national_code", "کدملی": "national_code", "رمز عبور": "password",
    "نقش": "role", "نام کاربری": "username",
}
TEMPLATE_HEADERS = ["اسم", "فامیل", "استان", "مدرسه", "پایه", "رشته", "کد ملی", "رمز عبور", "نقش", "نام کاربری"]


def clean_text(value):
    return str(value or "").strip().replace("ي", "ی").replace("ك", "ک")


def normalized(value):
    return re.sub(r"\s+", " ", clean_text(value)).casefold()


def normalize_code(value):
    value = clean_text(value).translate(DIGITS)
    if value.endswith(".0"):
        value = value[:-2]
    return value.zfill(10) if value.isdigit() and len(value) < 10 else value


def _xlsx_rows(upload):
    with zipfile.ZipFile(upload) as archive:
        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
            shared = ["".join(node.text or "" for node in item.iter() if node.tag.endswith("}t")) for item in root]
        workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
        rels = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {item.attrib["Id"]: item.attrib["Target"] for item in rels}
        sheet = next(node for node in workbook.iter() if node.tag.endswith("}sheet"))
        relation_id = next(value for key, value in sheet.attrib.items() if key.endswith("}id"))
        target = targets[relation_id].lstrip("/")
        if not target.startswith("xl/"):
            target = "xl/" + target
        root = ElementTree.fromstring(archive.read(target))
        for row in (node for node in root.iter() if node.tag.endswith("}row")):
            values, current = [], 0
            for cell in (node for node in row if node.tag.endswith("}c")):
                ref = cell.attrib.get("r", "A1")
                letters = re.match(r"[A-Z]+", ref).group()
                column = 0
                for letter in letters:
                    column = column * 26 + ord(letter) - 64
                while current < column - 1:
                    values.append(""); current += 1
                value_node = next((node for node in cell.iter() if node.tag.endswith("}v")), None)
                inline = next((node for node in cell.iter() if node.tag.endswith("}t")), None)
                value = inline.text if inline is not None else (value_node.text if value_node is not None else "")
                if cell.attrib.get("t") == "s" and value != "":
                    value = shared[int(value)]
                values.append(value or ""); current += 1
            yield values


def spreadsheet_rows(upload):
    name = upload.name.lower()
    if name.endswith(".csv"):
        text = upload.read().decode("utf-8-sig")
        rows = list(csv.reader(io.StringIO(text)))
    elif name.endswith(".xlsx"):
        rows = list(_xlsx_rows(upload))
    else:
        raise ValueError("فقط فایل‌های xlsx و csv قابل قبول هستند.")
    if not rows:
        return []
    headers = [HEADERS.get(clean_text(value), clean_text(value).lower()) for value in rows[0]]
    required_headers = {"first_name", "last_name", "national_code", "password", "role"}
    missing_headers = required_headers.difference(headers)
    if missing_headers:
        labels = {value: key for key, value in HEADERS.items()}
        raise ValueError("ستون‌های اصلی فایل ناقص است: " + "، ".join(labels.get(item, item) for item in sorted(missing_headers)))
    return [{headers[index]: clean_text(value) for index, value in enumerate(row) if index < len(headers)} for row in rows[1:] if any(clean_text(value) for value in row)]


def _province(value):
    value = normalized(value)
    for item in Province.objects.all():
        if value in {normalized(item.name), normalized(item.get_name_display())}:
            return item
    raise ValueError("استان در سامانه تعریف نشده است.")


def _role(value):
    role = ROLE_MAP.get(normalized(value))
    if not role:
        raise ValueError("نقش معتبر نیست.")
    return role


def import_users(upload):
    try:
        rows = spreadsheet_rows(upload)
    except (ValueError, KeyError, zipfile.BadZipFile, ElementTree.ParseError) as error:
        return [{"row": 1, "ok": False, "message": str(error) or "ساختار فایل Excel معتبر نیست."}]
    if not rows:
        return [{"row": 2, "ok": False, "message": "فایل هیچ عضو تکمیل‌شده‌ای ندارد؛ اطلاعات را از ردیف دوم وارد کنید."}]
    results = []
    required = {"first_name", "last_name", "national_code", "password", "role"}
    User = get_user_model()
    for number, row in enumerate(rows, start=2):
        try:
            missing = [key for key in required if not row.get(key)]
            if missing:
                labels = {"first_name": "اسم", "last_name": "فامیل", "national_code": "کد ملی", "password": "رمز عبور", "role": "نقش"}
                raise ValueError("مقادیر ضروری خالی است: " + "، ".join(labels[key] for key in missing))
            code = normalize_code(row["national_code"])
            if not (code.isdigit() and len(code) == 10):
                raise ValueError("کد ملی باید ۱۰ رقم باشد.")
            role = _role(row["role"])
            province = None
            school = None
            if role in (UserRole.STUDENT, UserRole.PROVINCE_TRUSTEE):
                if not row.get("province"):
                    raise ValueError("استان برای این نقش الزامی است.")
                province = _province(row["province"])
            if role == UserRole.STUDENT:
                row['grade'] = grade_title(row.get('grade'))
                if is_general_grade(row['grade']):
                    if row.get('field') not in {'', None, 'عمومی'}:
                        raise ValueError('پایه‌های پنجم تا نهم رشته ندارند؛ ستون رشته را خالی بگذارید.')
                    row['field'] = 'عمومی'
                empty_student_fields = [label for key, label in (("city", "مدرسه"), ("grade", "پایه"), ("field", "رشته")) if not row.get(key)]
                if empty_student_fields:
                    raise ValueError("برای دانش‌آموز این موارد الزامی است: " + "، ".join(empty_student_fields))
                school = next((item for item in School.objects.filter(province=province) if normalized(item.name) == normalized(row["city"])), None)
                if not school:
                    raise ValueError("مدرسه واردشده در استان انتخابی داخل users_module تعریف نشده است.")
            with transaction.atomic():
                user, created = User.objects.get_or_create(username=code, defaults={"first_name": row["first_name"], "last_name": row["last_name"], "role": role})
                if not created:
                    raise ValueError("کاربری با این کد ملی قبلاً وجود دارد.")
                user.first_name, user.last_name, user.role = row["first_name"], row["last_name"], role
                user.is_staff = role == UserRole.ADMIN
                user.set_password(row["password"])
                user.save()
                if role == UserRole.STUDENT:
                    fields = [item for item in FieldOfStudy.objects.select_related("grade").filter(grade__school=school, is_active=True, grade__is_active=True) if normalized(item.title) == normalized(row["field"]) and normalized(item.grade.title) == normalized(row["grade"])]
                    if len(fields) != 1:
                        raise ValueError("ترکیب استان، مدرسه، پایه و رشته در users_module پیدا نشد.")
                    Student.objects.create(user=user, field=fields[0])
                elif role == UserRole.CONSULTANT:
                    Consultant.objects.get_or_create(consultant=user)
                elif role == UserRole.PROVINCE_TRUSTEE:
                    ProvinceTrustee.objects.create(user=user, province=province)
            results.append({"row": number, "ok": True, "message": f"{row['first_name']} {row['last_name']} ساخته شد."})
        except Exception as error:
            results.append({"row": number, "ok": False, "message": str(error)})
    return results


def _xlsx_cell(reference, value, style=0):
    return f'<c r="{reference}" t="inlineStr" s="{style}"><is><t>{escape(clean_text(value))}</t></is></c>'


def _sheet_xml(rows, widths=None, auto_filter=False):
    columns = ""
    if widths:
        columns = "<cols>" + "".join(f'<col min="{index}" max="{index}" width="{width}" customWidth="1"/>' for index, width in enumerate(widths, 1)) + "</cols>"
    xml_rows = []
    for row_number, row in enumerate(rows, 1):
        cells = []
        for index, value in enumerate(row, 1):
            number, letters = index, ""
            while number:
                number, remainder = divmod(number - 1, 26)
                letters = chr(65 + remainder) + letters
            cells.append(_xlsx_cell(f"{letters}{row_number}", value, 1 if row_number == 1 else 0))
        xml_rows.append(f'<row r="{row_number}">{"".join(cells)}</row>')
    filter_xml = f'<autoFilter ref="A1:J{max(len(rows), 1)}"/>' if auto_filter else ""
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<sheetViews><sheetView workbookViewId="0" rightToLeft="1"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
            f'{columns}<sheetData>{"".join(xml_rows)}</sheetData>{filter_xml}</worksheet>')


def build_user_template_xlsx(member_rows=None):
    provinces = list(Province.objects.all())
    schools = list(School.objects.select_related("province").order_by("province_id", "name"))
    grades = list(Grade.objects.select_related("school__province").filter(is_active=True).order_by("school_id", "pk"))
    fields = list(FieldOfStudy.objects.select_related("grade__school__province").filter(is_active=True, grade__is_active=True).order_by("grade_id", "title"))
    entry_rows = [TEMPLATE_HEADERS] + list(member_rows or [])
    guide_rows = [
        ["راهنمای تکمیل فایل", "توضیح"],
        ["نام کاربری", "لازم نیست تغییر کند؛ سامانه همیشه کد ملی را به‌عنوان نام کاربری ثبت می‌کند."],
        ["دانش‌آموز", "اسم، فامیل، استان، مدرسه، پایه، رشته، کد ملی، رمز عبور و نقش را کامل کنید."],
        ["مسئول منطقه", "اسم، فامیل، استان، کد ملی، رمز عبور و نقش الزامی است."],
        ["مشاور / ناظر / مدیر", "اسم، فامیل، کد ملی، رمز عبور و نقش الزامی است؛ اطلاعات آموزشی می‌تواند خالی باشد."],
        ["نقش‌های مجاز", "دانش‌آموز، مشاور، مسئول منطقه، ناظر محتوا، مدیر سیستم"],
        ["هشدار", "نام استان، مدرسه، پایه و رشته را دقیقاً از شیت «مقادیر مجاز» کپی کنید."],
    ]
    allowed_rows = [["نوع", "استان", "مدرسه", "پایه", "رشته"]]
    allowed_rows += [["استان", item.get_name_display(), "", "", ""] for item in provinces]
    allowed_rows += [["مدرسه", item.province.get_name_display(), item.name, "", ""] for item in schools]
    allowed_rows += [["پایه", item.school.province.get_name_display(), item.school.name, item.title, ""] for item in grades]
    allowed_rows += [["رشته", item.grade.school.province.get_name_display(), item.grade.school.name, item.grade.title, item.title] for item in fields]
    allowed_rows += [["نقش", "", "", "", label] for _, label in UserRole.choices]

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/worksheets/sheet3.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>')
        archive.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        archive.writestr("xl/workbook.xml", '<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="ورود اعضا" sheetId="1" r:id="rId1"/><sheet name="راهنما" sheetId="2" r:id="rId2"/><sheet name="مقادیر مجاز" sheetId="3" r:id="rId3"/></sheets></workbook>')
        archive.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/><Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet3.xml"/><Relationship Id="rId4" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
        archive.writestr("xl/styles.xml", '<?xml version="1.0" encoding="UTF-8"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="2"><font><sz val="11"/><name val="Arial"/></font><font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Arial"/></font></fonts><fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF4F46E5"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="2"><xf numFmtId="49" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/><xf numFmtId="49" fontId="1" fillId="2" borderId="0" xfId="0" applyFill="1" applyFont="1" applyNumberFormat="1"/></cellXfs></styleSheet>')
        archive.writestr("xl/worksheets/sheet1.xml", _sheet_xml(entry_rows, [16, 18, 16, 18, 14, 18, 16, 18, 18, 16], True))
        archive.writestr("xl/worksheets/sheet2.xml", _sheet_xml(guide_rows, [24, 85]))
        archive.writestr("xl/worksheets/sheet3.xml", _sheet_xml(allowed_rows, [14, 20, 22, 18, 24], True))
    output.seek(0)
    return output


def shift_student_grade(student, direction):
    current = student.field.grade
    rank_words = {"اول": 1, "دوم": 2, "سوم": 3, "چهارم": 4, "پنجم": 5, "ششم": 6, "هفتم": 7, "هشتم": 8, "نهم": 9, "دهم": 10, "یازدهم": 11, "دوازدهم": 12}
    def grade_rank(grade):
        title = clean_text(grade.title).translate(DIGITS)
        match = re.search(r"\d+", title)
        if match:
            return int(match.group())
        return next((rank for word, rank in rank_words.items() if word in title), grade.pk)
    grades = sorted(Grade.objects.filter(school=current.school, is_active=True), key=grade_rank)
    index = next((i for i, grade in enumerate(grades) if grade.pk == current.pk), None)
    target_index = index + direction if index is not None else -1
    if target_index < 0 or target_index >= len(grades):
        return False
    target_field = FieldOfStudy.objects.filter(grade=grades[target_index], title__iexact=student.field.title, is_active=True).first()
    if not target_field:
        return False
    student.field = target_field
    student.save(update_fields=["field"])
    return True
