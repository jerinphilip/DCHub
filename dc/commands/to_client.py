
    ### Functions that send data to clients


    def give_ChatMessage(self, user, message):
        '''Send a chat message from the client to the entire hub'''
        if isinstance(user, str):
            nick = user
        else:
            nick = user.nick
        if self.handleslashme and (message.startswith('/me') or message.startswith('+me')):
            message = '* %s%s|' % (nick, message[3:])
        else:
            message = '<%s> %s|' % (nick, message)
        for user in self.users.values():
            user.sendmessage(message)

    def give_EmptyCommand(self, user):
        '''Send an empty command to a user (as a keep alive)'''
        user.sendmessage('|')

    def give_HubFullRedirect(self, user):
        '''Give the user a redirect, and ignore the user afterwards'''
        user.sendmessage('$ForceMove %s|' % self.hubredirectwhenfull)
        user.ignoremessages = True

    def give_PrivateMessage(self, sender, receiver, message):
        '''Sends a private message from sender to receiver

        If the receiver is a bot, send it as a command to the bot.
        '''
        if isinstance(sender, str):
            nick = sender
        else:
            nick = sender.nick
        if hasattr(receiver, 'isDCHubBot') and not hasattr(sender, 'isDCHubBot') and not isinstance(sender, str):
            receiver.processcommand(sender, message)
        else:
            if self.handleslashme and (message.startswith('/me') or message.startswith('+me')):
                message = '* %s%s|' % (nick, message[3:])
            else:
                message = '<%s> %s|' % (nick, message)
            receiver.sendmessage('$To: %s From: %s $%s|' % (receiver.nick, nick, message))

    def give_SpamNotification(self, user, args):
        '''Give the user a spam/flood notification message'''
        user.sendmessage('<Hub-Security> Your message was dropped because it violates our spam/flood limits.|')

    def give_WelcomeMessage(self, user):
        '''Give the user the welcome message for the hub'''
        message = 'This hub was written in Python. Python kicks butt!'
        user.sendmessage('<Hub-Security> %s|' % message)
        user.sendmessage('<User-Details> %s [ %s ] |' % (user.nick,user.ip))
        user.sendmessage('<Welcome> %s|' % (self.welcome))

    def giveBadPass(self, user):
        '''Give the user a message saying their password was incorrect'''
        user.sendmessage('$BadPass|')

    def giveConnectToMe(self, sender, receiver, ip, port):
        '''Give receiver a connect to me message from sender'''
        receiver.sendmessage('$ConnectToMe %s %s:%s|' % (receiver.nick, ip, port))

    def giveForceMove(self, victim, user, where, message):
        '''Give victim a force move message'''
        victim.sendmessage('$ForceMove %s|$To: %s From: %s $<%s> You are being redirected to %s because: %s|' % (where, victim.nick, user.nick, user.nick, where, message))
        victim.ignoremessages = True

    def giveGetPass(self, user):
        '''Ask the user for their password'''
        user.sendmessage('$GetPass|')

    def giveHello(self, user, newuser = False):
        '''Give the user a hello message

        If newuser is True, gives the hello message to all logged in users that
        don't support NoHello.
        If newuser is False, just gives the user a hello message (telling them
        they have been logged in).
        '''
        message = '$Hello %s|' % user.nick
        if newuser:
            for client in self.users.values():
                if client is not user and 'NoHello' not in client.supports:
                    client.sendmessage(message)
        else:
            user.sendmessage(message)

    def giveHubIsFull(self, user):
        '''Give the user a message telling them the hub is full (ignore them after)'''
        user.sendmessage('$HubIsFull|')
        user.ignoremessages = True

    def giveLogedIn(self, user):
        '''Give the user a message letting them know their password was accepted'''
        user.sendmessage('$LogedIn %s|' % user.nick)

    def giveLock(self, user):
        '''Give the user the lock'''
        user.sendmessage('$Lock %s Pk=%s|' % (self.lockstring, self.privatekeystring))

    def giveHubName(self, user = None):
        '''Give the hub name to a new connection or to all logged on users

        If user is None, the hub name has changed, so send it to all users
        Otherwise, the user has just logged in, so send it just to it
        '''
        message = '$HubName %s|' % self.name
        if user is None:
            for user in self.users.values():
                user.sendmessage(message)
        else:
            user.sendmessage(message)

    def giveMyINFO(self, client, newuser = False):
        '''Give MyINFO for user to the hub

        If newuser is True, give that user the MyINFO for everyuser in the hub
        '''
        if newuser:
            message = []
            for user in self.users.values():
                message.append(user.myinfo)
            message = ''.join(message)
            client.sendmessage(message)
        myinfo = client.myinfo
        for user in self.users.values():
            user.sendmessage(myinfo)

    def giveNickList(self, user):
        '''Give the nick list to the user'''
        user.sendmessage('$NickList %s$$|' % '$$'.join(self.users.keys()))

    def giveOpList(self, user = None):
        '''Give the op list to a user or the all users

        If user is None, the op list has changed, so give it to all users
        Otherwise, the user has just logged in, so give them the op list
        '''
        if self.ops:
            message = '$OpList %s$$|' % '$$'.join(self.ops.keys())
        else:
            message = '$OpList |'
        if user is None:
            for user in self.users.values():
                user.sendmessage(message)
        else:
            user.sendmessage(message)

    def giveQuit(self, user):
        '''Give hub a message that the user has disconnected'''
        message = '$Quit %s|' % user.nick
        for client in self.users.values():
            client.sendmessage(message)

    def giveRevConnectToMe(self, sender, receiver):
        '''Give RevConnectToMe to sender from receiver'''
        receiver.sendmessage('$RevConnectToMe %s %s|' % (sender.nick, receiver.nick))

    def giveSearch(self, searcher, host, sizerestricted, isminimumsize, size, datatype, searchpattern):
        '''Give search message from searcher to the entire hub'''
        message = '$Search %s %s?%s?%s?%s?%s|' % (host, sizerestricted, isminimumsize, size, datatype, searchpattern)
        for user in self.users.values():
            user.sendmessage(message)

    def giveSR(self, searcher, resulter, path, filesize, freeslots, totalslots, hubname, hubhost):
        '''Give search response from resulter to searcher'''
        searcher.sendmessage('$SR %s %s\x05%i %i/%i\x05%s (%s)|'% (resulter.nick, path, filesize, freeslots, totalslots, hubname, hubhost))

    def giveSupports(self, user):
        '''Give user a list of extensions that the server supports'''
        user.sendmessage('$Supports %s|' % ' '.join(self.supports))

    def giveUserCommand(self, user = None, command = None):
        '''Give user command(s) to user or hub

        If both user and command are None, determine the appropriate commands
         for each user and send them the commands
        If user is None, give the new command all appropriate users
        If command is None, give the user all appropriate commands
        If neither is None, give the user the command, if appropriate
        '''
        if user is None:
            if command is None:
                for user in self.users.values():
                    if 'UserCommand' in user.supports:
                        user.sendmessage(self.getusercommands(user))
            else:
                for user in self.users.values():
                    if 'UserCommand' in user.supports:
                        user.sendmessage(self.getusercommand(user, command))
        else:
            if command is None:
                command = self.getusercommands(user)
            else:
                command = self.getusercommand(user, command)
            if 'UserCommand' in user.supports:
                user.sendmessage(command)

    def giveUserIP(self, requestor = None, requestee = None):
        '''Give IP to requestor

        If neither requestor nor requestee are None, give the requestor the
         requestee's IP
        If the requesee is None, give the requestor a list of IPs for all
         users
        If requestor is None, give all ops the requestee's IP
        '''
        if requestor is not None and requestee is not None:
            requestor.sendmessage('$UserIP %s %s|' % (requestee.nick, requestee.ip))
        elif requestor is not None:
            requestor.sendmessage('$UserIP %s$$|' % '$$'.join(['%s %s' % (user.nick, user.ip) for user in self.users.values()]))
        elif requestee is not None:
            message = '$UserIP %s %s|' % (requestee.nick, requestee.ip)
            for op in self.ops.values():
                if 'UserIP2' in op.supports:
                    op.sendmessage(message)

    def giveValidateDenide(self, user):
        '''Give a user a message that their login has been denied'''
        user.sendmessage('$ValidateDenide|')

