import os
import logging
import datetime
from logging.handlers import SysLogHandler
from .parser import IntelConfigParser
from select import select
from .client import DCHubClient
import signal
import socket
import sys
import time
import pwd

class DCHub(object):
    '''Direct Connect Hub

    This is the base class that implements the entire client-hub Direct Connect
    protocol.  In most cases, this class will be subclassed to provide
    necessary functionality.  However, if the needs of the hub are very simple,
    using this hub might suffice.
    '''
    id = 0
    def __init__(self, **kwargs):
        self.setupsignals()
        self.setupdefaults(**kwargs)
        if 'oldhub' in kwargs:
            # If reloading hub, don't do a lot of the intial configuration steps
            self.postreload()
            # Delete old hub instance to avoid memory leak when reloading the
            # hub over and over
            del self.kwargs['oldhub']
        else:
            self.setuphub()


    def _copydocstring(self, oldfunction, newfunction):
        '''Copy the docstring from the old function to the new function

        Also attempts to copy the function name (works for Python >= 2.4)
        '''
        try:
            # Python 2.4 and higher can change the names of functions
            newfunction.func_name = oldfunction.func_name
        except:
            pass
        # Keep the same docstring
        newfunction.__doc__ = oldfunction.__doc__

    def _execwrapper(self, function):
        '''Decorator for functions so that other functions can execute before/after

        execbefore functions should return None unless they want to stop the
        main function from executing.  execafter functions should return
        returnobj unless they want to make the function return something
        different.  Either function can raise exceptions.
        '''
        self.log.log(self.loglevels['wrapping'], 'Wrapping %s for execafter and execbefore' % function.func_name)
        def new_function(*args, **kwargs):
            if function.func_name in self.execbefore:
                for f in self.execbefore[function.func_name]:
                    x = f(*args, **kwargs)
                    if x is not None:
                        self.log.log(self.loglevels['execchange'], 'Canceling function execution due to execbefore: function: %s, returning: %s, args: %s, keyword args: %s' % (function.func_name, x, args, kwargs))
                        return x
            returnobj = function(*args, **kwargs)
            if function.func_name in self.execafter:
                for f in self.execafter[function.func_name]:
                    x = f(returnobj, *args, **kwargs)
                    if x is not returnobj:
                        self.log.log(self.loglevels['execchange'], 'Returning different value due to execafter: function: %s, was returning: %s, now returning: %s, args: %s, keyword args: %s' % (function.func_name, returnobj, x, args, kwargs))
                        return x
            return returnobj
        self._copydocstring(function, new_function)
        return new_function

    def _timerwrapper(self, function, loglevel, warningtime, warninglevel = logging.WARNING):
        '''Decorator for functions that logs the amount of time the function takes

        loglevel is the level to log the time elapsed at if it is less than
        warningtime.  If it is greater than warningtime, it is logged at
        WARNING.
        '''
        self.log.log(self.loglevels['wrapping'], 'Wrapping %s for timing' % function.func_name)
        tim = time.time
        def new_function(*args, **kwargs):
            curtime = tim()
            try:
                try:
                    ret = function(*args, **kwargs)
                finally:
                    timediff = tim() - curtime
                    ll = loglevel
                    if timediff > warningtime:
                        ll = warninglevel
            except Exception as error:
                self.log.log(ll, '%s took %0.3f seconds (called with %s %s, raising %s: %r)' % (function.func_name, timediff, args, kwargs, error.__class__.__name__, str(error)))
                raise
            else:
                self.log.log(ll, '%s took %0.3f seconds (called with %s %s, returning %s)' % (function.func_name, timediff, args, kwargs, str(ret)))
            return ret
        self._copydocstring(function, new_function)
        return new_function

    def adduser(self, user):
        '''Add a new user (socket connection) to the hub'''
        self.hubfullcheck(user)
        self.joinfloodcheck(user, 'ip')
        # Python's select seems broken, even if it returns that a given socket
        # is writeable, it can block on writing to it, so you need to add a
        # timeout or the hub may occassionally freeze for minutes at a time
        user.socket.settimeout(0.01)
        self.log.log(self.loglevels['newconnection'],"New user connection from %s" % user.idstring)
        self.setuplimits(user)
        self.sockets[user.socketid] = user
        self.giveLock(user)
        self.giveHubName(user)

    def badcommand(self, user, command):
        '''Check the submitted command for illegal characters

        Also checks that the command length is not over the limit.
        '''
        if len(command) > user.limits['maxcommandsize']:
            return True
        if command.startswith('$Key '):
            # Key commands can contain almost any ASCII character, and
            # since the Key command is ignored, it doesn't really matter
            return False
        badchars = self.badchars
        if command.startswith('$MyINFO $ALL '):
            badcharindex = -1
            for char in command:
                if char in badchars:
                    # MyINFO has one byte that contains ASCII character
                    # 1-12, so ignore one bad character.
                    # checkMyINFO should take care of checking for bad
                    # characters after the MyINFO has been parsed
                    if badcharindex != -1:
                        return True
                    else:
                        badcharindex = command.index(char)
            return False
        if command.startswith('$SR '):
            # SR uses ASCII chracter 5 as a separator
            badchars = self.badsrchars
        if self.stringoverlaps(command, badchars):
            return True
        return False

    def badprivileges(self, user, functionname, args):
        '''Check to see if the user has the privileges to execute the command'''
        return functionname not in user.validcommands

    def cleanup(self):
        '''Close sockets and remove temporary files'''
        if not self.reloadonexit:
            for sock in self.listensocks.values():
                sock.close()
            for user in self.sockets.values():
                self.removeuser(user)
            if os.name == 'posix' and os.path.isfile(self.pidfile):
                try:
                    os.remove(self.pidfile)
                except:
                    self.log.exception('Error removing pid file')
        self.unloadbots()

    def createlisteningsocket(self, ip, port):
        '''Create an individual listening socket'''
        listensock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listensock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        print("Binding")
        listensock.bind((ip, port))
        print("Bound")
        listensock.listen(1)
        self.listensocks[listensock.fileno()] = listensock

    def debugexception(self, logmessage, loglevel = logging.DEBUG):
        '''Log an exception if being debugged, log a debug message otherwise'''
        if self.debug:
            self.log.exception(logmessage)
        else:
            self.log.log(loglevel, logmessage)

    def dropprivileges(self):
        '''Drop privileges if it makes sense to'''
        if not (os.name == 'posix' and self.changeuidgid):
            return
        try:
            if self.gid != os.getgid():
                os.setgid(self.gid)
            if self.uid != os.getuid():
                os.setuid(self.uid)
        except:
            self.log.critical("Can't change group or user ids, exiting")
            self.stop = True

    def getcommandtype(self, command):
        '''Return type of command and argument string'''
        if command[0] != '$':
            if command[0] == '<':
                return '_ChatMessage', command
            return '', ''
        args = command.split(' ', 1)
        if len(args) == 2:
            functionname, args = args
            functionname = functionname[1:]
        else:
            functionname = args[0][1:]
            args = ''
        if functionname == 'To:':
            return '_PrivateMessage', args
        return functionname, args

    def getuidgid(self):
        '''Get the user or group id for given name'''
        results = []
        for modulename, functionname, ugname in [('pwd', 'getpwnam', self.username), ('grp', 'getgrnam', self.groupname)]:
            try:
                print(modulename)
                results.append(int(ugname))
            except ValueError:
                if not modulename in globals():
                    print("CRITICAL: %s module not available, can\'t change ids, exiting")
                    sys.exit(1)
                results.append(getattr(globals()[modulename], functionname)(ugname)[2])
        return results

    def getusercommand(self, user, command):
        '''Return command string if user has permission to use command'''
        perm = command['permission']
        name = command['name'].split('$')[0]
        if perm & 8 and name not in self.bots:
            return ''
        if perm & 4 and (user.nick not in self.accounts or self.accounts[user.nick]['args'].find(name) == -1):
            return ''
        if perm & 2 and user.nick not in self.ops:
            return ''
        if perm & 1 and user.nick not in self.users:
            return ''
        return command['command']

    def getusercommands(self, user):
        '''Return command string containing all commands the user has access to'''
        commands = list(self.usercommands.values())
        commands.sort(key = lambda uc: uc['position'])
        # Remove all previous user commands for the user
        message = '$UserCommand 255 7 |'
        for command in commands:
            message += self.getusercommand(user, command)
        return message

    def handleconnections(self):
        '''Handle all socket connections

        Determine which sockets are readable, and which sockets that have data
        in their outgoing queue are writeable.  Remove any sockets that have
        their error condition set, accept new socket connections, break
        incoming data into discrete commands, put commands in user's incoming
        queue. Send data to writeable sockets.
        '''
        users = self.sockets.values()
        timeout = 1
        readsockets = list(self.listensocks.keys()) + [user.socketid for user in users]
        writesockets = [user.socketid for user in users if user.outgoing]
        readsockets, writesockets, errorsockets = select(readsockets, writesockets, readsockets+writesockets, timeout)
        self.handleerrorsockets(errorsockets)
        self.handlereadsockets(readsockets)
        self.handlewritesockets(writesockets)

    def handleerrorsockets(self, errorsockets):
        '''Handle sockets in error state'''
        for id in errorsockets:
            if id in self.listensocks:
                self.log.error('Error in listening socket %s, closing socket' % self.listensocks[id].getsockname())
                self.listensocks[id].close()
                del self.listensocks[id]
            else:
                self.removeuser(self.sockets[id])

    def handlereadsockets(self, readsockets):
        '''Read data from sockets, accept new connections'''
        curtime = time.time()
        for id in readsockets:
            if id in self.listensocks:
                # New socket connection, accept and add to hub
                try:
                    self.adduser(DCHubClient(self.listensocks[id].accept()))
                except:
                    self.debugexception('Error adding user', self.loglevels['useradderror'])
                continue
            try:
                user = self.sockets[id]
            except KeyError:
                continue
            try:
                data = user.socket.recv(self.buffersize)
                data = str(data)
                if not data:
                    self.log.log(self.loglevels['userdisconnect'], "Client disconnected: %s" % user.idstring)
                    self.removeuser(user)
                    continue
                self.log.log(self.loglevels['datareceived'], 'Data received from %s: %r' % (user.idstring, data))
            except socket.error:
                self.log.log(self.loglevels['socketerror'], "Removing connection due to error in receiving data: %s" % user.idstring)
                self.removeuser(user)
                continue
            except socket.timeout:
                self.log.log(self.loglevels['socketerror'], 'Timeout while reading from socket for user %s' % user.idstring)
                continue
            # Split data into commands
            # Note that if the data ends with '|', commands[-1] will be ''
            commands = data.split('|')
            # First and last commands may be incomplete. Make first command
            # complete by pasting to the end of previous incomplete command
            commands[0] = user.incoming.pop() + commands[0]
            # Add commands to user's incoming command queue
            user.incoming.extend(commands)
            user.commandtimes.extend([curtime] * (len(commands) -1 ))

    def handlereloaderror(self):
        '''Reset variables that allow the hub to continue operating'''
        # In case the other hub was loaded and took over the signals
        self.setupsignals()
        self.stop=False
        self.reloadonexit=False
        self.loadbots()
        self.log.exception('Error reloading hub')

    def handlewritesockets(self, writesockets):
        '''Write data to sockets'''
        for id in writesockets:
            try:
                user = self.sockets[id]
            except KeyError:
                continue
            try:
                data = user.outgoing.encode('utf-8')
                sentsize = user.socket.send(data)
                self.log.log(self.loglevels['datasent'], 'Data sent to %s: %r' % (user.idstring, user.outgoing[:sentsize]))
            except socket.error:
                self.log.log(self.loglevels['socketerror'], "Removing connection due to error in sending data: %s" % user.idstring)
                self.removeuser(user)
                continue
            except socket.timeout:
                self.log.log(self.loglevels['socketerror'], 'Timeout while writing to socket for user %s' % user.idstring)
                continue
            user.outgoing = user.outgoing[sentsize:]

    def hubfullcheck(self, user):
        '''Checks if the hub is full, and either denies access or redirects

        Note that this is normally called both on new socket connections and
        again right before login (after the nick has been received and verified
        if necessary.
        '''
        if self.ishubfull(user):
            if self.hubredirectwhenfull:
                self.give_HubFullRedirect(user)
            else:
                self.giveHubIsFull(user)
            raise ValueError('Hub is full, user cannot join')

    def ishubfull(self, user):
        '''Check to see if the hub is already full'''
        if len(self.users) >= self.maxusers:
            return True
        return False

    def joinfloodcheck(self, user, type='nick'):
        '''Check that the join flood limits aren't being violated'''
        curtime = time.time()
        self.jointimes = [jointime for jointime in self.jointimes if jointime[0] > curtime - self.joinfloodtime]
        joins = [jointime[1] for jointime in self.jointimes]
        checkattr = getattr(user, type)
        if checkattr in joins:
            self.removeuser(user)
            raise ValueError('join flood detected')
        self.jointimes.append((curtime, checkattr))

    def loadaccounts(self):
        '''Load accounts from file'''
        if not os.path.isfile(self.accountsfile):
            return self.log.log(self.loglevels['missingfile'], 'Accounts file does not exist')
        accounts = {}
        self.accountsparser = IntelConfigParser()
        try:
            self.accountsparser.read(self.accountsfile)
            truebools = 'yt1'
            if self.accountsparser.has_section('dchub-accounts'):
                for key, value in self.accountsparser.items('dchub-accounts'):
                    password, op, args = value.split('|', 2)
                    op = bool(op and op.lower() in truebools)
                    accounts[key] = {'name':key, 'password': password, 'op':op, 'args':args}
        except:
            return self.debugexception('Error loading accounts', self.loglevels['loadfileerror'])
        self.accounts = accounts
        self.log.log(self.loglevels['loading'], 'Loaded %s accounts' % len(accounts.keys()))
        self.log.log(self.loglevels['loadingdebug'], 'Loaded accounts: %s' % ' '.join(accounts.keys()))

    def loadbots(self):
        '''Load bots from bots directory'''
        self.unloadbots()
        if not os.path.isdir(self.botsdir):
            return self.log.log(self.loglevels['missingfile'], 'Bots directory does not exist')
        bots = {}
        botfiles = [filename for filename in os.listdir(self.botsdir) if filename.endswith('.py')]
        # Make sure that python looks in the correct directory for bots
        sys.path.insert(0, self.botsdir)
        try:
            for botfile in botfiles:
                try:
                    mod = __import__(botfile[:-3])
                    reload(mod)
                    for item in dir(mod):
                        item = getattr(mod, item)
                        # issubclass(item, DCHubBot) doesn't work
                        # issubclass(item, mod.DCHub.DCHubBot) works, but
                        #  it places additional constraints on bot authors
                        if hasattr(item, 'isDCHubBot') and hasattr(item, 'active') and item.active:
                            bot = item(self)
                            bots[bot.nick] = bot
                except:
                    self.debugexception('Error loading bot: %s' % botfile, self.loglevels['boterror'])
        finally:
            sys.path.pop(0)
        self.log.log(self.loglevels['loading'], 'Loaded %s bots' % (len(bots)))
        self.log.log(self.loglevels['loadingdebug'], 'Loaded bots: %s' % ' '.join(bots.keys()))
        # Keep track of whether any of the bots was an op, so we can send out
        # a new op list
        opsadded = False
        botnames = bots.keys()
        botnames.sort()
        for botnick in botnames:
            bot = bots[botnick]
            for functionname in bot.replace.keys():
                if functionname in self.replacedfunctions:
                    self.log.log(self.loglevels['boterror'], 'Bot %s not added, conflict with function %s' % (botnick, functionname))
                    # a continue(2) construct would have been better
                    bot = None
                    break
            if bot is None:
                continue
            try:
                bot.start()
            except:
                self.debugexception('Error executing bot.start for %s' % bot.idstring, self.loglevels['boterror'])
                try:
                    bot.close()
                except:
                    self.debugexception('Error closing bot %s' % bot.idstring, self.loglevels['boterror'])
                continue
            self.bots[bot.nick] = bot
            # Modify hub functions as requested by the bot
            for functionname, function in bot.replace.items():
                self.replacedfunctions[functionname] = getattr(self, functionname)
                setattr(self, functionname, function)
            for functionname, function in bot.execbefore.items():
                self.wrapfunction(functionname, function, execbefore = True)
            for functionname, function in bot.execafter.items():
                self.wrapfunction(functionname, function, execbefore = False)
            if bot.visible:
                # Make bot appear as a user to the hub
                if bot.nick in self.nicks:
                    # Poor user picked a bad name
                    self.removeuser(self.nicks[bot.nick])
                self.nicks[bot.nick] = bot
                self.users[bot.nick] = bot
                if bot.op:
                    opsadded = True
                    self.ops[bot.nick] = bot
                self.log.log(self.loglevels['userlogin'], 'Bot logged in: %s' % bot.idstring)
                self.giveHello(bot, newuser = True)
                self.giveMyINFO(bot)
        if opsadded:
            self.giveOpList()

    def loadconfig(self):
        '''Load configuration from file and keyword arguments

        Keyword arguments supercede entries in configuration file.  The entries
        are combined and overwrite values already in the hub's namespace.
        Only booleans, integers, floats, and strings can be given as
        configuration options.
        '''
        def givewarning(option):
            '''Give warning that the option is not valid'''
            print("WARNING: Invalid configuration option or option value:"), option
        config = {}
        config.update(self.kwargs)
        truebools = 'yt1'
        attrs = dir(self)
        if not os.path.isfile(self.configfile):
            print("WARNING: Configuration file does not exist")
        else:
            self.configparser = IntelConfigParser()
            self.configparser.read(self.configfile)
            if self.configparser.has_section('dchub'):
                for key, value in self.configparser.items('dchub'):
                    if key not in config:
                        config[key] = value
            for section in 'userlimits', 'loglevels':
                sectiondict = getattr(self, section)
                if self.configparser.has_section('dchub-%s' % section):
                    for key, value in self.configparser.items('dchub-%s' % section):
                        try:
                            if key not in sectiondict:
                                raise ValueError
                            value = int(value)
                        except ValueError:
                            givewarning(key)
                        else:
                            sectiondict[key] = value
            if self.configparser.has_section('dchub-bindings'):
                for key, value in self.configparser.items('dchub-bindings'):
                    try:
                        ip, port = value.split(':')
                        port = int(port)
                    except ValueError:
                        givewarning(value)
                    else:
                        self.bindinglocations.append((ip, port))
        for key, value in config.items():
            try:
                if key not in attrs:
                    raise ValueError
                attr = getattr(self, key)
                if isinstance(attr, bool):
                    # Y, T, Yes, yes, True, true, 1, etc. are True
                    value = bool(value and value[0].lower() in truebools)
                elif isinstance(attr, int):
                    value = int(value)
                elif isinstance(attr, float):
                    value = float(value)
                elif not isinstance(attr, str):
                    # If the variable isn't a bool, int, float, or string, we
                    # shouldn't be messing with it
                    raise ValueError
            except ValueError:
                givewarning(key)
            else:
                setattr(self, key, value)
        for fil in self.filelocations:
            if os.name == 'posix' and self.chroot and os.getuid() == 0:
                if not fil.startswith('/'):
                    setattr(self, fil, '/%s' % getattr(self, fil))
            else:
                setattr(self, fil, os.path.abspath(getattr(self, fil)))

    def loadusercommands(self):
        '''Load user commands from file'''
        if not os.path.isfile(self.usercommandsfile):
            return self.log.log(self.loglevels['missingfile'], 'User Commands file does not exist')
        usercommands = {}
        self.usercommandsparser = IntelConfigParser()
        try:
            self.usercommandsparser.read(self.usercommandsfile)
            if self.usercommandsparser.has_section('dchub-usercommands'):
                for key, value in self.usercommandsparser.items('dchub-usercommands'):
                    permission, position, type, context, command = value.split(' ', 4)
                    command = '$UserCommand %s %s %s|' % (type, context,
                      command.replace('$', '$&#36;').replace('|', '&#124;'))
                    permission = int(permission)
                    position = float(position)
                    type = int(type)
                    context = int(context)
                    usercommands[key] = {'name':key, 'permission': permission,
                      'position':position, 'type':type, 'context':context,
                      'command':command}
        except:
            return self.debugexception('Error loading user commands', self.loglevels['loadfileerror'])
        self.usercommands.clear()
        self.usercommands.update(usercommands)
        self.log.log(self.loglevels['loading'], 'Loaded %s user commands' % len(usercommands.keys()))
        self.log.log(self.loglevels['loadingdebug'], 'Loaded user commands: %s' % ' '.join(usercommands.keys()))

    def loadwelcome(self):
        '''Load welcome message from file'''
        if not os.path.isfile(self.welcomefile):
            return self.log.log(self.loglevels['missingfile'], 'Welcome message file does not exist')
        try:
            fil = open(self.welcomefile,'r')
            try:
                self.welcome = fil.read()
            finally:
                fil.close()
        except:
            return self.debugexception('Error loading welcome message', self.loglevels['loadfileerror'])

    def loginuser(self, user):
        '''Log user into hub

        After user has logged in, they have full access to the hub's features.
        Checks that the hub is full and that the user is not violating the
        join flood limits before adding them to the hub.
        '''
        self.hubfullcheck(user)
        self.joinfloodcheck(user)
        curtime = time.time()
        user.validcommands = self.validusercommands.copy()
        self.users[user.nick] = user
        user.loggedin = True
        self.log.log(self.loglevels['userlogin'], 'User logged in: %s' % user.idstring)
        self.giveHello(user, newuser = True)
        if 'NoGetINFO' in user.supports:
            self.giveMyINFO(user, newuser = True)
        else:
            self.giveMyINFO(user)
        if 'NoHello' not in user.supports and user.givenicklist:
            user.givenicklist = False
            self.giveNickList(user)
        if user.nick in self.accounts:
            user.account = self.accounts[user.nick]
            if self.accounts[user.nick]['op']:
                user.validcommands |= self.validopcommands
                self.ops[user.nick] = user
                user.op = True
                self.giveOpList()
        if self.ops and not user.op:
            self.giveOpList(user)
        self.give_WelcomeMessage(user)
        self.giveUserCommand(user)

    def logtimes(self, functionname, loglevel, warningtime, warninglevel = logging.WARNING):
        '''Log timing information for every call to function with name

        If this function has already been wrapped, this function will wrap the
        already wrapped version, and the intermediate version will no longer
        be available.  Also note that reloading the bots (or any call to
        unwrapfunctions) will remove the logging of timing information.
        '''
        oldfunction = getattr(self, functionname)
        setattr(self, functionname, self._timerwrapper(oldfunction, loglevel, warningtime, warninglevel))
        if functionname not in self.wrappedfunctions:
            self.wrappedfunctions[functionname] = oldfunction

    def mainloop(self):
        '''Continuously process, send, and receive data from socket connections'''
        self.setuplisteningsockets()
        self.log.log(self.loglevels['hubstatus'], 'Starting main loop')
        while not self.stop:
            try:
                self.processcommands()
                self.handleconnections()
            except:
                self.log.exception('Serious error in main control loop')
        self.cleanup()

    def postreload(self):
        '''Commands to preform after reloading the hub

        These commands are executed after the old hub has exited, but before
        the reloaded hub has entered its main loop.
        '''
        self.log = self.kwargs['oldhub'].log
        for key in self.kwargs['oldhub'].__dict__:
            if hasattr(self, key) and (callable(getattr(self, key)) or key in self.nonreloadableattrs):
                continue
            setattr(self, key, getattr(self.kwargs['oldhub'], key))

        # Fixes for reloading from versions <= 0.2.2
        if not self.listensocks and self.kwargs['oldhub'].listensock:
            self.listensocks[self.kwargs['oldhub'].listensock.fileno()] = self.kwargs['oldhub'].listensock
        if not self.bindinglocations:
            self.bindinglocations.append((self.ip, self.port))

        self.loadbots()
        self.log.log(self.loglevels['hubstatus'], 'Hub Reloaded')

    def processcommand(self, user, command):
        '''Process command for user

        Check that the command is valid, check that user has permission to use
        the command, parse the commands args, check that the args are valid
        for the command and user, execute the command.
        '''
        if not command:
            return self.got_EmptyCommand(user)
        if self.badcommand(user, command):
            return self.log.log(self.loglevels['badcommand'], 'Bad command from %s: %r' % (user.idstring, command))
        function, args = self.getcommandtype(command)
        if self.badprivileges(user, function, args):
            return self.log.log(self.loglevels['badcommand'], '%s lacks privilege for command: %r' % (user.idstring, command))
        try:
            parsedargs = getattr(self, 'parse%s' % function)(user, args)
        except:
            self.debugexception('Error parsing args for function parse%s, user %s, args %r' % (function, user.idstring, args), self.loglevels['commanderror'])
            return getattr(self, 'bad%s' % function)(user, args)
        if parsedargs is None:
            return
        try:
            checkedargs = getattr(self, 'check%s' % function)(user, *parsedargs)
        except:
            self.debugexception('Error checking args for function check%s, user %s, args %r' % (function, user.idstring, args), self.loglevels['commanderror'])
            return getattr(self, 'bad%s' % function)(user, args, parsedargs)
        if checkedargs is False:
            return
        if checkedargs is None:
            checkedargs = parsedargs
        getattr(self, 'got%s' % function)(user, *checkedargs)

    def processcommands(self):
        '''Process next command for all users

        Remove users if they have been set to ignore messages and their
        outgoing message queue has been flushed.  Also send a keep alive to
        users that haven't sent a command in a while.
        '''
        curtime = time.time()
        # self.sockets.values() doesn't work here because users can be
        # removed in many of the sub functions, and that modifies the
        # self.sockets dictionary.  This could be worked around by not removing
        # any users until after the processing of commands, but that would
        # require significant changes, and probably wouldn't be worth it except
        # for the largest sites
        for user in self.sockets.values():
            if user.ignoremessages:
                if not user.outgoing:
                    self.removeuser(user)
                continue
            incominglen = len(user.incoming)
            if incominglen > 1:
                if incominglen > user.limits['maxqueuedcommands']:
                    self.log.log(self.loglevels['badcommand'], 'User has more than the max number of queued commands (%i queued, %i max): %s' % (incominglen, user.limits['maxqueuedcommands'], user.idstring))
                    del user.incoming[user.limits['maxqueuedcommands'] - 1:-1]
                user.lastcommandtime = curtime
                commandtime = curtime - user.limits['timeperiod']
                user.commandtimes = [ct for ct in user.commandtimes if ct > commandtime]
                if len(user.commandtimes) >  user.limits['maxcommandspertimeperiod']:
                    continue
                try:
                    while len(user.incoming) > 1 and not user.ignoremessages:
                        command = user.incoming.pop(0)
                        self.processcommand(user, command)
                except:
                    self.log.exception('Error processing command from %s: %r' % (user.idstring, command))
            elif user.lastcommandtime < curtime - user.limits['pingtime']:
                self.give_EmptyCommand(user)

    def reload(self):
        '''Stop the hub's main loop and mark it to be reloaded'''
        self.log.log(self.loglevels['hubstatus'], 'Reloading Hub')
        self.reloadonexit = True
        self.stop = True

    def removeuser(self, user):
        '''Remove user from hub and related data structures'''
        self.log.log(self.loglevels['userremove'], "Removing User: %s" % user.idstring)
        if hasattr(user, 'socketid') and user.socketid in self.sockets \
          and self.sockets[user.socketid] is user:
            del self.sockets[user.socketid]
        try:
            user.close()
        except:
            self.log.exception('Error executing user.close for %s' % user.idstring)
        if user.nick in self.bots and self.bots[user.nick] is user:
            del self.bots[user.nick]
        if user.nick in self.nicks and self.nicks[user.nick] is user:
            del self.nicks[user.nick]
        if user.nick in self.users and self.users[user.nick] is user:
            del self.users[user.nick]
            self.giveQuit(user)
        if user.nick in self.ops and self.ops[user.nick] is user:
            del self.ops[user.nick]
        user.loggedin = False
        user.op = False

    def setupdefaults(self, **kwargs):
        '''Setup default values for hub variables'''
        self.__class__.id += 1
        self.id = self.__class__.id
        #self.version = __version__
        self.version = "1.0"
        #self.myinfoformat = myinfoformat
        self.myinfoformat = '$MyINFO $ALL %s %s%s$ $%s%s$%s$%i$|'
        self.supers = {}
        self.reloadmodules = []
        self.nonreloadableattrs = set('''supers stop nonreloadableattrs
            execbefore execafter replacedfunctions wrappedfunctions
            reloadonexit bots kwargs version'''.split())
        self.port = 411
        self.ip = ''
        self.bindinglocations = []
        self.listensocks = {}
        self.debug = True
        self.stop = False
        self.handleslashme = False
        self.notifyspammers = False
        self.reloadonexit = False
        self.kwargs = kwargs
        self.badchars = ''.join([chr(i) for i in list(range(9)) + list(range(14,32)) + [11, 12, 127]])
        self.badsrchars = self.badchars.replace('\x05','')
        # The DC protocol is only supposed to allow alphanumerics and $ as
        # search pattern characters, but since DC++ doesn't follow this, it
        # seems pointless to restrict the characters.  Here is the command if
        # want to ensure protocol conformance.
        # self.badsearchchars = ''.join([chr(i) for i in range(32, 36) + range(37, 48) + range(58, 65) + range(91, 97) + range(123, 256)])
        self.badsearchchars = ' '
        self.validsearchdatatypes = set(range(10))
        self.badnickchars = '$<>% \x09\x0A\x0D'
        self.supports = 'NoGetINFO NoHello UserCommand UserIP2'.split()
        self.replacedfunctions, self.wrappedfunctions = {}, {}
        self.execbefore, self.execafter = {}, {}
        self.usercommands = {}
        self.filelocations = 'configfile accountsfile welcomefile usercommandsfile botsdir'.split()
        self.validusercommands = set('''_ChatMessage _PrivateMessage MyINFO GetINFO
            GetNickList Search SR ConnectToMe RevConnectToMe UserIP'''.split())
        self.validopcommands = set('OpForceMove Kick Close ReloadBots'.split())
        self.lockstring = 'EXTENDEDPROTOCOLABCABCABCABCABCABC'
        self.privatekeystring = 'py-dchub-%s--' % self.version
        self.name = 'py-dchub'
        self.hubredirectwhenfull = ''
        self.welcome = ''
        # Incoming socket buffer size
        self.buffersize = 1024
        # Sockets includes all connections to the server
        # Nicks includes all users that have logged in with ValidateNick
        # Users includs all users that have sent MyINFO
        self.sockets, self.users,  self.ops, self.bots = {}, {}, {}, {}
        self.accounts, self.nicks = {}, {}
        self.jointimes = []
        self.loglevels = {'wrapping':10, 'datasent':1, 'datareceived':5,
            'newconnection': 10, 'useradderror': 10, 'userdisconnect': 10,
            'socketerror': 10, 'loading': 10, 'loadingdebug': 3,
            'loadfileerror': 40, 'missingfile': 30, 'boterror': 20,
            'userlogin': 10, 'hubstatus': 20, 'userremove': 10,
            'duplicatelogin': 20, 'commanderror':10, 'userloginerror':20,
            'badcommand':5, 'execchange': 10,}
        self.userlimits = {'maxcommandsize':25000, 'maxqueuedcommands':20,
            'maxcommandspertimeperiod':20, 'maxdescriptionlength':50,
            'maxtaglength':50, 'maxnicklength':25, 'maxemaillength':50,
            'minsharesize':0, 'maxmessagesize':500, 'maxnewlinespermessage':5,
            'maxcharacterspertimeperiod':1000, 'maxmessagespertimeperiod':10,
            'maxnewlinespertimeperiod':10, 'maxsearchespertimeperiod':10,
            'maxsearchsize':500, 'maxmyinfopertimeperiod':3, 'pingtime':300,
            'timeperiod':60}
        # Hub Limits
        self.maxusers = 500
        self.joinfloodtime = 60
        # Unix specific options
        self.chroot = True
        self.changeuidgid = False
        self.username = 'dchub'
        self.groupname = 'daemon'
        self.pidfile = ''
        # Logging options
        self.logfile = ''
        self.loglevel = 'DEBUG'
        self.usesyslog = False
        self.sysloghost = '/dev/log'
        self.syslogfacility = 'daemon'
        # Default file locations
        self.configfile = 'conf'
        self.accountsfile = 'accounts'
        self.welcomefile = 'welcome'
        self.usercommandsfile = 'usercommands'
        self.botsdir = 'bots'

    def setuphub(self):
        '''Commands the hub needs to preform when not reloaded'''
        if 'configfile' in self.kwargs:
            self.kwargs['configfile'] = os.path.abspath(self.kwargs['configfile'])
            self.configfile = self.kwargs['configfile']
        self.rootdir = os.path.abspath(os.path.dirname(sys.argv[0]))
        os.chdir(self.rootdir)
        self.loadconfig()
        self.unixconfig()
        self.setuplogging()
        self.loadaccounts()
        self.loadwelcome()
        self.loadusercommands()
        self.loadbots()

    def setuplimits(self, user):
        '''Give user the default limits for the hub'''
        user.limits.clear()
        user.limits.update(self.userlimits)

    def setuplisteningsockets(self):
        '''Setup the listening sockets if it has not already been created'''
        if self.listensocks:
            return
        try:
            self.bindinglocations.insert(0, (self.ip, self.port))
            for ip, port in self.bindinglocations:
                self.createlisteningsocket(ip, port)
        except socket.error:
            errormsg = 'CRITICAL: Error setting up listening socket, exiting'
            if os.name == 'posix' and os.getuid() != 0 and self.port <= 1024:
                errormsg += ' (maybe because the port is set to less than 1024 and you aren\'t running as root)'
            print(errormsg)
            sys.exit(1)
#       self.dropprivileges()

    def setuplogging(self):
        '''Setup logging for hub

        Creates the stdout logger if debugging, the file logger if logging to a
        file, and the syslog handler is logging to syslog. Logging levels for
        specific types of messages can be changed by modifying the appropriate
        item in the loglevels dictionary.
        '''
        self.log = logging.getLogger('dchub.%s.default' % self.id)
        try:
            self.log.setLevel(logging.__dict__[self.loglevel])
        except KeyError:
            try:
                self.loglevel = int(self.loglevel)
                if 1 <= self.loglevel <= 50:
                    self.log.setLevel(self.loglevel)
            except ValueError:
                self.log.setLevel(logging.DEBUG)
        self.defaultlogformatter = logging.Formatter('%(levelname)s: %(message)s')
        self.defaultlogfileformatter = logging.Formatter('%(asctime)s %(levelname)s: %(message)s')
        self.defaultsyslogformatter = logging.Formatter('%(module)s[%(process)d]: %(levelname)s: %(message)s')
        if self.debug or os.name == 'nt':
            self.defaultloghandler = logging.StreamHandler(sys.stdout)
            self.defaultloghandler.setFormatter(self.defaultlogformatter)
            self.log.addHandler(self.defaultloghandler)
        if self.logfile != '':
            try:
                self.defaultlogfilehandler = logging.FileHandler(self.logfile)
                self.defaultlogfilehandler.setFormatter(self.defaultlogfileformatter)
                self.log.addHandler(self.defaultlogfilehandler)
                if os.name == 'posix' and self.changeuidgid:
                    os.chown(self.logfile, self.uid, self.gid)
            except:
                message = 'ERROR: Setting up logging to file failed: '
                if self.debug:
                    self.log.exception(message)
                else:
                    print(message, sys.exc_info()[1])
        if self.usesyslog:
            try:
                address = (self.sysloghost,514)
                if self.sysloghost.count('/'):
                    if os.name != 'posix':
                        raise ValueError('Can only log to Unix domain socket under Unix')
                    address = self.sysloghost
                self.defaultsysloghandler = SysLogHandler(address ,getattr(SysLogHandler,'LOG_%s' % self.syslogfacility.upper()))
                self.defaultsysloghandler.setFormatter(self.defaultsyslogformatter)
                self.log.addHandler(self.defaultsysloghandler)
            except:
                message = 'ERROR: Setting up logging to syslog failed: '
                if self.debug or self.logfile:
                    self.log.exception(message)
                else:
                    print(message, sys.exc_info()[1])

    def setupsignals(self):
        '''Do an orderly shutdown upon receiving a signal.

        SIGABRT, SIGBREAK, SIGILL, SIGINT, SIGQUIT, SIGTERM, SIGUSR1, and
        SIGUSR2 all cause an orderly shutdown. SIGHUP will reload the hub (not
        reread it's config file as other daemons commonly do, due to the fact
        that handling changes to the various configuration options is often not
        possible). Other signals aren't caught and will cause the program to
        terminate immediately.
        '''
        if os.name == 'posix':
            signal.signal(signal.SIGHUP, self.sighuphandler)
        for sig in 'SIGABRT SIGBREAK SIGILL SIGINT SIGQUIT SIGTERM SIGUSR1 SIGUSR2'.split():
            try: signal.signal(getattr(signal, sig), self.sighandler)
            except: pass

    def sighandler(self, signum, frame):
        '''Set the flag to stop the server normally'''
        if not self.stop and hasattr(self, 'log'):
            self.log.log(self.loglevels['hubstatus'], 'Stopping due to signal %s' % signum)
        self.stop = True

    def sighuphandler(self, signum, frame):
        '''Reload the hub on receiving a SIGHUP'''
        if hasattr(self, 'log'):
            self.log.log(self.loglevels['hubstatus'], 'Reloading due to signal %s' % signum)
        self.reload()

    def stringoverlaps(self, string1, string2):
        '''Check if any character in either string is in the other string

        Used for testing if strings contain illegal characters.  Checks the
        sizes of the strings to make sure the loop is as short as possible.
        '''
        if len(string1) > len(string2):
            string1, string2 = string2, string1
        for char in string1:
            if char in string2:
                return True
        return False

    def unixconfig(self):
        '''Handle forking, creating pid, getting the uid/gid, and chrooting'''
        if os.name != 'posix':
            return
        if os.getuid() == 0:
            # Can only chroot and change user and group ids if root
            if self.changeuidgid:
                try:
                    self.uid, self.gid = self.getuidgid()
                except KeyError:
                    print("CRITICAL: Username or groupname is invalid, can\'t drop privileges, exiting")
                    #sys.exit(1)
            if self.chroot:
                os.chroot(self.rootdir)
                self.rootdir = '/'
                sys.path = ['/']
        else:
            self.uid = os.getuid()
            self.gid = os.getgid()
        if not self.debug:
            pid = os.getpid()
            os.fork()
            if os.getpid() == pid:
                sys.exit(0)
        if self.pidfile and (not os.path.exists(self.pidfile) \
          or os.path.isfile(self.pidfile)):
            try:
                fil = file(self.pidfile,'wb')
                try:
                    fil.write('%s' % os.getpid())
                finally:
                    fil.close()
                if self.changeuidgid and os.getuid() == 0:
                    os.chown(self.pidfile, self.uid, self.gid)
            except:
                print("WARNING: Unable to write to the pidfile")

    def unloadbots(self):
        '''Remove all bots and unwrap related functions'''
        for bot in self.bots.values():
            self.removeuser(bot)
        self.unwrapfunctions()

    def unwrapfunctions(self):
        '''Restore default hub functions'''
        for functionname, function in self.wrappedfunctions.items():
            self.log.log(self.loglevels['wrapping'], 'Unwrapping %s' % functionname)
            setattr(self, functionname, function)
        for functionname, function in self.replacedfunctions.items():
            self.log.log(self.loglevels['wrapping'], 'Restoring %s' % functionname)
            setattr(self, functionname, function)
        self.execbefore.clear()
        self.execafter.clear()
        self.wrappedfunctions.clear()
        self.replacedfunctions.clear()

    def wrapfunction(self, functionname, function, execbefore):
        '''Set new function to execute before/after hub function

        If function has not already been wrapped, wrap the function with a
        decorator.  Add the old function to the list of wrapped functions.
        '''
        if functionname not in self.wrappedfunctions:
            oldfunction = getattr(self, functionname)
            self.wrappedfunctions[functionname] = oldfunction
            setattr(self, functionname, self._execwrapper(oldfunction))
        place = self.execafter
        if execbefore:
            place = self.execbefore
        if functionname not in place:
            place[functionname] = []
        place[functionname].append(function)

    def writefile(self, type):
        '''Write file of specified type to disk

        Writes to new file and then does a copy and delete, so a crash during
        the function shouldn't destroy data.  Also note that this takes the
        values from the IntelConfigParser (if applicable) and not the hub's
        variables.

        To write the accounts file, call the function with 'accounts'
        To write the usercommands file, call the function with 'usercommands'
        Writing the config file or the welcome file is an exercise left to the
        user
        '''
        try:
            filename = getattr(self, '%sfile' % type)
            assert os.path.isfile(filename)
            try:
                icp = getattr(self, '%sparser' % type)
                oldfil = file(filename, 'rb')
                try:
                    fil = file('%s.new' % filename, 'wb')
                    try:
                        fil.write(icp.get_config(oldfil))
                    finally:
                        fil.close()
                finally: oldfil.close()
            except AttributeError:
                text = getattr(self, type)
                fil = file('%s.new' % filename, 'wb')
                try:
                    fil.write(text)
                finally:
                    fil.close()
            os.rename(filename, '%s.old' % filename)
            os.rename('%s.new' % filename, filename)
            os.remove('%s.old' % filename)
        except:
            return self.debugexception('Error writing %s file to disk' % type, self.loglevels['loadfileerror'])
        self.log.log(self.loglevels['loading'], 'Wrote %s file to disk' % type)

