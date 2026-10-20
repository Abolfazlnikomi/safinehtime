from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.shortcuts import redirect
from django.urls import include, path

urlpatterns = [
    path('student-records/', include('student_records.urls')),
    path('lessons/', lambda request: redirect('courses_module:course_list'), name='lessons'),
    path("admin/", admin.site.urls),
    path("ckeditor/", include("ckeditor_uploader.urls")),
    path("blog/", include("blog_module.urls")),
    path(
        "news/",
        include("news_module.urls"),
    ),
    path('dashboard/',include('dashboard_module.urls')),
    path('auth/',include('auth_module.urls')),
    path('questions/', include('questions_module.urls')),
    path('quizzes/', include('quiz_module.urls')),
    path('', include("landing_module.urls")),
    path('courses/', include('courses_module.urls', namespace='courses_module')),
    path('plans/', include('plans_module.urls', namespace='plans_module')),
    path('tickets/', include('ticketing_module.urls', namespace='ticketing')),
    path('counseling/', include('counseling_module.urls', namespace='counseling')),
    path('reports/', include('reports_module.urls', namespace='reports_module')),
    path('classes/', include('classroom_module.urls', namespace='classroom_module')),
]

if settings.DEBUG:
    urlpatterns += static(
        settings.MEDIA_URL,
        document_root=settings.MEDIA_ROOT,
    )
