from django.urls import path
from . import views

urlpatterns = [
    path("list/", views.notifications_list, name="notifications_list"),
    path("count/", views.notifications_count, name="notifications_count"),
    path("mark_as_read/<int:notification_id>/", views.mark_as_read, name="mark_as_read"),
    path("mark_all_as_read/", views.mark_all_as_read, name="mark_all_as_read"),
]