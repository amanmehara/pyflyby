# pyflyby/_cmdline.py.
# Copyright (C) 2011, 2012, 2013, 2014 Karl Chen.
# License: MIT http://opensource.org/licenses/MIT

from __future__ import absolute_import, division, with_statement

import optparse
import os
import signal
import sys
from   textwrap                 import dedent
import traceback

from   pyflyby._file            import (FileText, Filename, atomic_write_file,
                                        expand_py_files_from_args, read_file)
from   pyflyby._importstmt      import ImportFormatParams
from   pyflyby._log             import logger
from   pyflyby._util            import cached_attribute


DEBUG = False


def hfmt(s):
    return dedent(s).strip()

def maindoc():
    import __main__
    return (__main__.__doc__ or '').strip()


def _sigpipe_handler(*args):
    # The parent process piped our stdout and closed the pipe before we
    # finished writing, e.g. "tidy-imports ... | head" or "tidy-imports ... |
    # less".  Exit quietly - squelch the "close failed in file object
    # destructor" message would otherwise be raised.
    raise SystemExit(1)


def parse_args(addopts=None, import_format_params=False, modify_action_params=False):
    """
    Do setup for a top-level script and parse arguments.
    """
    ### Setup.
    # Register a SIGPIPE handler.
    signal.signal(signal.SIGPIPE, _sigpipe_handler)
    ### Parse args.
    parser = optparse.OptionParser(usage='\n'+maindoc())

    def log_level_callbacker(level):
        def callback(option, opt_str, value, parser):
            logger.set_level(level)
        return callback

    def debug_callback(option, opt_str, value, parser):
        global DEBUG
        DEBUG = True
        logger.set_level("DEBUG")

    parser.add_option("--debug", action="callback",
                      callback=debug_callback,
                      help="Debug mode (noisy and fail fast).")

    parser.add_option("--verbose", action="callback",
                      callback=log_level_callbacker("DEBUG"),
                      help="Be noisy.")

    parser.add_option("--quiet", action="callback",
                      callback=log_level_callbacker("ERROR"),
                      help="Be quiet.")

    if modify_action_params:
        group = optparse.OptionGroup(parser, "Action options")
        action_diff = action_external_command('pyflyby-diff')
        def parse_action(v):
            V = v.strip().upper()
            if V == 'PRINT':
                return action_print
            elif V == 'REPLACE':
                return action_replace
            elif V == 'QUERY':
                return action_query()
            elif V == "DIFF":
                return action_diff
            elif V.startswith("QUERY:"):
                return action_query(v[6:])
            elif V.startswith("EXECUTE:"):
                return action_external_command(v[8:])
            elif V == "IFCHANGED":
                return action_ifchanged
            else:
                raise Exception(
                    "Bad argument %r to --action; "
                    "expected PRINT or REPLACE or QUERY or IFCHANGED "
                    "or EXECUTE:..." % (v,))

        def set_actions(actions):
            actions = tuple(actions)
            parser.values.actions = actions

        def action_callback(option, opt_str, value, parser):
            action_args = value.split(',')
            set_actions([parse_action(v) for v in action_args])
        def action_callbacker(actions):
            def callback(option, opt_str, value, parser):
                set_actions(actions)
            return callback

        group.add_option(
            "--actions", type='string', action='callback',
            callback=action_callback,
            metavar='PRINT|REPLACE|IFCHANGED|QUERY|DIFF|EXECUTE:mycommand',
            help=hfmt('''
                   Comma-separated list of action(s) to take.  If PRINT, print
                   the changed file to stdout.  If REPLACE, then modify the
                   file in-place.  If EXECUTE:mycommand, then execute
                   'mycommand oldfile tmpfile'.  If DIFF, then execute
                   'pyflyby-diff'.  If QUERY, then query user to continue.
                   If IFCHANGED, then continue actions only if file was
                   changed.'''))
        group.add_option(
            "--print", "-p", action='callback',
            callback=action_callbacker([action_print]),
            help=hfmt('''
                Equivalent to --action=PRINT (default when stdin or stdout is
                not a tty) '''))
        group.add_option(
            "--diff", "-d", action='callback',
            callback=action_callbacker([action_diff]),
            help=hfmt('''Equivalent to --action=DIFF'''))
        group.add_option(
            "--replace", "-r", action='callback',
            callback=action_callbacker([action_ifchanged, action_replace]),
            help=hfmt('''Equivalent to --action=IFCHANGED,REPLACE'''))
        group.add_option(
            "--diff-replace", "-R", action='callback',
            callback=action_callbacker([action_ifchanged, action_diff, action_replace]),
            help=hfmt('''Equivalent to --action=IFCHANGED,DIFF,REPLACE'''))
        actions_interactive = [
            action_ifchanged, action_diff,
            action_query("Replace {filename}?"), action_replace]
        group.add_option(
            "--interactive", "-i", action='callback',
            callback=action_callbacker(actions_interactive),
            help=hfmt('''
               Equivalent to --action=IFCHANGED,DIFF,QUERY,REPLACE (default
               when stdin & stdout are ttys) '''))
        if os.isatty(0) and os.isatty(1):
            default_actions = actions_interactive
        else:
            default_actions = [action_print]
        parser.set_default('actions', tuple(default_actions))
        parser.add_option_group(group)

    if import_format_params:
        group = optparse.OptionGroup(parser, "Pretty-printing options")
        group.add_option('--align-imports', '--align', type='str', default="32",
                         metavar='N',
                         help=hfmt('''
                             Whether and how to align the 'import' keyword in
                             'from modulename import aliases...'.  If 0, then
                             don't align.  If 1, then align within each block
                             of imports.  If an integer > 1, then align at
                             that column, wrapping with a backslash if
                             necessary.  If a comma-separated list of integers
                             (tab stops), then pick the column that results in
                             the fewest number of lines total per block.'''))
        group.add_option('--from-spaces', type='int', default=3, metavar='N',
                         help=hfmt('''
                             The number of spaces after the 'from' keyword.
                             (Must be at least 1; default is 3.)'''))
        group.add_option('--separate-from-imports', action='store_true',
                         default=False,
                         help=hfmt('''
                             Separate 'from ... import ...'
                             statements from 'import ...' statements.'''))
        group.add_option('--no-separate-from-imports', action='store_false',
                         dest='separate_from_imports',
                         help=hfmt('''
                            (Default) Don't separate 'from ... import ...'
                            statements from 'import ...' statements.'''))
        group.add_option('--align-future', action='store_true',
                         default=False,
                         help=hfmt('''
                             Align the 'from __future__ import ...' statement
                             like others.'''))
        group.add_option('--no-align-future', action='store_false',
                         dest='align_future',
                         help=hfmt('''
                             (Default) Don't align the 'from __future__ import
                             ...' statement.'''))
        group.add_option('--width', type='int', default=79, metavar='N',
                         help=hfmt('''
                             Maximum line length (default: 79).'''))
        def uniform_callback(option, opt_str, value, parser):
            parser.values.separate_from_imports = False
            parser.values.from_spaces           = 3
            parser.values.align_imports         = '32'
        group.add_option('--uniform', '-u', action="callback",
                         callback=uniform_callback,
                         help=hfmt('''
                             (Default) Shortcut for --no-separate-from-imports
                             --from-spaces=3 --align-imports=32.'''))
        def unaligned_callback(option, opt_str, value, parser):
            parser.values.separate_from_imports = True
            parser.values.from_spaces           = 1
            parser.values.align_imports         = '0'
        group.add_option('--unaligned', '-n', action="callback",
                         callback=unaligned_callback,
                         help=hfmt('''
                             Shortcut for --separate-from-imports
                             --from-spaces=1 --align-imports=0.'''))

        parser.add_option_group(group)
    if addopts is not None:
        addopts(parser)
    options, args = parser.parse_args()
    if import_format_params:
        align_imports_args = [int(x.strip())
                              for x in options.align_imports.split(",")]
        if len(align_imports_args) == 1 and align_imports_args[0] == 1:
            align_imports = True
        elif len(align_imports_args) == 1 and align_imports_args[0] == 0:
            align_imports = False
        else:
            align_imports = tuple(sorted(set(align_imports_args)))
        options.params = ImportFormatParams(
            align_imports         =align_imports,
            from_spaces           =options.from_spaces,
            separate_from_imports =options.separate_from_imports,
            max_line_length       =options.width,
            align_future          =options.align_future
            )
    return options, args


def _default_on_error(filename):
    raise SystemExit("bad filename %s" % (filename,))

def filename_args(args, on_error=_default_on_error):
    """
    Return list of filenames given command-line arguments.

    @rtype:
      C{list} of L{Filename}
    """
    if args:
        return expand_py_files_from_args(args, on_error)
    elif not os.isatty(0):
        return [Filename.STDIN]
    else:
        syntax()

def syntax():
    print >>sys.stderr, maindoc() + '\n\nFor usage, see: %s --help' % (sys.argv[0],)
    raise SystemExit(1)


class AbortActions(Exception):
    pass


class Modifier(object):
    def __init__(self, modifier, filename):
        self.modifier = modifier
        self.filename = filename
        self._tmpfiles = []

    @cached_attribute
    def input_content(self):
        return read_file(self.filename)

    # TODO: refactor to avoid having these heavy-weight things inside a
    # cached_attribute, which causes annoyance while debugging.
    @cached_attribute
    def output_content(self):
        return FileText(self.modifier(self.input_content), filename=self.filename)

    def _tempfile(self):
        from tempfile import NamedTemporaryFile
        f = NamedTemporaryFile()
        self._tmpfiles.append(f)
        return f, Filename(f.name)


    @cached_attribute
    def output_content_filename(self):
        f, fname = self._tempfile()
        f.write(self.output_content.joined)
        f.flush()
        return fname

    @cached_attribute
    def input_content_filename(self):
        if isinstance(self.filename, Filename):
            return self.filename
        # If the input was stdin, and the user wants a diff, then we need to
        # write it to a temp file.
        f, fname = self._tempfile()
        f.write(self.input_content)
        f.flush()
        return fname


    def __del__(self):
        for f in self._tmpfiles:
            f.close()


def process_actions(filenames, actions, modify_function):
    errors = []
    def on_error_filename_arg(arg):
        print >>sys.stderr, "%s: bad filename %s" % (sys.argv[0], arg)
        errors.append("%s: bad filename" % (arg,))
    filenames = filename_args(filenames, on_error=on_error_filename_arg)
    for filename in filenames:
        try:
            m = Modifier(modify_function, filename)
            for action in actions:
                action(m)
        except AbortActions:
            continue
        except Exception as e:
            errors.append("%s: %s: %s" % (filename, type(e).__name__, e))
            if str(filename) not in str(e):
                e = type(e)("While processing %s: %s" % (filename, e))
            if DEBUG:
                raise e, None, sys.exc_info()[2]
            traceback.print_exception(*sys.exc_info())
            continue
    if errors:
        msg = "\n%s: encountered the following problems:\n" % (sys.argv[0],)
        for e in errors:
            lines = e.splitlines()
            msg += "    " + lines[0] + '\n'.join(
                ("            %s"%line for line in lines[1:]))
        raise SystemExit(msg)


def action_print(m):
    output_content = m.output_content
    sys.stdout.write(output_content.joined)


def action_ifchanged(m):
    if m.output_content.joined == m.input_content.joined:
        logger.debug("unmodified: %s", m.filename)
        raise AbortActions


def action_replace(m):
    if m.filename == Filename.STDIN:
        raise Exception("Can't replace stdio in-place")
    logger.info("%s: *** modified ***", m.filename)
    atomic_write_file(m.filename, m.output_content)


def action_external_command(command):
    import subprocess
    def action(m):
        bindir = os.path.dirname(os.path.realpath(sys.argv[0]))
        env = os.environ
        env['PATH'] = env['PATH'] + ":" + bindir
        fullcmd = "%s %s %s" % (
            command, m.input_content_filename, m.output_content_filename)
        logger.debug("Executing external command: %s", fullcmd)
        ret = subprocess.call(fullcmd, shell=True, env=env)
        logger.debug("External command returned %d", ret)
    return action


def action_query(prompt="Proceed?"):
    def action(m):
        p = prompt.format(filename=m.filename)
        print
        print "%s [y/N] " % (p),
        try:
            if raw_input().strip().lower().startswith('y'):
                return True
        except KeyboardInterrupt:
            print >>sys.stderr, "KeyboardInterrupt"
            raise SystemExit(1)
        print "Aborted"
        raise AbortActions
    return action