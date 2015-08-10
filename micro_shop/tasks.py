# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import tablib
from celery import task
from celery.utils.log import get_task_logger

from .models import Shop

logger = get_task_logger(__name__)


@task(name='notifications.import_mailing')
def import_mailing_from_file(instance):
    shop = Shop.objects.get(instance.shop)
   # shop.update_rating()

def import_mailing_from_file_without_celery(instance):
    file = instance.file.file.read()
    data = tablib.import_set(file)
    data.headers = ['First Name', 'Last Name', 'Full Name']
    raise EnvironmentError(data.json)
    pass


def import_shop_order_from_file(file):
    pass

from import_export.admin import ImportMixin
from import_export.resources import (
    modelresource_factory,
    )
from import_export.formats import base_formats