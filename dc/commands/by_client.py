
    ### Functions that handle commands sent by clients

    # There are four types of functions that handle commands sent by the user
    # parse* takes a user and an argument string and parses the argument string
    #  into arguments, and may return None or raise an exception if the format
    #  is incorrect
    # check* takes a user and these arguments and raises an exception if
    #  anything is invalid (bad combination of arguments, etc.).  It may
    #  change the arguments if it can fix the problems, instead of raising an
    #  exception.
    # got* takes a user and the arguments and processes the command
    # bad* is called when parse* or check* raise exceptions

    # Since many of these functions are quite simple and similiar, no doc
    # strings are provided.

    ## _ChatMessage command

    def parse_ChatMessage(self, user, args):
        nick, message = args[1:].split('> ', 1)
        return nick, message

    def check_ChatMessage(self, user, nick, message, *args):
        if nick != user.nick:
            raise ValueError( 'bad nick')
        # Check whether the message taken by itself has problems
        messagesize = len(message)
        if messagesize > user.limits['maxmessagesize']:
            raise ValueError( 'above maximum size')
        numcr = message.count('\r')
        numnl = message.count('\n')
        if numcr > numnl:
            numnl = numcr
        if numnl > user.limits['maxnewlinespermessage']:
            raise ValueError( 'too many newlines')
        # Checks recently submitted messages to see if this message pushes the
        # user over any of its limits
        curtime = time.time()
        pretime = curtime - user.limits['timeperiod']
        user.recentmessages = [messageinfo for messageinfo in user.recentmessages if messageinfo[0] > pretime]
        nummessages = len(user.recentmessages)
        if nummessages >= user.limits['maxmessagespertimeperiod']:
            raise ValueError( 'too many messages within time period')
        numchars = sum([messageinfo[1] for messageinfo in user.recentmessages]) + messagesize
        if numchars >= user.limits['maxcharacterspertimeperiod']:
            raise ValueError( 'too many characters within time period')
        numnewlines = sum([messageinfo[2] for messageinfo in user.recentmessages]) + numnl
        if numnewlines >= user.limits['maxnewlinespertimeperiod']:
            raise ValueError( 'too many newlines within time period')
        user.recentmessages.append((curtime, messagesize, numnl, message))

    def got_ChatMessage(self, user, nick, message, *args):
        self.give_ChatMessage(user, message)
        if message.startswith('+'):
            self.got_Genie(user,message,'sendmessage')
        if message.startswith('!tvinfo'):
            self.got_TVInfo(user,'')

    ######### JohnDoe %TVInfo Handles These Commands
    def got_TVInfo(self, user, message):
        try:
       	    if message.startswith('!tvinfo'):
                taskStat = getattr(self.bots['TVInfo'],'tvinfo')('')
            else:
                taskStat = getattr(self.bots['TVInfo'],'tvinfo')(message)
            self.log.log(self.loglevels['hubstatus'],'User:%s issued %s. Status:Success.'%(user.nick,message))
            self.give_PrivateMessage(self.bots['TVInfo'],user,'%s|'%(taskStat))
        except Exception as e:
            self.log.log(self.loglevels['hubstatus'],'User:%s issued %s. Exception:%s'%(user.nick,message,e))
            self.give_PrivateMessage(self.bots['TVInfo'],user,'Some unexpected error Occured. Cut the programmer some slack.|')
	################################################

    ######### JohnDoe %Genie Handles These Commands
    def got_Genie(self, user, message,messageType):
        messageParts = message.split()
        userCommand = messageParts[0][1:]
        userCommandArgs = ' '.join(messageParts[1:])+'\r\n'
        if userCommand in self.bots['Genie'].genie:
            if messageType == 'sendmessage':
                user.sendmessage('<Hub-Genie> %s, you issued a +%s command. Your word is my command!|'%(user.nick,userCommand))
            elif messageType == 'give_PrivateMessage':
                self.give_PrivateMessage(self.bots['Genie'],user,'%s, you issued a +%s command. Your word is my command!|'%(user.nick,userCommand))
            try:
                taskStat = getattr(self.bots['Genie'],'%s'%userCommand)(user,userCommandArgs)
                self.log.log(self.loglevels['hubstatus'],'User:%s issued %s. Status:%s'%(user.nick,message,taskStat))
            except Exception as e:
                self.log.log(self.loglevels['hubstatus'],'User:%s issued %s. Exception:%s'%(user.nick,message,e))
        else:
            if messageType == 'sendmessage':
                user.sendmessage('<Hub-Genie> %s, perhaps you want something done. +%s is not a valid command. Read the list of available commands by entering +help. |'%(user.nick,userCommand))
            elif messageType == 'give_PrivateMessage':
                self.give_PrivateMessage(self.bots['Genie'],user,'%s, I will ignore that and pretend you never said that. If you want something done, speak to me in a language I understand. %s |'%(user.nick,self.bots['Genie'].availableCommands))
    ########## JohnDoe


    def bad_ChatMessage(self, user, args, parsedargs = None):
        if self.notifyspammers and parsedargs is not None:
            self.give_SpamNotification(user, parsedargs[1])

    ## _EmptyCommand ('|')

    def got_EmptyCommand(self, user):
        pass

    ## _PrivateMessage command

    def parse_PrivateMessage(self, user, args):
        sentto, message = args.split(' From: ', 1)
        sentfrom, nick, message = message.split(' ', 2)
        nick = nick[2:-1]
        return sentto, sentfrom, nick, message

    def check_PrivateMessage(self, user, sentto, sentfrom, nick, message, *args):
        if sentfrom != user.nick:
            raise ValueError( 'bad sent from')
        if sentto not in self.users:
            raise ValueError( 'bad sent to')

    def got_PrivateMessage(self, user, sentto, sentfrom, nick, message, *args):
        self.give_PrivateMessage(user, self.users[sentto], message)

    def bad_PrivateMessage(self, user, args, parsedargs = None):
        pass

    ## Close command

    def parseClose(self, user, args):
        nick = args
        return (nick, )

    def checkClose(self, user, nick, *args):
        if nick not in self.nicks:
            raise ValueError( 'bad nick')

    def gotClose(self, user, nick, *args):
        self.removeuser(self.users[nick])

    def badClose(self, user, args, parsedargs = None):
        pass

    ## ConnectToMe command

    def parseConnectToMe(self, user, args):
        nick, host = args.split(' ', 1)
        ip, port = host.split(':', 1)
        return nick, ip, port

    def checkConnectToMe(self, user, nick, ip, port, *args):
        if nick not in self.users:
            raise ValueError( 'bad nick')

    def gotConnectToMe(self, user, nick, ip, port, *args):
        self.giveConnectToMe(user, self.users[nick], ip, port)

    def badConnectToMe(self, user, args, parsedargs = None):
        pass

    ## GetNickList command

    def parseGetNickList(self, user, args):
        return ()

    def checkGetNickList(self, user, *args):
        if user.loggedin:
            return
        user.givenicklist = True
        return False

    def gotGetNickList(self, user, *args):
        self.giveNickList(user)
        if self.ops:
            self.giveOpList(user)

    def badGetNickList(self, user, args, parsedargs = None):
        pass

    ## GetINFO command

    def parseGetINFO(self, user, args):
        nick = args.split(' ')[-1]
        return (nick, )

    def checkGetINFO(self, user, nick, *args):
        if nick not in self.users:
            raise ValueError( 'bad nick')

    def gotGetINFO(self, user, nick, *args):
        self.giveMyINFO(self.users[nick])

    def badGetINFO(self, user, args, parsedargs = None):
        pass

    ## Key command

    def parseKey(self, user, args):
        key = args
        return (key, )

    def checkKey(self, user, key, *args):
        pass

    def gotKey(self, user, key, *args):
        user.key = key

    def badKey(self, user, args, parsedargs = None):
        pass

    ## Kick command

    def parseKick(self, user, args):
        nick = args
        return(nick, )

    def checkKick(self, user, nick, *args):
        if nick not in self.nicks:
            raise ValueError( 'bad nick')

    def gotKick(self, user, nick, *args):
        self.removeuser(self.nicks[nick])

    def badKick(self, user, args, parsedargs = None):
        pass

    ## MyINFO command

    def parseMyINFO(self, user, args):
        tag = ''
        check, nick, rest = args.split(' ', 2)
        if check != '$ALL':
            raise ValueError( 'bad format, no $ALL')
        description, space, speed, email, sharesize, blah = rest.split('$',5)
        if description[-1:] == '>':
            x = description.rfind('<')
            if x != -1:
                tag = description[x:]
                description = description[:x]
        speedclass = ord(speed[-1])
        speed = speed[:-1]
        sharesize = int(sharesize)
        return nick, description, tag, speed, speedclass, email, sharesize

    def checkMyINFO(self, user, nick, description, tag, speed, speedclass, email, sharesize, *args):
        if nick != user.nick:
            raise ValueError( "nick doesn't match")
        for char in description + tag + email + speed:
            if char in self.badchars:
                raise ValueError( 'bad character')
        #if speedclass not in range(1,12):       #JohnDoe commented this. Seemed unnecessary. Was bloking out Eiskalt.
            #raise ValueError( 'bad speedclass')
        if sharesize < user.limits['minsharesize']:
            raise ValueError( 'share size too low')
        # Check for too many recent MyINFOs
        curtime = time.time()
        timelimit = curtime - user.limits['timeperiod']
        user.myinfotimes = [myinfotime for myinfotime in user.myinfotimes if myinfotime > timelimit]
        nummyinfos = len(user.myinfotimes)
        if nummyinfos >= user.limits['maxmyinfopertimeperiod']:
            raise ValueError( 'Too many MyINFOs with time period %s: %i' % (user.idstring, nummyinfos))
        user.myinfotimes.append(curtime)

    def gotMyINFO(self, user, nick, description, tag, speed, speedclass, email, sharesize, *args):
        user.description = description
        user.tag = tag
        user.speed = speed
        user.speedclass = speedclass
        user.email = email
        user.sharesize = sharesize
        self.formatMyINFO(user)
        if not user.loggedin:
            try:
                self.loginuser(user)
            except:
                self.debugexception('Error logging in user', self.loglevels['userloginerror'])
        else:
            self.giveMyINFO(user)

    def badMyINFO(self, user, args, parsedargs = None):
        if not user.loggedin:
            self.removeuser(user)

    def formatMyINFO(self, user):
        # The MyINFO sent to the hub may truncate the description, email, or
        # tag, but the user's object keeps the full value internally
        tag = user.tag
        if len(user.tag) > user.limits['maxtaglength']:
            tag = user.tag[:user.limits['maxtaglength'] - 1] + '>'
        user.myinfo = self.myinfoformat % (user.nick, user.description[:user.limits['maxdescriptionlength']], tag, user.speed, chr(user.speedclass), user.email[:user.limits['maxemaillength']], user.sharesize)

    ## MyPass command

    def parseMyPass(self, user, args):
        password = args
        return (password, )

    def checkMyPass(self, user, password, *args):
        if password != self.accounts[user.nick]['password']:
            raise ValueError( 'bad pass')
        if user.nick in self.nicks and self.nicks[user.nick] is not user:
            self.log.log(self.loglevels['duplicatelogin'], 'Duplicate correct login, removing current user %s, adding new user %s' % (self.nicks[user.nick].idstring, user.idstring))
            self.removeuser(self.nicks[user.nick])

    def gotMyPass(self, user, password, *args):
        self.nicks[user.nick] = user
        if self.accounts[user.nick]['op']:
            self.giveLogedIn(user)
        self.giveHello(user)
        user.validcommands = set('Version GetNickList MyINFO'.split())

    def badMyPass(self, user, args, parsedargs = None):
        self.giveBadPass(user)
        user.ignoremessages = True

    ## OpForceMove command

    def parseOpForceMove(self, user, args):
        nick, where, message = args.split('$',3)[1:]
        nick = nick[4:]
        where = where[6:]
        message = message[4:]
        return nick, where, message

    def checkOpForceMove(self, user, nick, where, message, *args):
        if nick not in self.users:
            raise ValueError( 'bad nick')

    def gotOpForceMove(self, user, nick, where, message, *args):
        victim = self.users[nick]
        self.giveForceMove(victim, user, where, message)

    def badOpForceMove(self, user, args, parsedargs = None):
        pass

    ## ReloadBots command - py-dchub extension

    def parseReloadBots(self, user, args):
        self.loadbots()
        return None

    ## RevConnectToMe command

    def parseRevConnectToMe(self, user, args):
        sender, receiver = args.split(' ', 1)
        return sender, receiver

    def checkRevConnectToMe(self, user, sender, receiver, *args):
        if sender != user.nick:
            raise ValueError( 'bad sender')
        if receiver not in self.users:
            raise ValueError( 'badreceiver')

    def gotRevConnectToMe(self, user, sender, receiver, *args):
        self.giveRevConnectToMe(user, self.users[receiver])

    def badRevConnectToMe(self, user, args, parsedargs = None):
        pass

    ## Search command

    def parseSearch(self, user, args):
        lenargs = len(args)
        if lenargs > user.limits['maxsearchsize']:
            raise ValueError( 'search string too large (%i bytes)' % lenargs)
        host, searchstring = args.split(' ', 1)
        sizerestricted, isminimumsize, size, datatype, searchpattern = searchstring.split('?', 4)
        size = int(size)
        datatype = int(datatype)
        return host, sizerestricted, isminimumsize, size, datatype, searchpattern

    def checkSearch(self, user, host, sizerestricted, isminimumsize, size, datatype, searchpattern, *args):
        if host[:4] == 'Hub:':
            if host[4:] != user.nick:
                raise ValueError( 'bad nick')
        else:
            ip, port = host.split(':',1)
            port = int(port)
            map(int, ip.split('.',3))
        if datatype not in self.validsearchdatatypes:
            raise ValueError( 'bad datatype')
        if self.stringoverlaps(searchpattern, self.badsearchchars):
            raise ValueError( 'bad search pattern character')
        if sizerestricted not in 'FT':
            raise ValueError( 'bad size restricted')
        if isminimumsize not in 'FT':
            raise ValueError( 'bad is minimum size')
        # Check for too many recent searches
        curtime = time.time()
        timelimit = curtime - user.limits['timeperiod']
        user.searchtimes = [searchtime for searchtime in user.searchtimes if searchtime > timelimit]
        numsearches = len(user.searchtimes)
        if numsearches >= user.limits['maxsearchespertimeperiod']:
            raise ValueError( 'Too many searches within time period')
        user.searchtimes.append(curtime)

    def gotSearch(self, user, host, sizerestricted, isminimumsize, size, datatype, searchpattern, *args):
        self.giveSearch(user, host, sizerestricted, isminimumsize, size, datatype, searchpattern)

    def badSearch(self, user, args, parsedargs = None):
        pass

    ## SR command

    def parseSR(self, user, args):
        filesize, freeslots, totalslots = 0, 0, 0
        parts = args.split('\x05')
        if not (3 <= len(parts) <= 4):
            raise ValueError( 'bad split')
        nick, path = parts[0].split(' ', 1)
        if len(parts) == 4:
            filesize, rest = parts[1].split(' ', 1)
            del parts[1]
            freeslots, totalslots = rest.split('/', 1)
            filesize = int(filesize)
            freeslots = int(freeslots)
            totalslots = int(totalslots)
        hubparts = parts[1].split(' ')
        hubname = ' '.join(hubparts[:-1])
        hubhost = hubparts[-1]
        if hubhost[0] + hubhost[-1] != '()':
            raise ValueError( 'bad hubhost')
        hubhost = hubhost[1:-1]
        requestor = parts[2]
        return nick, path, filesize, freeslots, totalslots, hubname, hubhost, requestor

    def checkSR(self, user, nick, path, filesize, freeslots, totalslots, hubname, hubhost, requestor, *args):
        if nick != user.nick:
            raise ValueError( 'bad nick')
        if hubhost.find(':') != -1:
            hubip, hubport = hubhost.split(':', 1)
            hubport = int(hubport)
        else:
            hubip = hubhost
        if requestor not in self.users:
            raise ValueError( 'bad requestor')

    def gotSR(self, user, nick, path, filesize, freeslots, totalslots, hubname, hubhost, requestor, *args):
        self.giveSR(self.users[requestor], user, path, filesize, freeslots, totalslots, hubname, hubhost)

    def badSR(self, user, args, parsedargs = None):
        pass

    ## Supports command

    def parseSupports(self, user, args):
        supports = args.split()
        return (supports, )

    def checkSupports(self, user, supports, *args):
        supports = [feature for feature in supports if feature in self.supports]
        return (supports, )

    def gotSupports(self, user, supports, *args):
        user.supports = supports
        if self.supports:
            self.giveSupports(user)

    def badSupports(self, user, args, parsedargs = None):
        pass

    ## UserIP command

    def parseUserIP(self, user, args):
        nick = args
        return (nick, )

    def checkUserIP(self, user, nick, *args):
        if nick not in self.nicks:
            raise ValueError( 'bad nick')
        if not user.op and nick != user.nick:
            # This is fairly common, so no need to log it
            return False

    def gotUserIP(self, user, nick, *args):
        self.giveUserIP(user, self.nicks[nick])

    def badUserIP(self, user, args, parsedargs = None):
        pass

    ## ValidateNick command

    def parseValidateNick(self, user, args):
        nick = args
        return (nick, )

    def checkValidateNick(self, user, nick, *args):
        if not nick:
            raise ValueError( 'empty nick')
        if len(nick) > user.limits['maxnicklength']:
            raise ValueError( 'nick too long')
        if nick in self.nicks:
            if nick not in self.accounts:
                otheruser = self.nicks[nick]
                if otheruser.ip == user.ip:
                    self.log.log(self.loglevels['duplicatelogin'], 'Duplicate login and IPs match, removing currently logged in user')
                    self.removeuser(otheruser)
                else:
                    self.give_EmptyCommand(otheruser)
                    raise ValueError( 'nick already in use')
        elif self.stringoverlaps(nick, self.badnickchars):
            raise ValueError( 'bad nick character')

    def gotValidateNick(self, user, nick, *args):
        user.nick = nick
        user.idstring += nick
        if nick in self.accounts:
            if not self.accounts[nick]['password']:
                return self.gotMyPass(user, '')
            self.giveGetPass(user)
            user.validcommands = set(['MyPass'])
        else:
            self.nicks[nick] = user
            self.giveHello(user)
            user.validcommands = set('Version GetNickList MyINFO'.split())

    def badValidateNick(self, user, args, parsedargs = None):
        self.giveValidateDenide(user)

    ## Version command

    def parseVersion(self, user, args):
        version = args
        return (version, )

    def checkVersion(self, user, version, *args):
        pass

    def gotVersion(self, user, version, *args):
        user.version = version

    def badVersion(self, user, args, parsedargs = None):
        pass
