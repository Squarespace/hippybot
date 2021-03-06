#!/usr/bin/env python
import os
import os.path
import sys
import codecs
import time
import traceback
import logging
from jabberbot import botcmd, JabberBot, xmpp
from ConfigParser import ConfigParser
from optparse import OptionParser
from inspect import ismethod
from lazy_reload import lazy_reload

from hippybot.hipchat import HipChatApi
from hippybot.daemon.daemon import Daemon
from hippybot.lookup import Lookup, USER_DOMAIN, ROOM_DOMAIN

# List of bot commands that can't be registered, as they would conflict with
# internal HippyBot methods
RESERVED_COMMANDS = (
    'api',
)

class Thing:
    pass

def do_import(name):
    """Helper function to import a module given it's full path and return the
    module object.
    """
    mod = __import__(name)
    components = name.split('.')
    for comp in components[1:]:
        mod = getattr(mod, comp)
    return mod

class HippyBot(JabberBot):

    _timestamp = time.time()
    _content_commands = {}
    _global_commands = []
    _command_aliases = {}
    _all_msg_handlers = []
    _last_message = ''
    _last_send_time = time.time()
    _restart = False
    _lookup = None

    def __init__(self, config):
        self._config = config

        prefix = config['connection']['username'].split('_')[0]
        self._channels = [u"%s_%s@%s" % (prefix, c.strip().lower().replace(' ',
                '_'), ROOM_DOMAIN) for c in
                config['connection']['channels'].split('\n')]

        username = u"%s@%s" % (config['connection']['username'], USER_DOMAIN)
        # Set this here as JabberBot sets username as private
        self._username = username
        super(HippyBot, self).__init__(username=username,
                                        password=config['connection']['password'])
        # Make sure we don't timeout after 150s
        self.PING_FREQUENCY = 50


        for channel in self._channels:
            self.join_room(channel, config['connection']['nickname'])

        self._lookup = Lookup(self)

        # To work in hipchat's actual chat rooms, the registered mention name
        # must be used in all cases. That requires fetching the hipchat user object.
        self._at_name = u"@%s " % self.bot_user().mention_name
        self._at_short_name = self._at_name

        plugins = config.get('plugins', {}).get('load', [])
        if plugins:
            plugins = plugins.strip().split('\n')
        self._plugin_modules = plugins
        self._plugins = {}

        self.load_plugins()

        self.log.setLevel(logging.INFO)

    def is_groupchat_message(self, mess):
        return mess.getType() == 'groupchat' 

    def get_sending_room(self, mess):
        return self._lookup.get_sending_room(mess.getFrom())

    def get_sending_user(self, mess):
        return self._lookup.get_sending_user(mess.getFrom())

    def bot_user(self):
        return self._lookup.get_sending_user(self._username)

    def from_bot(self, mess):
        """Helper method to test if a message was sent from this bot.
        """
        sender = self._lookup.get_sending_user(mess.getFrom())
        return sender is not None and sender.xmpp_jid == self._username

    def to_bot(self, mess):
        """Helper method to test if a message was directed at this bot.
        Returns a tuple of a flag set to True if the message was to the bot,
        and the message strip without the "at" part.
        """
        respond_to_all = self._config.get('hipchat', {}).get(
            'respond_to_all', False
            )
        # If not a groupchat msg, it's a chat msg, and thus could only
        # be addressed directly to the bot.
        if mess.getType() != 'groupchat':
            return True, (mess.getBody() or '')

        to = False
        if not isinstance(mess, basestring):
            mess = unicode(mess.getBody()) or u''

        names = [
            u'@all ',
            unicode(self._at_short_name),
            unicode(self._at_name)
        ]

        for n in names:
            if mess.startswith(n):
                return True, mess[len(n):]

        return to, mess

    def send_message(self, mess):
        """Send an XMPP message
        Overridden from jabberbot to update _last_send_time
        """
        self._last_send_time = time.time()
        self.connect().send(mess)

    def callback_message(self, conn, mess):
        """Message handler, this is where we route messages and transform
        direct messages and message aliases into the command that will be
        matched by JabberBot.callback_message() to a registered command.
        """
        self.log.debug("Message: %s" % mess)
        message = unicode(mess.getBody()).strip()
        if not message:
            return

        at_msg, message = self.to_bot(mess)
        mess.to_bot = at_msg

        if len(self._all_msg_handlers) > 0:
            for handler in self._all_msg_handlers:
                try:
                    handler(mess)
                except Exception, e:
                    self.log.exception(
                            'An error happened while processing '
                            'a message ("%s") from %s: %s"' %
                            (mess.getType(), mess.getFrom(),
                                traceback.format_exc(e)))

        if u' ' in message:
            cmd = message.split(u' ')[0]
        else:
            cmd = message

        if cmd in self._command_aliases:
            message = u"%s%s" % (self._command_aliases[cmd],
                                message[len(cmd):])
            cmd = self._command_aliases[cmd]

        ret = None
        if at_msg or cmd in self._global_commands:
            mess.setBody(message)
            ret = super(HippyBot, self).callback_message(conn, mess)
        self._last_message = message
        if ret:
            return ret
        for name in self._content_commands:
            try:
                cmd = self._content_commands[name]
                ret = cmd(mess)
                if ret:
                    self.send_simple_reply(mess, ret)
                    return ret
            except Exception as e:
                self.log.exception(e)
                logging.exception(e)
                return 'Error processing cmd'

    def up_time(self):
        return time.time() - self._timestamp

    def join_room(self, room, username=None, password=None):
        """Overridden from JabberBot to provide history limiting.
        """
        NS_MUC = 'http://jabber.org/protocol/muc'
        if username is None:
            username = self._username.split('@')[0]
        my_room_JID = u'/'.join((room, username))
        pres = xmpp.Presence(to=my_room_JID)
        if password is not None:
            pres.setTag('x',namespace=NS_MUC).setTagData('password',password)
        else:
            pres.setTag('x',namespace=NS_MUC)

        # Don't pull the history back from the server on joining channel
        pres.getTag('x').addChild('history', {'maxchars': '0',
                                                'maxstanzas': '0'})
        self.connect().send(pres)

    def _idle_ping(self):
        """Pings the server, calls on_ping_timeout() on no response.

        To enable set self.PING_FREQUENCY to a value higher than zero.

        Overridden from jabberbot in order to send a single space message
        to HipChat, as XMPP ping doesn't seem to cut it.
        """
        if self.PING_FREQUENCY \
            and time.time() - self._last_send_time > self.PING_FREQUENCY:
            self._last_send_time = time.time()
            self.send_message(' ')

    def rewrite_docstring(self, m):
        if m.__doc__ and m.__doc__.find("@NickName") > -1:
            m.__func__.__doc__ = m.__doc__.replace("@NickName", self._at_name)

    @botcmd(hidden=True)
    def load_plugins(self, mess=None, args=None):
        """Internal handler and bot command to dynamically load and reload
        plugin classes based on the [plugins][load] section of the config.
        """
        for path in self._plugin_modules:
            name = path.split('.')[-1]
            try:
                if name in self._plugins:
                    lazy_reload(self._plugins[name])
                module = do_import(path)
                self._plugins[name] = module
            except Exception as e:
                self.log.warn('Unable to load plugin: %s', name)
                logging.warn('Unable to load plugin: %s', name)
                logging.exception(e)
                continue

            # If the module has a function matching the module/command name,
            # then just use that
            command = getattr(module, name, None)

            content_funcs = []
            if not command:
                # Otherwise we're looking for a class called Plugin which
                # provides methods decorated with the @botcmd decorator.
                plugin = getattr(module, 'Plugin')()
                plugin.bot = self
                commands = [c for c in dir(plugin)]
                funcs = []

                for command in commands:
                    m = getattr(plugin, command)
                    if ismethod(m) and getattr(m, '_jabberbot_command', False):
                        if command in RESERVED_COMMANDS:
                            self.log.error('Plugin "%s" attempted to register '
                                        'reserved command "%s", skipping..' % (
                                            plugin, command
                                        ))
                            continue
                        self.rewrite_docstring(m)
                        name = getattr(m, '_jabberbot_command_name', False)
                        self.log.info("command loaded: %s" % name)
                        funcs.append((name, m))

                    if ismethod(m) and getattr(m, '_jabberbot_content_command', False):
                        if command in RESERVED_COMMANDS:
                            self.log.error('Plugin "%s" attempted to register '
                                        'reserved command "%s", skipping..' % (
                                            plugin, command
                                        ))
                            continue
                        self.rewrite_docstring(m)
                        name = getattr(m, '_jabberbot_command_name', False)
                        self.log.info("command loaded: %s" % name)
                        content_funcs.append((name, m))

                # Check for commands that don't need to be directed at
                # hippybot, e.g. they can just be said in the channel
                self._global_commands.extend(getattr(plugin,
                                                'global_commands', []))
                # Check for "special commands", e.g. those that can't be
                # represented in a python method name
                self._command_aliases.update(getattr(plugin,
                                                'command_aliases', {}))

                # Check for handlers for all XMPP message types,
                # this can be used for low-level checking of XMPP messages
                self._all_msg_handlers.extend(getattr(plugin,
                                                'all_msg_handlers', []))
            else:
                funcs = [(name, command)]

            for command, func in funcs:
                setattr(self, command, func)
                self.commands[command] = func
            for command, func in content_funcs:
                setattr(self, command, func)
                self._content_commands[command] = func
        if mess:
            return 'Reloading plugin modules and classes..'

    _api = None
    @property
    def api(self):
        """Accessor for lazy-loaded HipChatApi instance
        """
        if self._api is None:
            auth_token = self._config.get('hipchat', {}).get(
                'api_auth_token', None)
            if auth_token is None:
                self._api = False
            else:
                self._api = HipChatApi(auth_token=auth_token)
        return self._api

class HippyDaemon(Daemon):
    config = None
    def run(self):
        try:
            bot = HippyBot(self.config._sections)
            bot.serve_forever()
        except Exception, e:
            print >> sys.stderr, "ERROR: %s" % (e,)
            print >> sys.stderr, traceback.format_exc()
            return 1
        else:
            return 0

def control():
    parser = OptionParser(usage="""usage: %prog [options] start|stop|restart""")

    parser.add_option("-c", "--config", dest="config_path", help="Config file path")
    parser.add_option("-p", "--pid", dest="pid", help="PID file location")
    (options, pos_args) = parser.parse_args()

    # commands are start, stop, restart
    cmd = pos_args[0] if len(pos_args) else None
    if cmd not in ('start', 'stop', 'restart'):
        parser.error("Command must be one of start, stop, restart")
        
    pid = options.pid
    if not pid:
        pid = os.path.abspath(os.path.join(os.path.dirname(
            options.config_path) if options.config_path else os.getcwd(), 'hippybot.pid'))

    config = ConfigParser()
    if options.config_path:
        config.readfp(codecs.open(os.path.abspath(options.config_path), "r", "utf8"))

    # set up logging
    import logging
    logargs = { 'level': 'INFO' }
    if config.has_section('logging'):
        for opt in ('filename', 'filemode', 'format', 'datefmt', 'level'):
            if config.has_option('logging', opt):
                logargs[opt] = config.get('logging', opt)
    logging.basicConfig(**logargs)

    runner = HippyDaemon(pid, stdout=sys.stdout, stderr=sys.stderr)

    # if stop, don't bother requiring config
    if cmd == 'stop':
        ret = runner.stop()
        return 0 if ret is None else ret


    # now we require config
    if not options.config_path:
        parser.error('Missing config file path')


    runner.config = config

    if cmd == 'start':
        ret = runner.start()
        return 0 if ret is None else ret
    elif cmd == 'restart':
        ret = runner.stop()
        try:
            os.remove(pid)
        except OSError:
            logging.warning("Could not remove pid file %s" % pid)
        if ret is not None:
            return ret
        ret = runner.start()
        return 0 if ret is None else ret
    else:
        parser.error("Command must be one of start, stop, restart")

def main():
    import logging
    logging.basicConfig(level='INFO')

    parser = OptionParser(usage="""usage: %prog [options]""")

    parser.add_option("-c", "--config", dest="config_path", help="Config file path")
    parser.add_option("-d", "--daemon", dest="daemonise", help="Run as a"
            " daemon process", action="store_true")
    parser.add_option("-p", "--pid", dest="pid", help="PID file location if"
            " running with --daemon")
    (options, pos_args) = parser.parse_args()

    if not options.config_path:
        print >> sys.stderr, 'ERROR: Missing config file path'
        return 1

    config = ConfigParser()
    config.readfp(codecs.open(os.path.abspath(options.config_path), "r", "utf8"))

    pid = options.pid
    if not pid:
        pid = os.path.abspath(os.path.join(os.path.dirname(
            options.config_path), 'hippybot.pid'))

    runner = HippyDaemon(pid)
    runner.config = config
    if options.daemonise:
        ret = runner.start()
        if ret is None:
            return 0
        else:
            return ret
    else:
        return runner.run()

if __name__ == '__main__':
    sys.exit(main())
