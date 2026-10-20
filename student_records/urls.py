from django.urls import path
from . import views

app_name = 'student_records'
urlpatterns = [
    path('', views.student_list, name='list'),
    path('<int:pk>/', views.detail, name='detail'),
    path('<int:pk>/reports/<int:report_id>/image/', views.report_image, name='image'),
]
