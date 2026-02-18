from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from config import constants as api_consts

from core.db import BaseModel, list_to_choice, optional


class UserManager(BaseUserManager):

    def create_user(self, email, password=None, username=None, **extra_fields):
        if not email:
            raise ValueError('Users must have an email address')
        user = self.model(
            email=self.normalize_email(email),
            username=username,
            **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, username, email, password, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        if extra_fields.get('is_staff') is not True:
            raise ValueError('Superuser must have is_staff=True.')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser must have is_superuser=True.')
        return self.create_user(
            email=email,
            password=password,
            username=username,
            **extra_fields)

    # Make the login email lookup be case insensitive
    def get_by_natural_key(self, email):
        return self.get(email__iexact=email)


class User(AbstractUser):

    username_validator = UnicodeUsernameValidator()

    username = models.CharField(
        _('username'),
        max_length=150,
        unique=True,
        null=True,
        blank=True,
        help_text=_('Required. 150 characters or fewer. Letters, digits and @/./+/-/_ only.'),
        validators=[username_validator],
        error_messages={
            'unique': _("A user with that username already exists."),
        },
    )

    first_name = models.TextField(**optional)
    last_name = models.TextField(**optional)

    email = models.EmailField(
        verbose_name='email address',
        max_length=255,
        unique=True)

    # Has google integration
    google_id = models.CharField(max_length=255, db_index=True, **optional)

    # Way to link multiple accounts that belong to the same user
    link_id = models.IntegerField(**optional)

    ip = models.TextField(**optional)

    is_builder = models.BooleanField(default=False)
    is_temporary = models.BooleanField(default=False)
    is_confirmed = models.BooleanField(default=False)
    # True when we know the e-mail address is invalid
    is_invalid = models.BooleanField(default=False)
    send_newsletter = models.BooleanField(default=False)
    use_grapevine = models.BooleanField(default=False)
    cod_accepted = models.BooleanField(default=False)

    # Moderation flags
    nochat = models.BooleanField(default=False)
    noplay = models.BooleanField(default=False)
    is_muted = models.BooleanField(default=False)

    accessibility_mode = models.BooleanField(default=False)

    # Patreon tiers
    name_recognition = models.BooleanField(default=False)
    player_housing = models.BooleanField(default=False)
    multiplayer_worlds = models.BooleanField(default=False)

    # identifier = models.CharField(max_length=40, unique=True)
    # USERNAME_FIELD = 'identifier'
    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['username']

    objects = UserManager()

    @property
    def key(self):
        return "user.%s" % self.id

    @property
    def name(self):
        return self.username or "Anonymous User %s" % self.id

    @staticmethod
    def post_user_delete(sender, **kwargs):
        kwargs['instance'].characters.all().delete()

models.signals.post_delete.connect(User.post_user_delete, User)



class UserFlag(BaseModel):
    user = models.ForeignKey('user',
                             on_delete=models.CASCADE,
                             related_name='flags')
    code = models.TextField(choices=list_to_choice(api_consts.USER_FLAGS))
    # Staff-only note
    notes = models.TextField(**optional)

    class Meta:
        unique_together = ['user', 'code']


class ResetPasswordRequest(BaseModel):

    code = models.TextField()
    user = models.ForeignKey(User,
                             on_delete=models.CASCADE,
                             related_name='reset_password_requests')


class EmailConfirmation(BaseModel):

    code = models.TextField()
    user = models.ForeignKey(User,
                             on_delete=models.CASCADE,
                             related_name='email_confirmations')


class LoginLinkRequest(BaseModel):
    user = models.ForeignKey(User,
                             on_delete=models.CASCADE,
                             related_name='login_link_requests')
    code_hash = models.CharField(max_length=64, db_index=True)
    used_ts = models.DateTimeField(**optional)
    expires_ts = models.DateTimeField(db_index=True)
