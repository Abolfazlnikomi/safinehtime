from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from auth_module.decorators import role_required
from auth_module.models import UserRole

from .forms import (
    ModerationForm,
    ReferralForm,
    TicketCreateForm,
    TicketEditForm,
    TicketMessageForm,
)
from .models import (
    ModerationStatus,
    Ticket,
    TicketAuditLog,
    TicketMessage,
    TicketQueue,
    TicketStatus,
    TicketType,
)
from .services import (
    STAFF_ROLES,
    can_access_ticket,
    route_after_initial_approval,
    student_profile_for,
    visible_tickets_for,
)


ALL_ROLES = (
    UserRole.STUDENT,
    UserRole.CONSULTANT,
    UserRole.PROVINCE_TRUSTEE,
    UserRole.CONTENT_MODERATOR,
    UserRole.ADMIN,
)


def _has_attachment(file_value):
    """
    تشخیص وجود فایل واقعی.
    برای UploadedFile و FieldFile هر دو قابل استفاده است.
    """
    return bool(file_value and getattr(file_value, "name", file_value))


def _approve_text_message(message, reviewer=None):
    """
    پیام بدون فایل مستقیماً تأیید می‌شود.
    """
    message.moderation_status = ModerationStatus.APPROVED
    message.is_approved_by_moderator = True

    if reviewer is not None:
        message.reviewed_by = reviewer
        message.reviewed_at = timezone.now()


def _set_ticket_after_direct_text_message(ticket, sender):
    """
    پیام متنی پس از تأیید مستقیم، تیکت را از حالت انتظار ناظر خارج می‌کند.
    پیام مشاور به‌عنوان پاسخ داده‌شده ثبت می‌شود.
    پیام سایر کاربران باعث باز بودن تیکت می‌شود.
    """
    if getattr(sender, "role", None) == UserRole.CONSULTANT:
        ticket.status = TicketStatus.RESOLVED
    elif ticket.status == TicketStatus.PENDING_APPROVAL:
        ticket.status = TicketStatus.OPEN

    ticket.save(update_fields=["status", "updated_at"])


@role_required(*ALL_ROLES)
def ticket_list(request):
    tickets = visible_tickets_for(request.user)

    status = request.GET.get("status")
    ticket_type = request.GET.get("type")

    if status in TicketStatus.values:
        tickets = tickets.filter(status=status)

    if ticket_type:
        tickets = tickets.filter(ticket_type=ticket_type)

    return render(
        request,
        "ticketing_module/ticket_list.html",
        {
            "tickets": tickets,
            "status_choices": TicketStatus.choices,
            "selected_status": status,
            "selected_type": ticket_type,
        },
    )


@role_required(UserRole.STUDENT)
def ticket_create(request):
    student = student_profile_for(request.user)

    if not student:
        messages.error(request, "پروفایل دانش‌آموزی شما کامل نشده است.")
        return redirect("ticketing:list")

    if request.method == "POST":
        form = TicketCreateForm(
            request.POST,
            request.FILES,
            student=student,
        )

        if form.is_valid():
            content = form.cleaned_data["content"]
            attachment = form.cleaned_data.get("attachment")
            technical_issue = form.cleaned_data.get("technical_issue")
            is_private = form.cleaned_data.get("is_private_consultation", False)
            has_attachment = _has_attachment(attachment)

            # الصاق مشخصه نوع اشکال فنی به ابتدای متن پیام جهت وضوح بیشتر
            if form.cleaned_data.get("ticket_type") == TicketType.TECHNICAL and technical_issue:
                issue_label = dict(TicketCreateForm.TECHNICAL_ISSUES).get(technical_issue, technical_issue)
                content = f"[نوع اشکال فنی: {issue_label}]\n\n{content}"

            with transaction.atomic():
                ticket = form.save(commit=False)
                ticket.student = student
                ticket.is_private_consultation = is_private

                if has_attachment:
                    # فقط تیکت دارای فایل برای ناظر ارسال می‌شود.
                    ticket.current_queue = TicketQueue.MODERATOR
                    ticket.status = TicketStatus.PENDING_APPROVAL
                else:
                    # تیکت متنی مستقیماً وارد جریان پاسخ‌گویی می‌شود.
                    ticket.status = TicketStatus.OPEN

                ticket.save()

                ticket_message = TicketMessage(
                    ticket=ticket,
                    sender=request.user,
                    content=content,
                    attachment=attachment,
                )

                if has_attachment:
                    ticket_message.moderation_status = ModerationStatus.PENDING
                    ticket_message.is_approved_by_moderator = False
                else:
                    _approve_text_message(ticket_message)

                ticket_message.save()

                metadata = {
                    "is_private": is_private,
                    "has_attachment": has_attachment,
                }
                if technical_issue:
                    metadata["technical_issue"] = technical_issue

                if has_attachment:
                    TicketAuditLog.objects.create(
                        ticket=ticket,
                        actor=request.user,
                        action=TicketAuditLog.Action.CREATED,
                        to_queue=TicketQueue.MODERATOR,
                        description="ثبت تیکت دارای پیوست و ارسال برای ناظر محتوا",
                        metadata=metadata,
                    )
                    success_message = "تیکت ثبت شد. فایل پیوست پس از بررسی ناظر نمایش داده می‌شود."
                else:
                    # متن بدون فایل مستقیم مسیردهی می‌شود
                    route_after_initial_approval(ticket, request.user)

                    TicketAuditLog.objects.create(
                        ticket=ticket,
                        actor=request.user,
                        action=TicketAuditLog.Action.CREATED,
                        to_queue=ticket.current_queue,
                        description="ثبت تیکت متنی و ارسال مستقیم بدون نیاز به ناظر",
                        metadata=metadata,
                    )
                    success_message = "تیکت با موفقیت ایجاد و ارسال شد."

            messages.success(request, success_message)
            return redirect("ticketing:detail", pk=ticket.pk)

    else:
        form = TicketCreateForm(student=student)

    return render(
        request,
        "ticketing_module/ticket_form.html",  # ✅ اصلاح شد
        {"form": form},
    )


def _visible_messages(ticket, user):
    from .services import visible_messages_for

    return visible_messages_for(ticket, user)


@role_required(*ALL_ROLES)
def ticket_detail(request, pk):
    ticket = get_object_or_404(
        Ticket.objects.select_related(
            "student__user",
            "student__field__grade__school__province",
            "subject",
            "current_assignee_user",
        ),
        pk=pk,
    )

    if not can_access_ticket(request.user, ticket):
        raise PermissionDenied("این تیکت در محدوده دسترسی شما نیست.")

    previous_tickets = Ticket.objects.none()

    if request.user.role in STAFF_ROLES or request.user.is_superuser:
        previous_tickets = (
            Ticket.objects.filter(
                student=ticket.student,
                subject=ticket.subject,
            )
            .exclude(pk=ticket.pk)
            .order_by("-created_at")[:10]
        )

    can_moderate = (
        request.user.is_superuser
        or request.user.role
        in {
            UserRole.CONTENT_MODERATOR,
            UserRole.ADMIN,
        }
    )

    can_edit = (
        not ticket.is_private_consultation
        and (
            request.user.is_superuser
            or request.user.role
            in {
                UserRole.PROVINCE_TRUSTEE,
                UserRole.ADMIN,
            }
        )
    )

    can_refer = (
        not ticket.is_private_consultation
        and (
            request.user.is_superuser
            or request.user.role
            in {
                UserRole.CONSULTANT,
                UserRole.PROVINCE_TRUSTEE,
                UserRole.ADMIN,
            }
        )
    )

    return render(
        request,
        "ticketing_module/ticket_detail.html",
        {
            "ticket": ticket,
            "ticket_messages": _visible_messages(ticket, request.user),
            "message_form": TicketMessageForm(),
            "referral_form": ReferralForm(
                actor=request.user,
                ticket=ticket,
            ),
            "edit_form": TicketEditForm(instance=ticket),
            "previous_tickets": previous_tickets,
            "can_moderate": can_moderate,
            "can_edit": can_edit,
            "can_refer": can_refer,
            "can_view_student_academic_details": (
                request.user.role != UserRole.CONSULTANT
            ),
        },
    )


@role_required(*ALL_ROLES)
def add_message(request, pk):
    if request.method != "POST":
        raise PermissionDenied

    ticket = get_object_or_404(Ticket, pk=pk)

    if not can_access_ticket(request.user, ticket):
        raise PermissionDenied

    if ticket.status == TicketStatus.CLOSED:
        messages.error(
            request,
            "برای تیکت بسته‌شده نمی‌توان پیام فرستاد.",
        )
        return redirect("ticketing:detail", pk=pk)

    form = TicketMessageForm(
        request.POST,
        request.FILES,
    )

    if form.is_valid():
        with transaction.atomic():
            item = form.save(commit=False)
            item.ticket = ticket
            item.sender = request.user

            has_attachment = _has_attachment(item.attachment)

            if has_attachment:
                # هر نوع فایل، فارغ از نقش فرستنده، باید به ناظر برود.
                item.moderation_status = ModerationStatus.PENDING
                item.is_approved_by_moderator = False
                item.reviewed_by = None
                item.reviewed_at = None

                item.save()

                ticket.status = TicketStatus.PENDING_APPROVAL
                ticket.save(
                    update_fields=[
                        "status",
                        "updated_at",
                    ]
                )

                audit_description = "پیام دارای تصویر یا فایل در انتظار بررسی ناظر"
                success_message = "پیام دارای فایل ارسال شد و پس از تأیید ناظر نمایش داده می‌شود."
            else:
                # پیام متنی مستقیم تأیید می‌شود و به ناظر نمی‌رود.
                _approve_text_message(item)
                item.save()

                _set_ticket_after_direct_text_message(
                    ticket,
                    request.user,
                )

                audit_description = "پیام متنی تأییدشده و ارسال مستقیم"
                success_message = "پیام متنی با موفقیت ارسال شد."

            TicketAuditLog.objects.create(
                ticket=ticket,
                actor=request.user,
                action=TicketAuditLog.Action.MESSAGE_SENT,
                description=audit_description,
                metadata={
                    "has_attachment": has_attachment,
                    "moderation_required": has_attachment,
                },
            )

        messages.success(request, success_message)

    else:
        messages.error(request, "متن پیام یا فایل ارسالی معتبر نیست.")

    return redirect("ticketing:detail", pk=pk)


@role_required(
    UserRole.CONTENT_MODERATOR,
    UserRole.ADMIN,
)
def moderation_queue(request):
    pending = (
        TicketMessage.objects.filter(
            moderation_status=ModerationStatus.PENDING,
            ticket__in=visible_tickets_for(request.user),
        )
        .select_related(
            "ticket__student__user",
            "ticket__subject",
            "sender",
        )
        .order_by("created_at")
    )

    return render(
        request,
        "ticketing_module/moderation_queue.html",
        {
            "pending_messages": pending,
        },
    )


@role_required(
    UserRole.CONTENT_MODERATOR,
    UserRole.ADMIN,
)
def moderate_message(request, message_id):
    if request.method != "POST":
        raise PermissionDenied

    item = get_object_or_404(
        TicketMessage.objects.select_related(
            "ticket",
            "sender",
        ),
        pk=message_id,
    )

    if not can_access_ticket(request.user, item.ticket):
        raise PermissionDenied

    form = ModerationForm(request.POST)

    if form.is_valid() and item.moderation_status == ModerationStatus.PENDING:
        approved = form.cleaned_data["decision"] == "approve"

        with transaction.atomic():
            item.moderation_status = (
                ModerationStatus.APPROVED
                if approved
                else ModerationStatus.REJECTED
            )
            item.is_approved_by_moderator = approved
            item.reviewed_by = request.user
            item.reviewed_at = timezone.now()
            item.review_note = form.cleaned_data["note"]

            item.save(
                update_fields=[
                    "moderation_status",
                    "is_approved_by_moderator",
                    "reviewed_by",
                    "reviewed_at",
                    "review_note",
                ]
            )

            TicketAuditLog.objects.create(
                ticket=item.ticket,
                actor=request.user,
                action=(
                    TicketAuditLog.Action.APPROVED
                    if approved
                    else TicketAuditLog.Action.REJECTED
                ),
                description=form.cleaned_data["note"],
                metadata={
                    "message_id": item.pk,
                    "has_attachment": _has_attachment(item.attachment),
                },
            )

            if approved:
                first_message = (
                    item.ticket.messages
                    .order_by("created_at")
                    .first()
                )

                if first_message and first_message.pk == item.pk:
                    route_after_initial_approval(
                        item.ticket,
                        request.user,
                    )
                elif item.sender.role == UserRole.CONSULTANT:
                    item.ticket.status = TicketStatus.RESOLVED
                    item.ticket.save(
                        update_fields=[
                            "status",
                            "updated_at",
                        ]
                    )
                else:
                    item.ticket.status = TicketStatus.OPEN
                    item.ticket.save(
                        update_fields=[
                            "status",
                            "updated_at",
                        ]
                    )
            else:
                # تیکت پس از رد فایل در وضعیت باز می‌ماند
                item.ticket.status = TicketStatus.OPEN
                item.ticket.save(
                    update_fields=[
                        "status",
                        "updated_at",
                    ]
                )

        messages.success(request, "نتیجه بررسی ثبت شد.")

    return redirect(
        "ticketing:detail",
        pk=item.ticket_id,
    )


@role_required(
    UserRole.CONSULTANT,
    UserRole.PROVINCE_TRUSTEE,
    UserRole.ADMIN,
)
def refer_ticket(request, pk):
    if request.method != "POST":
        raise PermissionDenied

    ticket = get_object_or_404(Ticket, pk=pk)

    if not can_access_ticket(request.user, ticket):
        raise PermissionDenied

    form = ReferralForm(
        request.POST,
        actor=request.user,
        ticket=ticket,
    )

    if form.is_valid():
        old_queue = ticket.current_queue
        old_user = ticket.current_assignee_user

        with transaction.atomic():
            ticket.current_queue = form.cleaned_data["queue"]
            ticket.current_assignee_user = form.cleaned_data["assignee"]
            ticket.status = TicketStatus.IN_PROGRESS

            ticket.save(
                update_fields=[
                    "current_queue",
                    "current_assignee_user",
                    "status",
                    "updated_at",
                ]
            )

            TicketAuditLog.objects.create(
                ticket=ticket,
                actor=request.user,
                action=TicketAuditLog.Action.REFERRED,
                from_queue=old_queue,
                to_queue=ticket.current_queue,
                from_user=old_user,
                to_user=ticket.current_assignee_user,
                description=form.cleaned_data["note"],
            )

        messages.success(
            request,
            "تیکت با موفقیت ارجاع شد.",
        )
    else:
        messages.error(
            request,
            "اطلاعات ارجاع معتبر نیست.",
        )

    return redirect(
        "ticketing:detail",
        pk=pk,
    )


@role_required(
    UserRole.PROVINCE_TRUSTEE,
    UserRole.ADMIN,
)
def edit_ticket(request, pk):
    if request.method != "POST":
        raise PermissionDenied

    ticket = get_object_or_404(Ticket, pk=pk)

    if not can_access_ticket(request.user, ticket):
        raise PermissionDenied

    old_type = ticket.ticket_type
    old_subject = ticket.subject
    old_status = ticket.status

    form = TicketEditForm(
        request.POST,
        instance=ticket,
    )

    if form.is_valid():
        with transaction.atomic():
            ticket = form.save()

            if old_type != ticket.ticket_type:
                TicketAuditLog.objects.create(
                    ticket=ticket,
                    actor=request.user,
                    action=TicketAuditLog.Action.TYPE_CHANGED,
                    description=f"{old_type} ← {ticket.ticket_type}",
                )

            if old_subject != ticket.subject:
                TicketAuditLog.objects.create(
                    ticket=ticket,
                    actor=request.user,
                    action=TicketAuditLog.Action.SUBJECT_CHANGED,
                    description=f"{old_subject or '-'} ← {ticket.subject or '-'}",
                )

            if old_status != ticket.status:
                TicketAuditLog.objects.create(
                    ticket=ticket,
                    actor=request.user,
                    action=TicketAuditLog.Action.STATUS_CHANGED,
                    description=f"{old_status} ← {ticket.status}",
                )

        messages.success(
            request,
            "اطلاعات تیکت و تاریخچه تغییرات ثبت شد.",
        )
    else:
        messages.error(
            request,
            "اطلاعات تیکت معتبر نیست.",
        )

    return redirect(
        "ticketing:detail",
        pk=pk,
    )
