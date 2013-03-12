"""Implement the command line 'lnt' tool."""

import logging
import os
import sys
import tempfile
from optparse import OptionParser, OptionGroup

import werkzeug.contrib.profiler

import StringIO
import lnt
import lnt.util.multitool
import lnt.util.ImportData
from lnt import testing
from lnt.testing.util.commands import note, warning, error, fatal

def action_runserver(name, args):
    """start a new development server"""

    parser = OptionParser("""\
%s [options] <instance path>

Start the LNT server using a development WSGI server. Additional options can be
used to control the server host and port, as well as useful development features
such as automatic reloading.

The command has built-in support for running the server on an instance which has
been packed into a (compressed) tarball. The tarball will be automatically
unpacked into a temporary directory and removed on exit. This is useful for
passing database instances back and forth, when others only need to be able to
view the results.\
""" % name)
    parser.add_option("", "--hostname", dest="hostname", type=str,
                      help="host interface to use [%default]",
                      default='localhost')
    parser.add_option("", "--port", dest="port", type=int, metavar="N",
                      help="local port to use [%default]", default=8000)
    parser.add_option("", "--reloader", dest="reloader", default=False,
                      action="store_true", help="use WSGI reload monitor")
    parser.add_option("", "--debugger", dest="debugger", default=False,
                      action="store_true", help="use WSGI debugger")
    parser.add_option("", "--profiler", dest="profiler", default=False,
                      action="store_true", help="enable WSGI profiler")
    parser.add_option("", "--show-sql", dest="show_sql", default=False,
                      action="store_true", help="show all SQL queries")
    parser.add_option("", "--threaded", dest="threaded", default=False,
                      action="store_true", help="use a threaded server")
    parser.add_option("", "--processes", dest="processes", type=int,
                      metavar="N", help="number of processes to use [%default]",
                      default=1)

    (opts, args) = parser.parse_args(args)
    if len(args) != 1:
        parser.error("invalid number of arguments")

    input_path, = args

    # Setup the base LNT logger.
    logger = logging.getLogger("lnt")
    if opts.debugger:
        logger.setLevel(logging.DEBUG)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(
            '%(asctime)s %(levelname)s: %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'))
    logger.addHandler(handler)

    # Enable full SQL logging, if requested.
    if opts.show_sql:
        sa_logger = logging.getLogger("sqlalchemy")
        if opts.debugger:
            sa_logger.setLevel(logging.DEBUG)
        sa_logger.setLevel(logging.DEBUG)
        sa_logger.addHandler(handler)

    import lnt.server.ui.app
    app = lnt.server.ui.app.App.create_standalone(input_path,)
    if opts.debugger:
        app.debug = True
    if opts.profiler:
        app.wsgi_app = werkzeug.contrib.profiler.ProfilerMiddleware(
            app.wsgi_app, stream = open('profiler.log', 'w'))
    app.run(opts.hostname, opts.port,
            use_reloader = opts.reloader,
            use_debugger = opts.debugger,
            threaded = opts.threaded,
            processes = opts.processes)

from create import action_create
from convert import action_convert
from import_data import action_import
from updatedb import action_updatedb

def action_checkformat(name, args):
    """check the format of an LNT test report file"""

    parser = OptionParser("%s [options] files" % name)

    (opts, args) = parser.parse_args(args)
    if len(args) > 1:
        parser.error("incorrect number of argments")

    if len(args) == 0:
        input = '-'
    else:
        input, = args

    if input == '-':
        input = StringIO.StringIO(sys.stdin.read())
    
    import lnt.server.db.v4db
    import lnt.server.config
    db = lnt.server.db.v4db.V4DB('sqlite:///:memory:',
                                 lnt.server.config.Config.dummyInstance())
    result = lnt.util.ImportData.import_and_report(
        None, None, db, input, 'json', commit = True)
    lnt.util.ImportData.print_report_result(result, sys.stdout, sys.stderr,
                                            verbose = True)

def action_runtest(name, args):
    """run a builtin test application"""

    parser = OptionParser("%s test-name [options]" % name)
    parser.disable_interspersed_args()
    parser.add_option("", "--submit", dest="submit_url", metavar="URLORPATH",
                      help=("autosubmit the test result to the given server "
                            "(or local instance) [%default]"),
                      type=str, default=None)
    parser.add_option("", "--commit", dest="commit",
                      help=("whether the autosubmit result should be committed "
                            "[%default]"),
                      type=int, default=True)
    parser.add_option("", "--output", dest="output", metavar="PATH",
                      help="write raw report data to PATH (or stdout if '-')",
                      action="store", default=None)
    parser.add_option("-v", "--verbose", dest="verbose",
                      help="show verbose test results",
                      action="store_true", default=False)

    (opts, args) = parser.parse_args(args)
    if len(args) < 1:
        parser.error("incorrect number of argments")

    test_name,args = args[0],args[1:]

    import lnt.tests
    try:
        test_instance = lnt.tests.get_test_instance(test_name)
    except KeyError:
        parser.error('invalid test name %r' % test_name)

    report = test_instance.run_test('%s %s' % (name, test_name), args)

    if opts.output is not None:
        if opts.output == '-':
            output_stream = sys.stdout
        else:
            output_stream = open(opts.output, 'w')
        print >>output_stream, report.render()
        if output_stream is not sys.stdout:
            output_stream.close()

    # Save the report to a temporary file.
    #
    # FIXME: This is silly, the underlying test probably wrote the report to a
    # file itself. We need to clean this up and make it standard across all
    # tests. That also has the nice side effect that writing into a local
    # database records the correct imported_from path.
    tmp = tempfile.NamedTemporaryFile(suffix='.json')
    print >>tmp, report.render()
    tmp.flush()

    if opts.submit_url is not None:
        if report is None:
            raise SystemExit,"error: report generation failed"

        from lnt.util import ServerUtil
        test_instance.log("submitting result to %r" % (opts.submit_url,))
        ServerUtil.submitFile(opts.submit_url, tmp.name, True, opts.verbose)
    else:
        # Simulate a submission to retrieve the results report.

        # Construct a temporary database and import the result.
        test_instance.log("submitting result to dummy instance")
        
        import lnt.server.db.v4db
        import lnt.server.config
        db = lnt.server.db.v4db.V4DB("sqlite:///:memory:",
                                     lnt.server.config.Config.dummyInstance())
        result = lnt.util.ImportData.import_and_report(
            None, None, db, tmp.name, 'json', commit = True)
        lnt.util.ImportData.print_report_result(result, sys.stdout, sys.stderr,
                                                opts.verbose)

    tmp.close()

def action_showtests(name, args):
    """show the available built-in tests"""

    parser = OptionParser("%s" % name)
    (opts, args) = parser.parse_args(args)
    if len(args) != 0:
        parser.error("incorrect number of argments")

    import lnt.tests

    print 'Available tests:'
    test_names = lnt.tests.get_test_names()
    max_name = max(map(len, test_names))
    for name in test_names:
        print '  %-*s - %s' % (max_name, name,
                               lnt.tests.get_test_description(name))

def action_submit(name, args):
    """submit a test report to the server"""

    parser = OptionParser("%s [options] <url> <file>+" % name)
    parser.add_option("", "--commit", dest="commit", type=int,
                      help=("whether the result should be committed "
                            "[%default]"),
                      default=False)
    parser.add_option("-v", "--verbose", dest="verbose",
                      help="show verbose test results",
                      action="store_true", default=False)

    (opts, args) = parser.parse_args(args)
    if len(args) < 2:
        parser.error("incorrect number of argments")

    from lnt.util import ServerUtil
    ServerUtil.submitFiles(args[0], args[1:], opts.commit, opts.verbose)

def action_update(name, args):
    """create and or auto-update the given database"""

    parser = OptionParser("%s [options] <db path>" % name)
    parser.add_option("", "--show-sql", dest="show_sql", default=False,
                      action="store_true", help="show all SQL queries")

    (opts, args) = parser.parse_args(args)
    if len(args) != 1:
        parser.error("incorrect number of argments")

    db_path, = args

    # Setup the base LNT logger.
    logger = logging.getLogger("lnt")
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(
            '%(asctime)s %(levelname)s: %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'))
    logger.addHandler(handler)

    # Enable full SQL logging, if requested.
    if opts.show_sql:
        sa_logger = logging.getLogger("sqlalchemy")
        sa_logger.setLevel(logging.INFO)
        sa_logger.addHandler(handler)

    # Update the database.
    lnt.server.db.migrate.update_path(db_path)

def action_send_daily_report(name, args):
    """send a daily report email"""
    import datetime
    import email.mime.multipart
    import email.mime.text
    import smtplib

    import lnt.server.reporting.dailyreport

    parser = OptionParser("%%prog %s [options] <instance path> <address>" % (
            name,))
    parser.add_option("", "--database", dest="database", default="default",
                      help="database to use [%default]")
    parser.add_option("", "--testsuite", dest="testsuite", default="nts",
                      help="testsuite to use [%default]")
    parser.add_option("", "--host", dest="host", default="localhost",
                      help="email relay host to use [%default]")
    parser.add_option("", "--from", dest="from_address", default=None,
                      help="from email address (required)")
    parser.add_option("", "--today", dest="today", action="store_true",
                      help="send the report for today (instead of most recent)")
    parser.add_option("", "--subject-prefix", dest="subject_prefix",
                      help="add a subject prefix")
    (opts, args) = parser.parse_args(args)

    if len(args) != 2:
        parser.error("invalid number of arguments")
    if opts.from_address is None:
        parser.error("--from argument is required")

    path, to_address = args

    # Load the LNT instance.
    instance = lnt.server.instance.Instance.frompath(path)
    config = instance.config

    # Get the database.
    db = config.get_database(opts.database)

    # Get the testsuite.
    ts = db.testsuite[opts.testsuite]

    if opts.today:
        date = datetime.datetime.now()
    else:
        # Get a timestamp to use to derive the daily report to generate.
        latest = ts.query(ts.Run).\
            order_by(ts.Run.start_time.desc()).limit(1).first()

        # If we found a run, use it's start time (rounded up to the next hour,
        # so we make sure it gets included).
        if latest:
            date = latest.start_time + datetime.timedelta(hours=1)
        else:
            # Otherwise, just use now.
            date = datetime.datetime.now()

    # Generate the daily report.
    note("building report data...")
    report = lnt.server.reporting.dailyreport.DailyReport(
        ts, year=date.year, month=date.month, day=date.day,
        day_start_offset_hours=date.hour, for_mail=True)
    report.build()

    note("generating HTML report...")
    ts_url = "%s/db_%s/v4/%s" % (config.zorgURL, opts.database, opts.testsuite)
    subject = "Daily Report: %04d-%02d-%02d" % (
        report.year, report.month, report.day)
    html_report = report.render(ts_url, only_html_body=False)

    if opts.subject_prefix is not None:
        subject = "%s %s" % (opts.subject_prefix, subject)

    # Form the multipart email message.
    msg = email.mime.multipart.MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = opts.from_address
    msg['To'] = to_address
    msg.attach(email.mime.text.MIMEText(html_report, "html"))

    # Send the report.
    s = smtplib.SMTP(opts.host)
    s.sendmail(opts.from_address, [to_address],
               msg.as_string())
    s.quit()

###

tool = lnt.util.multitool.MultiTool(locals())
main = tool.main

if __name__ == '__main__':
    main()
