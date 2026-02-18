from datetime import timedelta
import time

from django.db import transaction
from django.utils import timezone

from backend.config.exceptions import ServiceError
from config import constants
from spawns.models import Player, PlayerEvent
from system.models import SiteControl, IPBan
from users.models import User
from worlds.models import World


class WorldGate:
    """
    Service class for a player entering a world.

    Usage:
        gate = WorldGate(player=player, world=world)
        gate.enter()

    Attributes:
        player (spawns.models.Player): player entering the world
        world (worlds.models.World): world or instance the player is entering
    """

    def __init__(self, player, world):
        self.player = player
        self.world = world

    def enter(self, ip=None):
        player = self.player
        world = self.world
        self.ip = ip

        if world.lifecycle in (
            constants.WORLD_LIFECYCLE_NEW,
            constants.WORLD_LIFECYCLE_STOPPED):
            from worlds.services import WorldSmith
            WorldSmith(world=world).start()

        self.preflight()

        player.in_game = True
        player.last_connection_ts = timezone.now()
        player.last_action_ts = timezone.now()
        player.save(update_fields=[
            'in_game', 'last_connection_ts', 'last_action_ts'])

        # Mark when the context world was last entered
        context = world.context if world.context else world
        context.last_entered_ts = timezone.now()
        context.save(update_fields=['last_entered_ts'])

        PlayerEvent.objects.create(
            player=player,
            event=constants.PLAYER_EVENT_LOGIN,
            ip=ip)

        return player

    def preflight(self):
        """
        Validate that a player is able to enter a world, preventing it if:
        * The e-mail is invalid
        * The user is banned
        * World is disabled
        * World is MPW and player is already logged in

        Returns:
            Boolean: Whether the world & player pass preflight checks

        Raises:
            ServiceError: Raised if preflight failed, with a reason.
        """
        player = self.player
        world = self.world

        # Site control mechanism to prevent all players from entering worlds
        try:
            site_control = SiteControl.objects.get(name='prod')
            if not player.user.is_staff and site_control.maintenance_mode:
                raise ServiceError(
                    "Unable to enter world: Written Realms is undergoing "
                    "maintenance. Please try again later.")
        except SiteControl.DoesNotExist:
            pass

        # Invalid email
        if player.user.is_invalid:
            raise ServiceError(
                "Your e-mail address is invalid. Unable to enter worlds.")

        # Banned user
        if player.user.noplay:
            raise ServiceError("You have been banned from entering worlds.")

        # Banner player
        if player.noplay:
            raise ServiceError("You are banned from this world.")

        # Admin disabled world
        if world.context.no_start or world.no_start:
            raise ServiceError("World is disabled.")

        # Builder disabled world
        if not player.is_immortal and world.context.maintenance_mode:
            if world.context.maintenance_msg:
                raise ServiceError(world.context.maintenance_msg)
            raise ServiceError("World is temporarily closed.")

        # Check multicharing
        if world.is_multiplayer and not player.is_immortal:
            if self.check_multicharing():
                raise ServiceError("You are logged on another character.")

        # Add a 5 minutes cross-race delay
        # Get the last character that the user logged out on from the same
        # world.
        cross_race_cooldown = world.config.cross_race_cooldown
        if (cross_race_cooldown
            and world.is_multiplayer
            and not player.is_immortal):
            last_logout = PlayerEvent.objects.filter(
                player__user=player.user,
                player__world=world,
                event=constants.PLAYER_EVENT_LOGOUT,
            ).exclude(
                player=player
            ).order_by('-created_ts').first()
            if last_logout:
                player_assignment = player.faction_assignments.filter(
                    faction__is_core=True).first()
                last_logout_assignment = last_logout.player.faction_assignments.filter(
                    faction__is_core=True).first()
                if player_assignment and last_logout_assignment:
                    if (player_assignment.faction
                        != last_logout_assignment.faction):
                        delta = timedelta(minutes=cross_race_cooldown)
                        if (timezone.now() - last_logout.created_ts) < delta:
                            raise ServiceError(
                                "You must wait %s minutes before switching "
                                "to a character of a different core faction."
                                % cross_race_cooldown)

        # Only worlds in STORED, NEW or RUNNING states may be entered.
        if world.lifecycle != constants.WORLD_STATE_RUNNING:
            raise ServiceError("World cannot be entered in '%s' state."
                                % world.lifecycle)

        # Check for IP bans
        if self.ip:
            if IPBan.objects.filter(ip=self.ip).exists():
                raise ServiceError("Your IP address has been banned.")

        # Make sure the player is not in the process of being saved
        with transaction.atomic():
            player = Player.objects.select_for_update().get(pk=player.pk)
            if player.save_start_ts:
                raise ServiceError(
                    "Character is being saved, please try again in a minute.")

    def check_multicharing(self):
        """
        Check to see if a player is logged on other characters.

        Returns:
            List[spawns.models.Player]: The other players that the user is
                currently logged on.
        """
        player = self.player
        world = self.world
        user_ids = [player.user.id]

        # Account for players with known multiple accounts
        if player.user.link_id:
            other_account_ids = User.objects.filter(
                    link_id=player.user.link_id,
                ).exclude(
                    id=player.user.id
                ).values_list('id', flat=True)
            if other_account_ids:
                user_ids.extend(other_account_ids)

        # Check for instance conflicts
        worlds = [world]
        if world.context.instance_of:
            # Scenario where a player is entering an instance
            # so we check the base world.
            parent_spawn_world = World.objects.filter(
                context=world.context.instance_of,
                is_multiplayer=True,
            ).first()
            if parent_spawn_world:
                worlds.append(parent_spawn_world)
        else:
            # Scenario where a player is entering a base world
            # so we check its instances.
            instances = World.objects.filter(
                context__instance_of=world.context,
            )
            if instances:
                worlds.extend(instances)

        # Ideally, there would be no idle check. But since there
        # are sometimes ghost issues, we do this out of humility.
        delta = timedelta(seconds=constants.IDLE_THRESHOLD)
        idle_ts = timezone.now() - delta
        players_in_worlds = Player.objects.filter(
            world__in=worlds,
            user__in=user_ids,
            last_action_ts__gt=idle_ts,
            in_game=True,
            is_immortal=False, # Exclude builder chars
        ).exclude(id=player.id)

        return players_in_worlds

    def exit(self,
             player_data_id=None,
             transfer_to=None,
             transfer_from=None,
             ref=None,
             leave_instance=False,
             member_ids=None):
        """
        Exit a world
        """

        # Wait for the previous save job to complete if there was one
        WAIT_TIMEOUT = 60 # seconds
        WAIT_INTERVAL = 3 # seconds
        wait_start = timezone.now()
        while True:
            with transaction.atomic():
                player = Player.objects.select_for_update().get(
                    pk=self.player.pk)
                if not player.save_start_ts:
                    break
            if (timezone.now() - wait_start).total_seconds() > WAIT_TIMEOUT:
                break
            time.sleep(WAIT_INTERVAL)

        # spw / mpw specific exits
        if self.world.is_multiplayer:
            self.exit_mpw(player_data_id=player_data_id,
                          transfer_to=transfer_to,
                          transfer_from=transfer_from,
                          ref=ref,
                          leave_instance=leave_instance,
                          member_ids=member_ids)
        else:
            self.exit_spw(player_data_id=player_data_id)

        self.player.refresh_from_db()
        self.player.in_game = False
        self.player.save(update_fields=['in_game'])

        PlayerEvent.objects.create(
            player=self.player,
            event=constants.PLAYER_EVENT_LOGOUT)

    def exit_spw(self, player_data_id=None):
        from worlds.services import WorldSmith
        WorldSmith(world=self.world).stop_spw(
            player=self.player,
            player_data_id=player_data_id)

    def exit_mpw(self, player_data_id=None,
                 transfer_to=None,
                 transfer_from=None,
                 ref=None,
                 leave_instance=False,
                 member_ids=None):
        player = self.player.save_data(
            exiting=True,
            player_data_id=player_data_id)

        if player.world.context.instance_of:
            if leave_instance:
                player = World.leave_instance(player=player)

        elif transfer_to and transfer_from:
            instance = World.enter_instance(
                player=player,
                transfer_to_id=transfer_to,
                transfer_from_id=transfer_from,
                ref=ref,
                member_ids=member_ids)

        game_world = self.world.game_world
        game_player = self.player.game_player
        if not game_world or not game_player:
            return
