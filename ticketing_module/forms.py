from django import forms
from django.contrib.auth import get_user_model
from django.db.models import Q

from auth_module.models import Consultant, UserRole
from users_module.models import Subject
from users_module.curriculum_access import subjects_for_user

from .models import Ticket, TicketMessage, TicketQueue, TicketStatus, TicketType


INPUT_CLASS = (
    "w-full rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm "
    "outline-none transition focus:border-blue-500 focus:ring-4 focus:ring-blue-100"
)


class TicketCreateForm(forms.ModelForm):
    content = forms.CharField(
        label="متن پرسش",
        widget=forms.Textarea(attrs={"class": INPUT_CLASS, "rows": 7}),
    )
    attachment = forms.FileField(
        label="پیوست", required=False, widget=forms.FileInput(attrs={"class": INPUT_CLASS})
    )

    class Meta:
        model = Ticket
        fields = ["title", "ticket_type", "subject"]
        labels = {"title": "عنوان", "ticket_type": "موضوع", "subject": "درس"}
        widgets = {
            "title": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "خلاصه‌ای کوتاه از پرسشت بنویس"}),
            "ticket_type": forms.RadioSelect(),
            "subject": forms.Select(attrs={"class": INPUT_CLASS}),
        }

    def __init__(self, *args, student=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.student = student
        ticket_type = (
            self.data.get(self.add_prefix("ticket_type"))
            if self.is_bound
            else self.initial.get("ticket_type", self.instance.ticket_type)
        )
        self.fields["subject"].disabled = ticket_type != TicketType.LESSON
        self.fields["subject"].empty_label = "درس مرتبط با پرسش را انتخاب کنید"
        self.fields["content"].widget.attrs["placeholder"] = "پرسشت را با جزئیات بنویس تا بهتر راهنمایی‌ات کنیم…"
        self.fields['subject'].queryset = subjects_for_user(student.user).filter(field=student.field) if student else Subject.objects.none()

    def clean(self):
        cleaned = super().clean()
        ticket_type = cleaned.get("ticket_type")
        subject = cleaned.get("subject")
        if ticket_type == TicketType.LESSON and not subject:
            self.add_error("subject", "برای پرسش درسی، درس را انتخاب کنید.")
        if ticket_type != TicketType.LESSON:
            cleaned["subject"] = None
        return cleaned


class TicketMessageForm(forms.ModelForm):
    class Meta:
        model = TicketMessage
        fields = ["content", "attachment"]
        labels = {"content": "متن پیام", "attachment": "پیوست"}
        widgets = {
            "content": forms.Textarea(attrs={"class": INPUT_CLASS, "rows": 5}),
            "attachment": forms.FileInput(attrs={"class": INPUT_CLASS}),
        }


class ModerationForm(forms.Form):
    decision = forms.ChoiceField(
        label="نتیجه بررسی",
        choices=(("approve", "تأیید"), ("reject", "رد")),
        widget=forms.Select(attrs={"class": INPUT_CLASS}),
    )
    note = forms.CharField(
        label="توضیح ناظر",
        required=False,
        widget=forms.Textarea(attrs={"class": INPUT_CLASS, "rows": 3}),
    )


class ReferralForm(forms.Form):
    queue = forms.ChoiceField(
        label="ارجاع به", choices=TicketQueue.choices, widget=forms.Select(attrs={"class": INPUT_CLASS})
    )
    assignee = forms.ModelChoiceField(
        label="کاربر مشخص",
        queryset=get_user_model().objects.none(),
        required=False,
        widget=forms.Select(attrs={"class": INPUT_CLASS}),
    )
    note = forms.CharField(
        label="علت ارجاع",
        required=False,
        widget=forms.Textarea(attrs={"class": INPUT_CLASS, "rows": 3}),
    )

    def __init__(self, *args, actor=None, ticket=None, **kwargs):
        super().__init__(*args, **kwargs)
        User = get_user_model()
        moderators = Q(role=UserRole.CONTENT_MODERATOR)
        if ticket:
            allowed_genders = [User.ModerationStudentGender.BOTH]
            if ticket.student.user.gender in {"MALE", "FEMALE"}:
                allowed_genders.append(ticket.student.user.gender)
            moderators &= Q(moderation_student_gender__in=allowed_genders)
        eligible = moderators | Q(role=UserRole.ADMIN) | Q(is_superuser=True)
        if ticket:
            gender_scope = Q(
                scopes__ticket_student_gender=Consultant.TicketStudentGender.BOTH
            )
            if ticket.student.user.gender in {"MALE", "FEMALE"}:
                gender_scope |= Q(scopes__ticket_student_gender=ticket.student.user.gender)
            eligible |= Q(
                role=UserRole.PROVINCE_TRUSTEE,
                trustee_provinces__province_id=ticket.province.id,
            )
            if ticket.ticket_type == TicketType.PSYCHOLOGY:
                eligible |= gender_scope & Q(
                    role=UserRole.CONSULTANT,
                    scopes__can_answer_psychology=True,
                )
            elif ticket.ticket_type == TicketType.LESSON and ticket.subject_id:
                eligible |= gender_scope & Q(
                    role=UserRole.CONSULTANT,
                    scopes__accesses__name="ticket",
                    scopes__accesses__subject_id=ticket.subject_id,
                )
        self.fields["assignee"].queryset = User.objects.filter(
            eligible, is_active=True
        ).distinct()
        if actor and actor.role == UserRole.CONSULTANT:
            self.fields["queue"].choices = [(TicketQueue.TRUSTEE, "مسئول منطقه")]

    def clean(self):
        cleaned = super().clean()
        queue = cleaned.get("queue")
        assignee = cleaned.get("assignee")
        expected_roles = {
            TicketQueue.CONSULTANT: UserRole.CONSULTANT,
            TicketQueue.TRUSTEE: UserRole.PROVINCE_TRUSTEE,
            TicketQueue.MODERATOR: UserRole.CONTENT_MODERATOR,
            TicketQueue.ADMIN: UserRole.ADMIN,
        }
        if assignee and not (
            queue == TicketQueue.ADMIN and assignee.is_superuser
        ) and assignee.role != expected_roles.get(queue):
            self.add_error("assignee", "نقش کاربر انتخاب‌شده با مقصد ارجاع سازگار نیست.")
        return cleaned


class TicketEditForm(forms.ModelForm):
    class Meta:
        model = Ticket
        fields = ["ticket_type", "subject", "status"]
        labels = {"ticket_type": "موضوع", "subject": "درس", "status": "وضعیت"}
        widgets = {
            "ticket_type": forms.Select(attrs={"class": INPUT_CLASS}),
            "subject": forms.Select(attrs={"class": INPUT_CLASS}),
            "status": forms.Select(attrs={"class": INPUT_CLASS}),
        }

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("ticket_type") == TicketType.LESSON and not cleaned.get("subject"):
            self.add_error("subject", "برای موضوع درسی، انتخاب درس الزامی است.")
        if cleaned.get("ticket_type") != TicketType.LESSON:
            cleaned["subject"] = None
        return cleaned
