from functools import wraps, update_wrapper
from jabberbot import botcmd
import re

def directcmd(func):
    @wraps(func)
    def wrapper(self, origin, args):
        message = func(self, origin, args)
        if origin.getType() == 'groupchat':
            user = self.bot.get_sending_user(origin)
            return u'@%s %s' % (user.mention_name, message)
        else:
            return message
    return botcmd(wrapper)


def direct(fn):
    @wraps(fn)
    def _direct(ctx, msg, *args, **kwargs):
        if msg.to_bot:
            return fn(ctx, msg, *args, **kwargs)
        return
    return update_wrapper(_direct, fn)


def contentcmd(*args, **kwargs):
    """Decorator for bot commentary"""

    def decorate(func, name=None):
        setattr(func, '_jabberbot_content_command', True)
        setattr(func, '_jabberbot_command_name', name or func.__name__)
        return func

    if len(args):
        return decorate(args[0], **kwargs)
    else:
        return lambda func: decorate(func, **kwargs)


def match(regex=None):
    """Decorator for bot commentary that matches a regular expression"""
    def _match(fn):
        setattr(fn, '_jabberbot_content_command', True)
        setattr(fn, '_jabberbot_command_name', fn.__name__)

        @wraps(fn)
        def __match(ctx, msg, *args, **kwargs):
            if not regex or not msg or not msg.getBody() or ctx.bot.from_bot(msg):
                return
            else:
                m = re.search(regex, msg.getBody(), re.IGNORECASE)
                if m:
                    user = '@%s' % ctx.bot.get_sending_user(msg).mention_name
                    return fn(ctx, user, msg.getBody(), match=m, **kwargs)
                return
        return update_wrapper(__match, fn)

    return _match


def status(color='purple', regex=None):
    """Decorator for bot commentary that submits a status message of html with color"""
    def _status(fn):
        setattr(fn, '_jabberbot_content_command', True)
        setattr(fn, '_jabberbot_command_name', fn.__name__)

        @wraps(fn)
        def __status(ctx, msg, *args, **kwargs):
            if not regex or not msg or not msg.getBody() or ctx.bot.from_bot(msg):
                return
            else:
                m = re.search(regex, msg.getBody(), re.IGNORECASE)
                if m:
                    user = '@%s' % ctx.bot.get_sending_user(msg).mention_name
                    html = fn(ctx, user, msg.getBody(), match=m, **kwargs)
                    message_room(ctx, msg, html, color=color)
                return
        return __status

    return _status


def message_room(ctx, msg_obj, content, format='html', color='purple'):
    channel = unicode(msg_obj.getFrom()).split('/')[0].split('@')[0].split('_', 1)[1]
    room_id = sending_room = ctx.bot.get_sending_room(msg_obj).room_id
    apiargs = {
        'room_id': room_id,
        'from': ctx.bot._config['connection']['nickname'],
        'color': color,
        'message_format': format,
        'message': content
    }
    ctx.bot.api.rooms.message(apiargs)

