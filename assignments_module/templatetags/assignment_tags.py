from django import template

from assignments_module.services import remaining_time

register = template.Library()
register.filter('assignment_remaining', remaining_time)
