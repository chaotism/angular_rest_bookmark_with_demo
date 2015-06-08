#coding: utf-8
#from apps.trustedservice.models import Notification as OldNotification
from django.db import models, transaction
from django.utils.translation import ugettext_lazy as _
from extended_choices import Choices
from django.contrib.contenttypes.generic import GenericForeignKey
#from picklefield.fields import PickledObjectField
from django.core.exceptions import ValidationError
from django.core.urlresolvers import reverse
from django.utils.encoding import smart_str
from jsonfield.fields import JSONField
from django.contrib.auth.models import User
from django.conf import settings


class Notification(models.Model):
    """
    Уведомления всякие разные, разнообразные
    """

    NOTIFICATION_TYPES = Choices(
        ('INVITE_TO_POLL', 'poll-invite', u"Приглашаем проголосовать за магазин"),
        ('INVITE_TO_REVOTE', 'poll-invite-revote', u"Приглашаем переголосовать оставленный отзыв"),
        ('FOR_USER_REPLY_POLL', 'merchant-look-comment', u"Магазин оставил комментарий к вашему отзыву"),
        ('FOR_MERCHANT_REPLY_POLL', 'user-look-comment', u"Пользователь оставил ответ к вашему комментарию"),
        ('FOR_MERCHANT_SHOP_VOTE', 'merchant-shop-vote', u"Пользователь проголосовал за ваш магазин"),
        ('THANKS_FOR_SHOP_VOTE', 'thanks-for-shop-vote', u"Спасибо за то что проголосовали за магазин"),
    )

    shop = models.ForeignKey("Shop", null=True, blank=True, related_name="rel_notifications")

    from_user = models.ForeignKey("auth.User", null=True, blank=True, related_name="sended_notifications")
    to_user = models.ForeignKey("auth.User", db_index=True, null=True, blank=True, related_name="notifications")

    email = models.EmailField(null=True, blank=True, db_index=True)

    created_at = models.DateTimeField(_(u'создано'),
        auto_now_add=True, editable=False)

    content_type_id = models.ForeignKey("contenttypes.ContentType", null=True, blank=True)
    object_id = models.PositiveIntegerField(null=True, blank=True)

    content_object = GenericForeignKey('content_type_id', 'object_id')

    notification_type = models.CharField(max_length=42, choices=NOTIFICATION_TYPES)

    title = models.CharField(max_length=256)
    text = models.TextField(max_length=1024, blank=True)

    context = JSONField(default={}, blank=True, verbose_name=u"Контекст для емайла", editable=False)

    is_read = models.BooleanField(default=False, db_index=True)

    class Meta:
        verbose_name = _(u"Оповещение о событии")
        verbose_name_plural = _(u"Оповещения о событиях")
        ordering = ['-created_at']

    def __unicode__(self):
        return u"To: {self.email} From: {self.from_user.email} Subject: {self.title}".format(self=self)

    def save(self, *args, **kwargs):
        if not self.pk and not self.text:
            data = self.render()
            self.title = data['title']
            self.text = data['text']
        super(Notification, self).save(*args, **kwargs)

    @staticmethod
    def get_template_names(notification_type, shop=None):
        templates = ["notification/{}.html".format(notification_type)]

        if shop:
            slug = shop.slug.lower()
            shop_template = "notification/shop/{}/{}.html".format(smart_str(slug), notification_type)
            templates.insert(0, shop_template)

        return templates

    def get_context(self):
        context = {
            "from_user": self.from_user,
            "to_user": self.to_user,
            "object": self.content_object,
        }

        context.update(self.context or {})
        return context

    def send_email(self, priority='now', commit=True):
        context = self.get_context()
        if not any([self.text, self.title]):
            context.update(self.render(context))
        else:
            context['text'] = self.text
            context['title'] = self.title

        return mail_send(
            templates=self.get_template_names(self.notification_type, self.shop),
            recipients=[self.email],
            context=context,
            priority=priority,
            commit=commit
        )

    def render(self, context=None):
        context = context or self.get_context()
        template_names = Notification.get_template_names(self.notification_type, self.shop)
        blocks = render_template_by_blocks(template_names=template_names,
            context=context
        )
        blocks['text'] = blocks.pop('content', '')
        return blocks

    @staticmethod
    @transaction.atomic()
    def create_notification(notification_type,
                            email,
                            content_object=None,
                            to_user=None,
                            from_user=None,
                            context=None,
                            shop=None,
                            save=True,
                            send_email=True, **kwargs):

        # if content_object and not content_object.id:
        #     content_object = None

        if not to_user and email:
            try:
                to_user = User.objects.filter(email=email)[0]
            except IndexError:
                pass

        notification = Notification(to_user=to_user,
            email=email,
            from_user=from_user,
            notification_type=notification_type,
            context=context or {},
            shop=shop,
        )
        if content_object:
            notification.content_object = content_object

        if save:
            notification.save()

        # TODO возможно, тут должны быть подписки
        if send_email and email:
            priority = kwargs.get('priority', 'now')
            # Емейла может и не быть
            notification.send_email(priority=priority)
        return notification

    @classmethod
    def poll_invite(cls, email, shop_order, revote_poll_id=None, **kwargs):
        from weeny.utils import generate_url

        data = shop_order.get_poll_data(email,
            revote_poll_id=revote_poll_id)

        path = "%s%s?%s" % (settings.SITE_URL, reverse('email_poll'), urllib.urlencode(data))
        shorten_path = "%s%s" % (settings.SITE_URL, generate_url(path))

        if revote_poll_id and not email:
            # Голос полностью анонимен, не можем кинуть
            return None

        context = {}
        context['shop'] = shop_order.shop
        context['order'] = shop_order
        context['email_poll_url'] = shorten_path
        context['revote_poll_id'] = revote_poll_id

        notification_type = cls.NOTIFICATION_TYPES.INVITE_TO_POLL
        if revote_poll_id:
            notification_type = cls.NOTIFICATION_TYPES.INVITE_TO_REVOTE

        kw = dict(
            from_user=shop_order.shop.user,
            email=email,
            content_object=shop_order,
            context=context,
            shop=shop_order.shop,
        )

        kw.update(kwargs)

        return cls.create_notification(
            notification_type=notification_type,
            **kw
        )

    @classmethod
    def shop_vote(cls, poll, **kwargs):
        to_user = poll.order.shop.user
        from_user = None

        if poll.email:
            try:
                from_user = User.objects.filter(email=poll.email)[0]
            except IndexError:
                pass

        kw = {
            'email': to_user.email,
            'from_user': from_user,
            'to_user': to_user,
            'content_object': poll,
            'shop': poll.order.shop,
        }
        kw.update(kwargs)

        return cls.create_notification(
            cls.NOTIFICATION_TYPES.FOR_MERCHANT_SHOP_VOTE,
            **kw
        )

    @classmethod
    def thanks_for_shop_vote(cls, poll, **kwargs):

        email = poll.email or poll.order.email
        if not email:
            # Голос оказался полностью анонимным, мы не можем кинуть нотификацию
            return None

        from_user = poll.order.shop.user

        kw = dict(
            from_user=from_user,
            email=email,
            content_object=poll,
            shop=poll.order.shop,
        )
        kw.update(kwargs)

        return cls.create_notification(
            cls.NOTIFICATION_TYPES.THANKS_FOR_SHOP_VOTE,
            **kw
        )

    @classmethod
    def reply_poll(cls, reply, **kwargs):
        context = {}
        shop = reply.poll.order.shop
        context['reply'] = reply
        context['shop'] = shop

        kw = dict(
            from_user=reply.user,
            content_object=reply,
            context=context,
            shop=shop,
        )

        if reply.is_shop():
            kw['email'] = reply.poll.email or reply.poll.order.email
            notification_type = cls.NOTIFICATION_TYPES.FOR_USER_REPLY_POLL
            if not kw['email']:
                # Голос оказался полностью анонимным, мы не можем кинуть нотификацию
                return None
        else:
            kw['to_user'] = shop.user
            kw['email'] = kw['to_user'].email
            notification_type = cls.NOTIFICATION_TYPES.FOR_MERCHANT_REPLY_POLL

        kw.update(kwargs)

        return cls.create_notification(
            notification_type=notification_type,
            **kw
        )


class EmailMeta(models.Model):
    shop = models.ForeignKey('Shop', null=True, blank=True)
    is_read = models.BooleanField(default=False)
    email = models.OneToOneField('post_office.Email', related_name="meta", primary_key=True, db_column='id', parent_link=True)

class Shop(models.Model):
    name = models.CharField(verbose_name='Имя', max_length=256, blank=True, null=True)



# class Advantage(models.Model):
#     title = models.CharField(u"Заголовок", max_length=75)
#     description = models.TextField(u"Описание", max_length=300)
#     position = models.PositiveSmallIntegerField(u"Позиция", editable=False, default=0)
#
#     class Meta:
#         ordering = ['position']
#         verbose_name = u'Преимущество'
#         verbose_name_plural = u'Преимущества'
#
#     def __unicode__(self):
#         return u"{self.title}".format(self=self)
#
#     def clean(self):
#         if Advantage.objects.count() > 3:
#             raise ValidationError(u"Шаблоном сайта предусмотрен вывод только 3 преимуществ, "
#                                   u"удалите одно из существующих преимуществ или отредактируйте его текст")



# from post_office import mail
#
# mail.send(
#     'chaotism@mail.ru', # List of email addresses also accepted
#     'chaotism@mail.ru',
#     subject='My email',
#     message='Hi there!',
#     html_message='Hi <strong>there</strong>!',
# )
#
# mail.send(
#     'recipient@example.com', # List of email addresses also accepted
#     'from@example.com',
#     template='welcome_email', # Could be an EmailTemplate instance or name
#     context={'foo': 'bar'},
# )