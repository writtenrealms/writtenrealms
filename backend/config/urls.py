from django.contrib import admin
from django.urls import path, re_path, include
from rest_framework.urlpatterns import format_suffix_patterns

from builders import views as builder_views
from lobby import views as lobby_views
from spawns import views as spawn_views
from system import views as system_views
from users import views as user_views
from worlds import views as world_views


# /api/v1/ non-router URLs
api_v1_urls = [

    path('user/', user_views.LoggedInUserDetail.as_view(), name='logged-in-user'),
    path('user/<pk>/', user_views.user_detail, name='user-detail'),
    path('auth/login/', user_views.request_login_link, name='login-link-request'),
    path('auth/refresh/', user_views.refresh_jwt_token, name='jwt-refresh-token'),
    path('auth/email/request/', user_views.request_login_link, name='email-login-request'),
    path('auth/email/confirm/', user_views.confirm_login_link, name='email-login-confirm'),
    path('auth/signup/', user_views.signup, name='signup'),
    path('auth/save/', user_views.save, name='save-user'),
    path('auth/forgotpassword/', user_views.forgot_password, name='forgot-password'),
    path('auth/resetpassword/', user_views.reset_password, name='reset-password'),
    path('auth/confirmemail/', user_views.confirm_email, name='confirm-email'),
    path('auth/resendconfirmation/', user_views.resend_confirmation, name='resend-confirmation'),
    path('auth/acceptcodeofconduct/', user_views.accept_cod, name='accept_cod'),
    path('auth/google/login/', user_views.google_login, name='google-login'),
    path('auth/google/save/', user_views.google_save, name='google-save'),

    path('users/patrons/<tier>/', user_views.patrons, name='users-patrons'),
    path('users/patrons/', user_views.patrons, name='users-patrons'),

    # Staff
    path('staff/panel/', system_views.staff_panel, name='staff_panel'),
    path('staff/init/', system_views.staff_init, name='staff_init'),
    path('staff/teardown/', system_views.staff_teardown, name='staff_teardown'),
    path('staff/worlds/', system_views.RootWorlds.as_view(), name='staff-worlds'),
    path('staff/playerevents/', system_views.PlayerEvents.as_view(), name='staff-player-events'),
    path('staff/playing/', system_views.Playing.as_view(), name='staff-playing'),
    path('staff/signups/', system_views.SignUps.as_view(), name='staff-signups'),
    path('staff/activity/', system_views.Activity.as_view(), name='staff-activity'),
    path('staff/users/<user_pk>/', system_views.UserInfo.as_view(), name='staff-users'),
    path('staff/users/<user_pk>/invalidate/', system_views.invalidate_email, name='staff-invalidate-email'),
    path('staff/reviews/', system_views.Reviews.as_view(), name='staff-reviews'),
    path('staff/reviews/<pk>/', system_views.staff_review_detail, name='staff-reviews'),
    path('staff/search/', system_views.staff_search, name='staff-search'),
    path('staff/nexus/<pk>/', system_views.nexus_details, name='staff-nexus-details'),
    path('staff/nexus/<pk>/data/', system_views.NexusData.as_view(), name='staff-nexus-data'),

    # Lobby

    path('lobby/', lobby_views.Lobby.as_view(), name='lobby'),
    path('lobby/homedata/', lobby_views.HomeData.as_view(), name='lobby-home-data'),
    path('lobby/chars/recent/', lobby_views.RecentChars.as_view(), name='chars-recent'),
    path('lobby/worlds/featured/', lobby_views.FeaturedWorlds.as_view(), name='lobby-worlds-featured'),
    path('lobby/worlds/discover/', lobby_views.DiscoverWorlds.as_view(), name='lobby-worlds-discover'),
    path('lobby/worlds/user/', lobby_views.UserWorlds.as_view(), name='lobby-worlds-user'),
    path('lobby/worlds/all/', lobby_views.AllWorlds.as_view(), name='lobby-worlds-all'),
    path('lobby/worlds/playing/', lobby_views.PlayingWorlds.as_view(), name='lobby-worlds-playing'),
    path('lobby/worlds/building/', lobby_views.BuildingWorlds.as_view(), name='lobby-worlds-building'),
    path('lobby/worlds/reviewed/', lobby_views.ReviewedWorlds.as_view(), name='lobby-worlds-reviewed'),
    path('lobby/worlds/published/', lobby_views.PublishedWorlds.as_view(), name='lobby-worlds-published'),
    path('lobby/worlds/public/', lobby_views.PublicWorlds.as_view(), name='lobby-worlds-public'),
    path('lobby/worlds/dev/', lobby_views.InDevelopmentWorlds.as_view(), name='lobby-worlds-dev'),
    path('lobby/worlds/intro/', lobby_views.IntroWorlds.as_view(), name='lobby-worlds-intro'),
    path('lobby/worlds/online/', lobby_views.OnlineWorlds.as_view(), name='lobby-worlds-online'),
    path('lobby/worlds/search/', lobby_views.SearchWorlds.as_view(), name='lobby-worlds-search'),
    path('lobby/worlds/1/uniques/', lobby_views.EdeusUniques.as_view(), name='lobby-worlds-edeus-uniques'),
    path('lobby/worlds/<pk>/', lobby_views.WorldDetail.as_view(), name='lobby-world-detail'),
    path('lobby/worlds/<pk>/chars/', lobby_views.world_chars, name='lobby-world-chars'),
    path('lobby/worlds/<world_pk>/chars/<pk>/', lobby_views.world_char, name='lobby-world-char'),
    path('lobby/worlds/<pk>/leaders/', lobby_views.world_leaders, name='lobby-world-leaders'),
    path('lobby/worlds/<world_pk>/transfer/', lobby_views.transfer, name='lobby-world-transfer'),

    # Game calls made by a player either in game or in lobby
    path('game/enter/', spawn_views.EnterGame.as_view(), name='enter-game'),
    path('game/play/', spawn_views.PlayGame.as_view(), name='game-play'),
    path('game/lookup/<key>/', spawn_views.Lookup.as_view(), name='game-lookup'),
    path('game/enquiredquests/', spawn_views.EnquiredQuests.as_view(), name='enquired-quests'),
    path('game/quests/open/', spawn_views.OpenQuests.as_view(), name='quests-open'),
    path('game/quests/repeatable/', spawn_views.RepeatableQuests.as_view(), name='quests-repetable'),
    path('game/quests/completed/', spawn_views.CompletedQuests.as_view(), name='quests-complete'),
    path('game/player/config/', spawn_views.PlayerConfigView.as_view(), name='game-player-config'),

    # Calls which should only be made by the game engine itself
    path('game/system/run_loaders/', system_views.RunLoaders.as_view(), name='run-loaders'),
    path('game/system/rewards/<reward_pk>/', system_views.SpawnRewards.as_view(), name='spawn-rewards'),
    path('game/system/load/', system_views.LoadTemplate.as_view(), name='load-template'),
    path('game/system/generate/drops/', system_views.GenerateDrops.as_view(), name='generate-drops'),
    #path('game/system/extract/', system_views.Extract.as_view()),
    path('game/system/quit/', system_views.Quit.as_view(), name='system-quit'),
    path('game/system/complete/', system_views.Complete.as_view()),
    path('game/system/completequest/', system_views.CompleteQuest.as_view()),
    path('game/system/enquirequest/', system_views.EnquireQuest.as_view(), name='enquire-quest'),
    path('game/system/update_merchants/', system_views.UpdateMerchants.as_view(), name='update-merchants'),
    path('game/system/reset/<pk>/', system_views.Reset.as_view(), name='reset-world'),
    path('game/system/stop/', system_views.ShutdownWorld.as_view(), name='stop-world'),
    path('game/system/whois/<world_id>/<name>/', system_views.Whois.as_view(), name='whois'),
    path('game/system/lease/', system_views.lease_sign, name='game-lease-sign'),
    path('game/system/upgrade/', system_views.upgrade_item, name='game-upgrade-item'),
    path('game/system/craft/', system_views.craft_item, name='game-craft-item'),
    path('game/system/toggle/', system_views.toggle_room, name='game-toggle-room'),
    path('game/system/instance/enter/', system_views.enter_instance, name='game-instance-enter'),
    path('game/system/instance/leave/', system_views.leave_instance, name='game-instance-leave'),
    path('game/system/label/<item_pk>/', system_views.label_item, name='label-item'),
    path('game/system/ban/', system_views.Ban.as_view(), name='ban-player'),
    path('game/system/mute/', system_views.Mute.as_view(), name='mute-player'),
    path('game/system/nochat/', system_views.Nochat.as_view(), name='no-chat-player'),
    path('game/system/gban/', system_views.GlobalBan.as_view(), name='global-ban-player'),
    path('game/system/gmute/', system_views.GlobalMute.as_view(), name='global-mute-player'),
    path('game/system/gnochat/', system_views.GlobalNochat.as_view(), name='global-no-chat-player'),
    path('game/system/cregister/', system_views.register_clan, name='game-cregister'),
    path('game/system/cpassword/', system_views.clan_set_password, name='game-cpassword'),
    path('game/system/cjoin/', system_views.join_clan, name='game-cjoin'),
    path('game/system/cquit/', system_views.quit_clan, name='game-cquit'),
    path('game/system/cpromote/', system_views.clan_promote, name='game-cpromote'),
    path('game/system/ckick/', system_views.clan_kick, name='game-ckick'),
    path('game/system/cmembers/', system_views.clan_members, name='game-cmembers'),


    # General endpoints
    path('worlds/<pk>/', world_views.world_detail, name='world_delete'),

    # ==== Builder endpoints ====

    # Worlds
    path('builder/worlds/', builder_views.world_list, name='builder-world-list'),
    path('builder/worlds/<pk>/', builder_views.world_detail, name='builder-world-detail'),
    path('builder/worlds/<pk>/config/', builder_views.world_config, name='builder-world-config'),
    path('builder/worlds/<pk>/explore/', builder_views.world_explore, name='builder-world-explore'),
    path('builder/worlds/<pk>/map/', builder_views.world_map, name='builder-world-map'),
    path('builder/worlds/<world_pk>/manifests/apply/', builder_views.world_manifest_apply, name='builder-world-manifest-apply'),
    path('builder/worlds/<world_pk>/builders/', builder_views.builder_list, name='builder-builder-list'),
    path('builder/worlds/<world_pk>/builders/<pk>/', builder_views.builder_detail, name='builder-builder-detail'),
    path('builder/worlds/<world_pk>/builders/<builder_pk>/assignments/', builder_views.builder_assignment_list, name='builder-assignment-list'),
    path('builder/worlds/<world_pk>/builders/<builder_pk>/assignments/<pk>/', builder_views.builder_assignment_details, name='builder-assignment-details'),
    path('builder/worlds/<world_pk>/currencies/', builder_views.currency_list, name='builder-currency-list'),
    path('builder/worlds/<world_pk>/currencies/<pk>/', builder_views.currency_details, name='builder-currency-details'),
    path('builder/worlds/<world_id>/facts/', builder_views.FactList.as_view()),
    path('builder/worlds/<world_pk>/factschedules/', builder_views.fact_schedule_list, name='builder-fact-schedule-list'),
    path('builder/worlds/<world_pk>/factschedules/<pk>/', builder_views.fact_schedule_details, name='builder-fact-schedule-details'),
    path('builder/worlds/<world_pk>/skills/', builder_views.skill_list, name='builder-skill-list'),
    path('builder/worlds/<world_pk>/skills/<pk>/', builder_views.skill_detail, name='builder-skill-detail'),
    path('builder/worlds/<world_pk>/instances/', builder_views.instance_list, name='builder-world-instance-list'),
    path('builder/worlds/<world_pk>/reviews/', builder_views.review_list, name='builder-review-list'),
    path('builder/worlds/<world_pk>/reviews/<pk>/', builder_views.review_detail, name='builder-review-detail'),
    path('builder/worlds/<world_pk>/reviews/<pk>/claim/', builder_views.WorldReviewViewSet.as_view({'post': 'claim_review'}), name='builder-review-detail-claim'),
    path('builder/worlds/<world_pk>/reviews/<pk>/resolve/', builder_views.WorldReviewViewSet.as_view({'post': 'resolve_review'}), name='builder-review-detail-resolve'),
    path('builder/worlds/<world_pk>/socials/', builder_views.social_list, name='builder-social-list'),
    path('builder/worlds/<world_pk>/socials/<pk>/', builder_views.social_details, name='builder-social-details'),


    # World admin
    path('builder/worlds/<pk>/admin/', builder_views.world_admin, name='builder-world-admin'),
    path('builder/worlds/<world_pk>/admin/instance/<pk>/', builder_views.world_admin_instance, name='builder-world-admin-instance'),


    # Zones
    path('builder/worlds/<world_pk>/zones/', builder_views.zone_list, name='builder-zone-list'),
    path('builder/worlds/<world_pk>/zones/<pk>/', builder_views.zone_detail, name='builder-zone-detail'),
    path('builder/worlds/<world_pk>/zones/<pk>/rooms/', builder_views.zone_room_list, name='builder-zone-room-list'),
    path('builder/worlds/<world_pk>/zones/<pk>/paths/', builder_views.zone_path_list, name='builder-zone-path-list'),
    path('builder/worlds/<world_pk>/zones/<pk>/map/', builder_views.zone_map, name='builder-zone-map'),
    path('builder/worlds/<world_pk>/zones/<pk>/loaders/', builder_views.zone_loaders, name='builder-zone-loaders'),
    path('builder/worlds/<world_pk>/zones/<pk>/quests/', builder_views.zone_quest_list, name='builder-zone-quest_list'),
    path('builder/worlds/<world_pk>/zones/<pk>/loads/', builder_views.zone_loads, name='builder-zone-loads'),
    path('builder/worlds/<world_pk>/zones/<pk>/move/', builder_views.zone_move, name='builder-zone-move'),
    # Processions
    path('builder/worlds/<world_pk>/zones/<zone_pk>/processions/', builder_views.procession_list, name='builder-procession-list'),
    path('builder/worlds/<world_pk>/zones/<zone_pk>/processions/<pk>/', builder_views.procession_detail, name='builder-procession-detail'),

    # Rooms
    path('builder/worlds/<world_pk>/rooms/', builder_views.room_list, name='builder-room-list'),
    path('builder/worlds/<world_pk>/rooms/<pk>/', builder_views.room_detail, name='builder-room-detail'),
    path('builder/worlds/<world_pk>/rooms/<pk>/legacy/', builder_views.room_detail_legacy, name='builder-room-detail-legacy'),
    path('builder/worlds/<world_pk>/rooms/<pk>/dir_action/', builder_views.room_dir_action, name='builder-room-action'),
    path('builder/worlds/<world_pk>/rooms/<pk>/last_viewed/', builder_views.room_mark_last_viewed, name='builder-room-mark-last-viewed'),
    path('builder/worlds/<world_pk>/rooms/<pk>/config/', builder_views.room_config, name='builder-room-config'),
    path('builder/worlds/<world_pk>/rooms/<pk>/flags/', builder_views.room_flag_list, name='builder-room-flags'),
    path('builder/worlds/<world_pk>/rooms/<pk>/flags/<code>/', builder_views.room_flag_toggle, name='builder-room-flag-toggle'),
    path('builder/worlds/<world_pk>/rooms/<room_pk>/loads/', builder_views.room_loads, name='builder-room-loads'),
    path('builder/worlds/<world_pk>/rooms/<room_pk>/checks/', builder_views.room_checks, name='builder-room-checks'),
    path('builder/worlds/<world_pk>/rooms/<room_pk>/checks/<pk>/', builder_views.room_check_detail, name='builder-room-check-detail'),
    path('builder/worlds/<world_pk>/rooms/<room_pk>/triggers/', builder_views.room_triggers, name='builder-room-trigger-list'),
    path('builder/worlds/<world_pk>/rooms/<room_pk>/actions/', builder_views.room_action_list, name='builder-room-action-list'),
    path('builder/worlds/<world_pk>/rooms/<room_pk>/actions/<pk>/', builder_views.room_action_detail, name='builder-room-action-detail'),
    path('builder/worlds/<world_pk>/rooms/<room_pk>/actions/<pk>/clone/', builder_views.room_action_clone, name='builder-room-action-clone'),
    path('builder/worlds/<world_pk>/rooms/<room_pk>/details/', builder_views.room_detail_list, name='builder-room-detail-list'),
    path('builder/worlds/<world_pk>/rooms/<room_pk>/details/<pk>/', builder_views.room_detail_detail, name='builder-room-detail-detail'),
    path('builder/worlds/<world_pk>/rooms/<room_pk>/set_door/', builder_views.room_set_door, name='builder-room-set-door'),
    path('builder/worlds/<world_pk>/rooms/<room_pk>/clear_door/', builder_views.room_clear_door, name='builder-room-clear-door'),
    path('builder/worlds/<world_pk>/instancerooms/', builder_views.instance_room_list, name='builder-instance-room-list'),

    # Item Templates
    path('builder/worlds/<world_pk>/itemtemplates/', builder_views.item_template_list, name='builder-item-template-list'),
    path('builder/worlds/<world_pk>/itemtemplates/<pk>/', builder_views.item_template_detail, name='builder-item-template-detail'),
    path('builder/worlds/<world_pk>/itemtemplates/<pk>/inventory/', builder_views.item_template_inventory, name='builder-item-template-inventory'),
    path('builder/worlds/<world_pk>/itemtemplates/<item_template_pk>/inventory/<pk>/', builder_views.item_template_inventory_detail, name='builder-item-template-inventory-detail'),
    path('builder/worlds/<world_pk>/itemtemplates/<item_template_pk>/loadsin/', builder_views.item_template_loadsin, name='builder-item-template-loadsin'),
    path('builder/worlds/<world_pk>/itemtemplates/<pk>/quests/', builder_views.item_template_quests, name='builder-item-template-quests'),
    path('builder/worlds/<world_pk>/itemtemplates/<item_template_pk>/actions/', builder_views.item_action_list, name='builder-item-action-list'),
    path('builder/worlds/<world_pk>/itemtemplates/<item_template_pk>/actions/<pk>/', builder_views.item_action_detail, name='builder-item-action-detail'),
    path('builder/worlds/<world_pk>/itemtemplates/<item_template_pk>/actions/<pk>/clone/', builder_views.item_action_clone, name='builder-item-action-clone'),

    # Mob Templates
    path('builder/worlds/<world_pk>/mobtemplates/', builder_views.mob_template_list, name='builder-mob-template-list'),
    path('builder/worlds/<world_pk>/mobtemplates/<pk>/', builder_views.mob_template_detail, name='builder-mob-template-detail'),
    path('builder/worlds/<world_pk>/mobtemplates/<pk>/inventory/', builder_views.mob_template_inventory, name='builder-mob-template-inventory'),
    path('builder/worlds/<world_pk>/mobtemplates/<mob_template_pk>/inventory/<pk>/', builder_views.mob_template_inventory_detail, name='builder-mob-template-inventory-detail'),
    path('builder/worlds/<world_pk>/mobtemplates/<mob_template_pk>/merchantinventory/', builder_views.mob_template_merchant_inventory_list, name='builder-mob-template-merchant-inventory-list'),
    path('builder/worlds/<world_pk>/mobtemplates/<mob_template_pk>/merchantinventory/<pk>/', builder_views.mob_template_merchant_inventory_detail, name='builder-mob-template-merchant-inventory-detail'),
    path('builder/worlds/<world_pk>/mobtemplates/<pk>/reactions/', builder_views.mob_template_reactions, name='builder-mob-template-reactions'),
    path('builder/worlds/<world_pk>/mobtemplates/<mob_template_pk>/reactions/<pk>/', builder_views.mob_template_reaction_detail, name='builder-mob-template-reaction-detail'),
    path('builder/worlds/<world_pk>/mobtemplates/<mob_template_pk>/loadsin/', builder_views.mob_template_loadsin, name='builder-mob-template-loadsin'),
    path('builder/worlds/<world_pk>/mobtemplates/<pk>/factions/', builder_views.mob_template_factions, name='builder-mob-template-factions'),
    path('builder/worlds/<world_pk>/mobtemplates/<pk>/quests/', builder_views.mob_template_quests, name='builder-mob-template-quests'),
    path('builder/worlds/<world_pk>/mobtemplates/<mob_template_pk>/factions/<pk>/', builder_views.mob_template_faction_detail, name='builder-mob-template-faction-detail'),

    # Loaders
    path('builder/worlds/<world_pk>/loaders/', builder_views.loader_list, name='builder-loader-list'),
    path('builder/worlds/<world_pk>/loaders/<pk>/', builder_views.loader_detail, name='builder-loader-detail'),

    # Rules
    path('builder/worlds/<world_pk>/loaders/<loader_pk>/rules/', builder_views.rule_list, name='builder-loader-rule-list'),
    path('builder/worlds/<world_pk>/loaders/<loader_pk>/rules/<pk>/', builder_views.rule_detail, name='builder-loader-rule-detail'),

    # Quests
    path('builder/worlds/<world_pk>/quests/', builder_views.quest_list, name='builder-quest-list'),
    path('builder/worlds/<world_pk>/quests/<pk>/', builder_views.quest_detail, name='builder-quest-detail'),
    path('builder/worlds/<world_pk>/quests/<pk>/objectives/', builder_views.objective_list, name='builder-objective-list'),
    path('builder/worlds/<world_pk>/objectives/<pk>/', builder_views.objective_detail, name='builder-objective-detail'),
    path('builder/worlds/<world_pk>/quests/<pk>/rewards/', builder_views.reward_list, name='builder-reward-list'),
    path('builder/worlds/<world_pk>/rewards/<pk>/', builder_views.reward_detail, name='builder-reward-detail'),

    # World Config
    path('builder/worlds/<world_pk>/randomitemprofiles/', builder_views.random_item_profile_list, name='builder-random-item-profile-list'),
    path('builder/worlds/<world_pk>/randomitemprofiles/<pk>/', builder_views.random_item_profile_detail, name='builder-random-item-profile-detail'),
    path('builder/worlds/<world_pk>/transformationtemplates/', builder_views.transformation_template_list, name='builder-transformation-template-transformation-list'),
    path('builder/worlds/<world_pk>/transformationtemplates/<pk>/', builder_views.transformation_template_detail, name='builder-transformation-template-detail'),
    path('builder/worlds/<world_pk>/startingeq/', builder_views.starting_eq_list, name='builder-starting-eq-list'),
    path('builder/worlds/<world_pk>/startingeq/<pk>/', builder_views.starting_eq_detail, name='builder-starting-eq-detail'),

    # Paths
    path('builder/worlds/<world_pk>/paths/<pk>/', builder_views.path_detail, name='builder-path-details'),
    path('builder/worlds/<world_pk>/paths/<pk>/rooms/', builder_views.path_rooms, name='builder-path-rooms'),
    path('builder/worlds/<world_pk>/paths/<path_pk>/rooms/<pk>/', builder_views.path_room_detail, name='builder-path-room-detail'),

    # Factions
    path('builder/worlds/<pk>/factions/', builder_views.world_factions, name='builder-world-factions'),
    path('builder/worlds/<world_pk>/factions/<pk>/', builder_views.world_faction_detail, name='builder-world-faction-detail'),
    path('builder/worlds/<world_pk>/factions/<faction_pk>/ranks/', builder_views.world_faction_rank_list, name='builder-world-faction-rank-list'),
    path('builder/worlds/<world_pk>/factions/<faction_pk>/ranks/<pk>/', builder_views.world_faction_rank_detail, name='builder-world-faction-rank-detail'),

    # Players (builder)
    path('builder/worlds/<world_pk>/players/', builder_views.player_list, name='builder-player-list'),
    path('builder/worlds/<world_pk>/players/<pk>/', builder_views.player_detail, name='builder-player-detail'),
    path('builder/worlds/<world_pk>/players/<pk>/reset/', builder_views.player_reset, name='builder-player-reset'),
    path('builder/worlds/<world_pk>/players/<pk>/restore/', builder_views.player_restore, name='builder-player-restore'),
    path('builder/worlds/<world_pk>/players/<player_pk>/restore/<pk>/', builder_views.player_restore_item, name='builder-player-restore-item'),

    # Special endpoints
    path('builder/worlds/<world_key>/reflookup/', builder_views.RefLookup.as_view(), name='builder-ref-lookup'),
    path('builder/suggest/mob/', builder_views.suggest_mob, name='suggest-mob'),


    path('builder/worlds/<pk>/users/', builder_views.user_list, name='builder-world-users'),

    # url(r'/api/v1/builder/worlds/([^/]+)/reflookup/$', BuilderHandlers.RefLookupHandler, name='world_ref_lookup'),

    # Blog

    path('public/skills/', system_views.AllSkills.as_view(), name='all-skills'),
    path('public/skills/<archetype>/', system_views.ArchetypeSkills.as_view(), name='archetype-skills'),

]

urlpatterns = [
    path('api-auth/', include('rest_framework.urls')),
    path('admin/', admin.site.urls),
    path('api/v1/', include(api_v1_urls)),
]

# Apply format suffix patterns to all URLs except those from included apps
urlpatterns = format_suffix_patterns(urlpatterns)

"""
Thinking through the proper routing for interacting with this API. There are
three basic views for a world:
* What someone just browsing around in lobby sees, which cannot expose
  anything they would find out by exploring
* What someone who is the builder for a world sees
* What someone who has one or more character sees, which could be perhaps
  the rooms they've discovered

One way to do it:
/worlds/:id/ <-- what a builder interacts with
/lobby/worlds/:id/ <-- what someone who either has characters or is interested sees
"""
