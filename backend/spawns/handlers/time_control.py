from spawns.actions.base import ActionError
from spawns.handlers.base import CommandContext, CommandHandler
from spawns.handlers.registry import register_handler
from spawns import instance_time


def _error(ctx, command, exc):
    ctx.publish({'type': f'cmd.{command}.error', 'text': exc.message,
                 'data': {'code': exc.code, 'error': exc.message}})
    state = instance_time.snapshot_for_player(ctx.player)
    if state:
        ctx.publish({'type': 'instance.time_control', 'data': state})


@register_handler
class AdvanceHandler(CommandHandler):
    command_type = 'advance'
    text_commands = ('advance',)
    help = {'name': 'Advance Turn', 'format': 'advance',
            'description': 'Commit your prepared action and advance the whole instance by one turn.'}

    def handle(self, ctx: CommandContext):
        try:
            run = instance_time.controlled_run(ctx.player.world)
            if run is None:
                raise ActionError('Time control is unavailable here.', code='time_control_unavailable')
            result = instance_time.advance(
                run_id=ctx.payload.get('run_id', run.pk), player_id=ctx.player.pk,
                expected_tick=ctx.payload.get('expected_tick', run.simulation_tick),
                expected_generation=ctx.payload.get('expected_generation', run.time_generation),
                expected_pending_revision=ctx.payload.get('expected_pending_revision', run.pending_revision),
                request_id=ctx.payload.get('_request_id'),
            )
        except ActionError as exc:
            _error(ctx, self.command_type, exc)
            return
        ctx.publish_success('advance', result)


@register_handler
class TimeControlHandler(CommandHandler):
    command_type = 'time_control'
    text_commands = ('time',)
    help = {'name': 'Combat Pause', 'format': 'time [pause | resume | toggle]',
            'description': 'Pause the whole instance between combat rounds, or resume normal world timing.'}

    def handle(self, ctx):
        args = ctx.payload.get('args') or []
        pause = ctx.payload.get('pause_in_combat')
        state = instance_time.snapshot_for_player(ctx.player)
        if args:
            option = args[0].lower()
            if option not in {'pause', 'resume', 'toggle', 'on', 'off'}:
                ctx.publish_error('time_control', 'Use time pause, time resume, or time toggle.')
                return
            pause = not state['pause_in_combat'] if option == 'toggle' and state else option in {'pause', 'on'}
        if pause is None:
            if state:
                ctx.publish_success('time_control', state,
                    'Combat pauses before each round.' if state['pause_in_combat'] else 'Normal world timing is enabled.')
            else:
                ctx.publish_error('time_control', 'Time control is unavailable here.')
            return
        try:
            result = instance_time.configure(ctx.player.pk, pause_in_combat=pause,
                                             expected_generation=ctx.payload.get('expected_generation'),
                                             run_id=ctx.payload.get('run_id'))
        except ActionError as exc:
            _error(ctx, self.command_type, exc)
            return
        ctx.publish({'type': 'instance.time_control', 'data': result})
        ctx.publish_success('time_control', result,
            'Combat pauses before each round.' if result['pause_in_combat'] else 'Normal world timing is enabled.')


@register_handler
class PauseHandler(TimeControlHandler):
    command_type = 'pause'
    text_commands = ('pause',)
    help = {'name': 'Pause Combat', 'format': 'pause',
            'description': 'Wait for approval before every combat round. Exploration keeps normal timing.'}

    def handle(self, ctx):
        ctx.payload = {**ctx.payload, 'pause_in_combat': True, 'args': []}
        super().handle(ctx)


@register_handler
class ResumeHandler(TimeControlHandler):
    command_type = 'resume'
    text_commands = ('resume',)
    help = {'name': 'Resume Time', 'format': 'resume',
            'description': 'Disable combat pausing and resume the instance’s normal timing.'}

    def handle(self, ctx):
        ctx.payload = {**ctx.payload, 'pause_in_combat': False, 'args': []}
        super().handle(ctx)


@register_handler
class CancelTurnHandler(CommandHandler):
    command_type = 'cancel_turn'
    text_commands = ('cancelturn',)
    help = {'name': 'Cancel Prepared Turn', 'format': 'cancelturn',
            'description': 'Clear the action prepared for the next instance turn.'}

    def handle(self, ctx):
        try:
            result = instance_time.cancel(ctx.player.pk, run_id=ctx.payload.get('run_id'),
                expected_generation=ctx.payload.get('expected_generation'),
                expected_pending_revision=ctx.payload.get('expected_pending_revision'))
        except ActionError as exc:
            _error(ctx, self.command_type, exc)
            return
        ctx.publish({'type': 'instance.time_control', 'data': result})
        ctx.publish_success('cancel_turn', result)
