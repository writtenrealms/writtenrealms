from datetime import datetime

import boto3

from django.conf import settings


def send_email(subject, body, to_addresses):
    to_addresses = [ str(a) for a in to_addresses ]
    if settings.SEND_EMAIL:
        client = boto3.client(
            'ses',
            region_name='us-east-1',
            aws_access_key_id=settings.AWS_ACCESS_KEY,
            aws_secret_access_key=settings.AWS_SECRET_KEY)

        client.send_email(
            Destination={
                'ToAddresses': to_addresses,
            },
            Message={
                'Body': {
                    'Text': {
                        'Charset': 'UTF-8',
                        'Data': body,
                    }
                },
                'Subject': {
                    'Charset': 'UTF-8',
                    'Data': subject,
                }
            },
            Source=settings.SYSTEM_EMAIL_FROM)

        return True

    elif settings.PRINT_UNSENT_EMAIL:
        template = (
            "Emails disabled, would send:\n"
            "TO: {to}\n"
            "SUBJECT: {subject}\n"
            "BODY:\n"
            "{body}")
        print(template.format(
            to=to_addresses,
            subject=subject,
            body=body))

    return False


def send_password_reset_link(to_address, code):
    BODY = (
        "Someone (hopefully you) requested that the password for your "
        "WrittenRealms account be reset. If you initiated this request, "
        "please click the following link:\n"
        "\n"
        "%s/reset-password/%s" % (settings.SITE_BASE, code))
    return send_email(
        subject='WrittenRealms password reset',
        body=BODY,
        to_addresses=[to_address])


def send_email_confirmation(to_address, code):
    BODY = (
        "Thanks for creating your account on WrittenRealms. Please click the "
        "link below to confirm that this is your e-mail address:\n"
        "\n"
        "%s/emailconfirm/%s" % (settings.SITE_BASE, code))
    return send_email(
        subject='WrittenRealms email confirmation',
        body=BODY,
        to_addresses=[to_address])


def send_login_link(to_address, token):
    BODY = (
        "Use this link to log in to WrittenRealms:\n"
        "\n"
        "%s/login-link/%s" % (settings.SITE_BASE, token))
    return send_email(
        subject='WrittenRealms login link',
        body=BODY,
        to_addresses=[to_address])


def send_signup(user):
    pass


def send_enter_game_email(player):
    BODY = (
        "{name} (#{id}), a level {level} {archetype} has entered {world} in "
        "{room} at {ts}").format(
            id=player.id,
            name=player.name,
            level=player.level,
            archetype=player.archetype,
            world=player.world.name,
            room=player.room.name,
            ts=datetime.utcnow().isoformat())
    return send_email(
        subject='WR: {player} entered {realm}'.format(
            player=player.name,
            realm=player.world.name),
        body=BODY,
        to_addresses=settings.MONITORING_EMAILS)
