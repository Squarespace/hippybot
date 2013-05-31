from xmpp.protocol import JID

USER_DOMAIN = "chat.hipchat.com"
ROOM_DOMAIN = "conf.hipchat.com"

def _extract_hipchat_account_prefix_id(bot):
    return bot._config['connection']['username'].split('_', 1)[0]

def _create_xmpp_jid_for_user(prefix_id, user_id):
    return '%s_%s@%s' % (prefix_id, user_id, USER_DOMAIN)

class Lookup(object):
    def __init__(self, bot):
        self._bot = bot
        self._hipchat_account_prefix_id = _extract_hipchat_account_prefix_id(bot)

    def refresh(self):
        self._rooms = None
        self._rooms_by_channel = None
        self._users = None
        self._users_by_name = None

    _rooms = None
    def rooms(self):
        if self._rooms is None:
            self._rooms = {}
            for item in self._bot.api.rooms.list().get('rooms', []):
                room = Room.from_data(item)
                self._rooms[room.xmpp_jid] = room
        return self._rooms

    def room_for_jid(self, from_jid):
        from_jid = self.normalize_jid(from_jid)
        if self.is_groupchat(from_jid):
            return self.rooms().get(from_jid.getStripped())
        else:
            return None

    _users = None
    def users(self):
        if self._users is None:
            self._users = {}
            for user_item in self._bot.api.users.list().get('users', []):
                # Note: xmpp_jid not expressly provided: one must map to raw roster by resource name
                user_item['xmpp_jid'] = _create_xmpp_jid_for_user(self._hipchat_account_prefix_id, user_item.get('user_id'))
                user = User.from_data(user_item)
                self._users[user.xmpp_jid] = user
        return self._users

    _users_by_name = None
    def users_by_name(self):
        if self._users_by_name is None:
            self._users_by_name = {}
            for user in self.users().itervalues():
                self._users_by_name[user.name] = user
        return self._users_by_name

    def is_groupchat(self, jid):
        return ROOM_DOMAIN == self.normalize_jid(jid).getDomain()

    def normalize_jid(self, jid):
        if isinstance(jid, JID):
            return jid
        elif isinstance(jid, basestring):
            return JID(jid=unicode(jid))
        raise ValueError("JID value is not a JID or basestring, cannot normalize to JID object: %r" % jid)

    def get_sending_room(self, from_jid):
        return self.room_for_jid(from_jid)

    def get_sending_user(self, from_jid):
        # jid is either a groupchat jid where resource is the sender, or a chat jid where the user hipchat id
        # is embedded in the node string.
        from_jid = self.normalize_jid(from_jid)
        if self.is_groupchat(from_jid):
            nickname = from_jid.getResource()
            return self.users_by_name().get(nickname)
        else:
            stripped = from_jid.getStripped()
            return self.users().get(stripped)

class Room(object):
    def __init__(self):
        pass

    @classmethod
    def from_data(cls, data):
        self = cls()
        for k, v in data.iteritems():
            setattr(self, k, v)
        return self


class User(object):
    def __init__(self):
        pass

    @classmethod
    def from_data(cls, data):
        self = cls()
        for k, v in data.iteritems():
            setattr(self, k, v)
        return self

"""
Kinds of inbound messages:

1) groupchat message. the From is the room JID with a resource indicating the real sender. Message getType() will be "groupchat". The To will be empty I think. There may be a @mention of the bot in it.

- To @mention the sender, mention_name needs to be the getResource(), mapped to a hipchat user object, then getting mention_name.
- To detect a bot mention, use the mention_name of the hipchat user object representing the bot's account.

2) 1to1 message. the From will be the sender JID. getType() should be 'chat'. the To should be the bot JID. There may be a @mention of the bot but it would be redundant.

Decorators:

@directcmd should apply to any chat msg, or to groupchat messages where bot is @mentioned.
@botcmd should apply to any chat msg, or to groupchat messages where bot is @mentioned.
@contentcmd should apply to any chat msg and any groupchat msg.

"""

