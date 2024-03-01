from django.urls import path
from . import views

urlpatterns = [
    path('print/', views.print, name='print'),
    path('attach/', views.attach, name='attach')
]
