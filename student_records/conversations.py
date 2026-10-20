from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404

from ticketing_module.services import can_access_ticket, visible_messages_for


def conversation_context(request, student):
    tickets = student.tickets.select_related(
        'student__user', 'student__field__grade__school__province',
        'subject', 'current_assignee_user',
    )
    allowed_ids = [ticket.pk for ticket in tickets if can_access_ticket(request.user, ticket)]
    allowed = tickets.filter(pk__in=allowed_ids).order_by('-updated_at', '-pk')
    conversations = Paginator(allowed, 8).get_page(request.GET.get('chat_page'))
    selected_id = request.GET.get('conversation', '')
    if selected_id:
        if not selected_id.isdecimal() or len(selected_id) >= 19:
            raise Http404
        selected = get_object_or_404(allowed, pk=int(selected_id))
    else:
        selected = conversations.object_list.first()
    message_page = None
    if selected:
        messages = visible_messages_for(selected, request.user).order_by('created_at', 'pk')
        paginator = Paginator(messages, 30)
        message_page = paginator.get_page(request.GET.get('message_page', paginator.num_pages))
    return {'conversations': conversations, 'selected_conversation': selected, 'conversation_messages': message_page}
