from django.urls import path
from . import views

app_name = 'assignments'
urlpatterns = [
    path('', views.assignment_list, name='list'),
    path('new/', views.assignment_form, name='create'),
    path('access/', views.access_settings, name='access'),
    path('<int:pk>/', views.detail, name='detail'),
    path('<int:pk>/edit/', views.assignment_form, name='edit'),
    path('<int:pk>/review/', views.review, name='review'),
    path('<int:pk>/submit/', views.submit, name='submit'),
    path('<int:pk>/file/', views.download, name='file'),
    path('<int:pk>/responses/<int:submission_id>/file/', views.download, name='response_file'),
    path('<int:pk>/responses/<int:submission_id>/grade/', views.grade, name='grade'),
]
