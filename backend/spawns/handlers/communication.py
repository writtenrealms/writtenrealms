"""
Communication command handlers.
"""
from spawns.actions.base import ActionError
from spawns.actions.communication import (
    ChannelAction, DirectMessageAction, EmoteAction, ListenAction,
    SayAction, TalkAction, YellAction,
)
from spawns.events import publish_events
from spawns.handlers.base import CommandContext, CommandHandler
from spawns.handlers.registry import register_handler


def _resolve_message_text(ctx: CommandContext) -> str | None:
    args = ctx.payload.get("args", [])
    # Text-command path should use parsed args, not raw command text.
    if "raw_text" in ctx.payload:
        if not args:
            return None
        return " ".join(args)

    # Structured command path can pass explicit message text.
    message = ctx.payload.get("message")
    if message is not None:
        return str(message)

    if args:
        return " ".join(args)
    text = ctx.payload.get("text")
    if text is None:
        return None
    return str(text)


@register_handler
class SayHandler(CommandHandler):
    command_type = "say"
    text_commands = ("say",)
    supported_actor_types = ("player", "mob")
    trigger_step_mode = "transactional"
    help = {
        "name": "Say",
        "format": "say <message>",
        "description": "Say something that everyone in your room can hear.",
        "examples": [
            "say Hello there.",
        ],
    }

    def handle(self, ctx: CommandContext) -> None:
        text = _resolve_message_text(ctx)

        try:
            result = SayAction().execute(ctx.actor, text)
        except ActionError as err:
            ctx.publish(
                {
                    "type": "cmd.say.error",
                    "text": err.message,
                    "data": {"error": err.message, "code": err.code, **err.data},
                }
            )
            return

        publish_events(
            result.events,
            actor_key=ctx.actor_key,
            connection_id=ctx.connection_id,
        )


@register_handler
class YellHandler(CommandHandler):
    command_type = "yell"
    text_commands = ("yell",)
    supported_actor_types = ("player", "mob")
    help = {
        "name": "Yell",
        "format": "yell <message>",
        "description": "Yell something that everyone in your zone can hear.",
        "examples": [
            "yell Come here!",
        ],
    }

    def handle(self, ctx: CommandContext) -> None:
        text = _resolve_message_text(ctx)

        try:
            result = YellAction().execute(ctx.actor, text)
        except ActionError as err:
            ctx.publish(
                {
                    "type": "cmd.yell.error",
                    "text": err.message,
                    "data": {"error": err.message, "code": err.code, **err.data},
                }
            )
            return

        publish_events(
            result.events,
            actor_key=ctx.actor_key,
            connection_id=ctx.connection_id,
        )


@register_handler
class EmoteHandler(CommandHandler):
    command_type = "emote"
    text_commands = ("emote",)
    supported_actor_types = ("player", "mob")
    trigger_step_mode = "transactional"
    help = {
        "name": "Emote",
        "format": "emote <message>",
        "description": "Display an in-room action line beginning with your name.",
        "examples": [
            "emote smiles warmly.",
        ],
    }

    def handle(self, ctx: CommandContext) -> None:
        text = _resolve_message_text(ctx)

        try:
            result = EmoteAction().execute(ctx.actor, text)
        except ActionError as err:
            ctx.publish(
                {
                    "type": "cmd.emote.error",
                    "text": err.message,
                    "data": {"error": err.message, "code": err.code, **err.data},
                }
            )
            return

        publish_events(
            result.events,
            actor_key=ctx.actor_key,
            connection_id=ctx.connection_id,
        )


@register_handler
class TalkHandler(CommandHandler):
    command_type = "talk"
    text_commands = ("talk",)
    supported_actor_types = ("player",)
    trigger_step_mode = "events_only"
    help = {
        "name": "Talk",
        "format": "talk <mob>",
        "description": "Speak directly with a mob in your room.",
        "examples": [
            "talk guard",
        ],
    }

    def handle(self, ctx: CommandContext) -> None:
        args = ctx.payload.get("args", [])
        target = ctx.payload.get("target")
        if not target and args:
            target = " ".join(args)

        try:
            result = TalkAction().execute(ctx.player, target)
        except ActionError as err:
            ctx.publish(
                {
                    "type": "cmd.talk.error",
                    "text": err.message,
                    "data": {"error": err.message, "code": err.code, **err.data},
                }
            )
            return

        publish_events(
            result.events,
            actor_key=ctx.actor_key,
            connection_id=ctx.connection_id,
        )


def _publish_communication_result(ctx: CommandContext, command: str, execute) -> None:
    try:
        result = execute()
    except ActionError as err:
        ctx.publish({"type": f"cmd.{command}.error", "text": err.message,
                     "data": {"error": err.message, "code": err.code, **err.data}})
        return
    publish_events(result.events, actor_key=ctx.actor_key, connection_id=ctx.connection_id)


@register_handler
class ChatHandler(CommandHandler):
    command_type = "chat"
    text_commands = ("chat",)
    help = {
        "name": "Chat", "format": "chat <message>",
        "description": "Speak to players listening to chat in this world.",
        "details": ["Join with 'listen chat on'. New characters do not listen to chat."],
        "examples": ["chat Hello everyone."],
    }

    def handle(self, ctx: CommandContext) -> None:
        _publish_communication_result(ctx, self.command_type, lambda: ChannelAction().execute(
            ctx.player, self.command_type, _resolve_message_text(ctx),
        ))


@register_handler
class GossipHandler(ChatHandler):
    command_type = "gossip"
    text_commands = ("gossip",)
    help = {
        "name": "Gossip", "format": "gossip <message>",
        "description": "Speak to players listening to gossip in this world.",
        "details": ["Join with 'listen gossip on'."],
        "examples": ["gossip Did anyone watch the game?"],
    }


@register_handler
class AskHandler(ChatHandler):
    command_type = "ask"
    text_commands = ("ask",)
    help = {
        "name": "Ask", "format": "ask <question>",
        "description": "Ask a question whether or not you listen to the ask channel.",
        "details": ["Listeners see a short ID after incoming questions, such as [ 3 ]. IDs reset when this world restarts.",
                    "Use 'listen ask off' to stop hearing other players' questions. Replies to your own questions still reach you.",
                    "New characters listen automatically. Use 'listen ask on' to rejoin."],
        "examples": ["ask Where can I find a merchant?"],
    }


@register_handler
class AnswerHandler(CommandHandler):
    command_type = "answer"
    text_commands = ("answer",)
    help = {
        "name": "Answer", "format": "answer [question ID] <message>",
        "description": "Answer a question for its asker and everyone listening to ask.",
        "details": ["Omit the ID to answer this world's latest retained question.",
                    "Click a question's gray ID to prepare an answer to it. Other listeners see who is being answered.",
                    "Use 'answer -- <message>' if the message begins with a number.",
                    "Questions expire after three days and cannot be answered after a world restart."],
        "examples": ["answer 3 The merchant is east of the square.", "answer Try the market."],
    }

    def handle(self, ctx: CommandContext) -> None:
        def execute():
            text = _resolve_message_text(ctx)
            question_id = ctx.payload.get("question_id")
            if "raw_text" in ctx.payload:
                args = list(ctx.payload.get("args", []))
                question_id = None
                if args and args[0] == "--":
                    args.pop(0)
                elif args and args[0].isascii() and args[0].isdigit():
                    question_id = args.pop(0)
                text = " ".join(args)
            if question_id is not None:
                raw_id = str(question_id)
                if len(raw_id) > 19 or not raw_id.isascii() or not raw_id.isdigit():
                    raise ActionError("Question IDs must be positive numbers.", code="invalid_question")
                question_id = int(raw_id)
            return ChannelAction().execute(ctx.player, "answer", text, question_id)
        _publish_communication_result(ctx, "answer", execute)


@register_handler
class ListenHandler(CommandHandler):
    command_type = "listen"
    text_commands = ("listen",)
    help = {
        "name": "Listen", "format": "listen [ask|chat|gossip] [on|off]",
        "description": "Show your subscriptions, toggle a channel, or explicitly join or leave it.",
        "examples": ["listen", "listen ask off", "listen chat on", "listen gossip"],
    }

    def handle(self, ctx: CommandContext) -> None:
        def execute():
            if "raw_text" in ctx.payload:
                args = ctx.payload.get("args", [])
                if len(args) > 2:
                    raise ActionError("Use listen [ask|chat|gossip] [on|off].", code="invalid_args")
                channel = args[0] if args else None
                mode = args[1] if len(args) > 1 else None
            else:
                channel = ctx.payload.get("channel")
                mode = ctx.payload.get("mode")
            return ListenAction().execute(ctx.player, channel, mode)
        _publish_communication_result(ctx, "listen", execute)


@register_handler
class TellHandler(CommandHandler):
    command_type = "tell"
    text_commands = ("tell",)
    help = {
        "name": "Tell", "format": "tell <player> <message>",
        "description": "Send a private message to an online player in this world.",
        "details": ["Private messages are retained for moderation for three days."],
        "examples": ["tell Ada Meet me at the market."],
    }

    def handle(self, ctx: CommandContext) -> None:
        if "raw_text" in ctx.payload:
            args = ctx.payload.get("args", [])
            target = args[0] if args else None
            text = " ".join(args[1:])
        else:
            target = ctx.payload.get("target")
            text = _resolve_message_text(ctx)
        _publish_communication_result(ctx, self.command_type, lambda: DirectMessageAction().execute(
            ctx.player, self.command_type, target, text,
        ))


@register_handler
class WhisperHandler(TellHandler):
    command_type = "whisper"
    text_commands = ("whisper",)
    help = {
        "name": "Whisper", "format": "whisper <player> <message>",
        "description": "Send a private message to a player in the same room.",
        "details": ["Private messages are retained for moderation for three days."],
        "examples": ["whisper Ada I found the key."],
    }
