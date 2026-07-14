import hashlib

from core.utils import CamelCase__to__camel_case

from django.conf import settings
from django.db import connections, models, router, transaction
from django.db.models import Case, Max, When


optional = dict(blank=True, null=True)


class BaseModel(models.Model):
    created_ts = models.DateTimeField(auto_now_add=True, db_index=True)
    modified_ts = models.DateTimeField(auto_now=True, db_index=True)

    class Meta:
        abstract = True
        ordering = ['created_ts']


class AdventBaseModel(BaseModel):

    class Meta(BaseModel.Meta):
        abstract = True

    @classmethod
    def get_class_name(cls):
        return cls.__name__.lower()

    @property
    def key(self):
        classname = CamelCase__to__camel_case(self.__class__.__name__)
        if classname == 'equipment':
            classname = 'char_equipment'
        return '%s.%s' % (classname, self.id)

    @property
    def model_type(self):
        return self.__class__.__name__.lower()

    def __str__(self):
        cls_name = self.get_class_name()
        try:
            return "%s - %s" % (self.id, self.name)
        except AttributeError:
            return str(self.id)

    def get_game_key(self, spawn_world=None):
        if spawn_world is None:
            if self.world.is_multiplayer:
                spawn_world = self.world.spawned_worlds.get(
                    is_multiplayer=True)
            else:
                raise ValueError('spawn_world is required.')
        model_name = CamelCase__to__camel_case(self.__class__.__name__)

        return '@{world_id}:{model}.{id}'.format(
            world_id=spawn_world.pk,
            #model=self.get_class_name(),
            model=model_name,
            id=self.id)


class AdventWorldBaseModel(AdventBaseModel):

    relative_id = models.IntegerField(**optional)

    class Meta(AdventBaseModel.Meta):
        abstract = True
        unique_together = ['world', 'relative_id']

    @classmethod
    def _relative_id_lock_key(cls, world_id):
        lock_scope = f'{cls._meta.label_lower}:{world_id}'.encode('utf-8')
        lock_digest = hashlib.blake2b(lock_scope, digest_size=8).digest()
        return int.from_bytes(lock_digest, byteorder='big', signed=True)

    def save(self, *args, **kwargs):
        needs_relative_id = (
            self._state.adding
            and self.relative_id is None
            and self.world_id is not None
        )
        if not needs_relative_id:
            return super().save(*args, **kwargs)

        using = kwargs.get('using') or router.db_for_write(
            self.__class__,
            instance=self,
        )
        connection = connections[using]

        with transaction.atomic(using=using):
            # Serialize allocation only for this model and world. This keeps
            # concurrent builders from selecting the same next value without
            # introducing a global lock across unrelated worlds or models.
            if connection.vendor == 'postgresql':
                with connection.cursor() as cursor:
                    cursor.execute(
                        'SELECT pg_advisory_xact_lock(%s)',
                        [self._relative_id_lock_key(self.world_id)],
                    )

            highest_relative_id = (
                self.__class__.objects
                .using(using)
                .filter(world_id=self.world_id)
                .aggregate(highest=Max('relative_id'))['highest']
            )
            self.relative_id = (highest_relative_id or 0) + 1
            return super().save(*args, **kwargs)

    @property
    def key(self):
        return '%s.%s' % (
            CamelCase__to__camel_case(self.__class__.__name__),
            self.relative_id)

    def get_game_key(self, spawn_world=None):
        if spawn_world is None:
            if self.world.is_multiplayer:
                spawn_world = self.world.spawned_worlds.get(
                    is_multiplayer=True)
            else:
                raise ValueError('spawn_world is required.')
        return '@{world_id}:{model}.{id}'.format(
            world_id=spawn_world.pk,
            model=self.get_class_name(),
            id=self.relative_id)


# ==== Utility Functions ====


def qs_by_pks(model_cls, pk_list, limit=None):
    if limit is not None:
        pk_list = pk_list[0:limit]

    if not pk_list:
        return model_cls.objects.none()

    preserved = Case(*[When(pk=pk, then=pos) for pos, pk in enumerate(pk_list)])
    return model_cls.objects.filter(pk__in=pk_list).order_by(preserved)


def get_qset_by_pks(model, pk_list, pk_name='id', limit=None):
    "Older reference implementation of the same concept as qs_by_pks"

    if limit is not None:
        pk_list = pk_list[0:limit]

    if not pk_list:
        return model.objects.none()

    pk_list = list(pk_list)

    clauses = ' '.join([
        "WHEN {pk_name}='{pk_value}' THEN {order}".format(
            pk_name=pk_name,
            pk_value=pk,
            order=i)
        for i, pk in enumerate(pk_list)
    ])
    ordering = 'CASE {} END'.format(clauses)

    return model.objects.filter(
        pk__in=pk_list
    ).extra(
        select={'ordering': ordering},
        order_by=('ordering',))


def list_to_choice(lst):
    return [(i, i.capitalize()) for i in lst]


def batch_deletion(qs, batch_size=10000):
    count = 0
    while True:
        count += 1
        ids = list(qs.values_list('id', flat=True)[:batch_size])
        if not ids:
            break
        print("Batch %s deletion of %s %s..." % (count, len(ids), qs.model.__name__))
        with transaction.atomic():
            qs.model.objects.filter(id__in=ids).delete()
            print("Deleted %s %s" % (len(ids), qs.model.__name__))
