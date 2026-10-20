from django.urls import path
from .views import dash_view, mark_content_notifications_read, module_settings, notification_feed, presence_status, toggle_module
from questions_module.structure import school_structure
urlpatterns = [
    path('school-structure/', school_structure, name='school_structure'),
    path('', dash_view, name="dashboard"),
    path('modules/', module_settings, name="module_settings"),
    path('notifications/read/', mark_content_notifications_read, name="notifications_read"),
    path('notifications/feed/', notification_feed, name="notifications_feed"),
    path('presence/', presence_status, name="dashboard_presence"),
    path('modules/<str:code>/toggle/', toggle_module, name="toggle_module"),
]
